"""The day's plan.

Four calls, in the order someone actually uses them: see what the plan would be, roll a
different one if you do not like it, start it, watch it. Only ``start`` spends anything.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..plan.day import ASSUMED_DAILY, MAX_TRACKS, DayPlanner, lever_catalogue
from .deps import ScopedRequest, State

router = APIRouter(prefix="/api/plan", tags=["plan"])


@router.get("/levers")
async def levers() -> dict[str, Any]:
    """What varies between one plan and the next."""
    return {
        **lever_catalogue(),
        "maxTracks": MAX_TRACKS,
        "assumedDaily": ASSUMED_DAILY,
        "note": (
            "Every combination is a real piece of research, so there is no wrong choice "
            "here. They exist so that two people running the same plan on the same day "
            "do not produce the same alphas."
        ),
    }


class SuggestRequest(BaseModel):
    tracks: int = Field(default=3, ge=1, le=MAX_TRACKS)
    target: int = Field(default=ASSUMED_DAILY, ge=1, le=50_000)
    neutralization: str = "SUBINDUSTRY"
    seed: int | None = Field(
        default=None,
        description="Fixes the draw, so the same plan can be shown twice. Normally unset.",
    )


@router.post("/suggest")
async def suggest(body: SuggestRequest, state: State) -> dict[str, Any]:
    """Draw a plan. Costs nothing, writes nothing — press it as often as you like."""
    proposals = state.planner.suggest(
        tracks=body.tracks,
        target=body.target,
        neutralization=body.neutralization,
        seed=body.seed,
    )
    return {"tracks": proposals, "slots": state.engine.slots, "target": body.target}


@router.get("/yield")
async def yields(state: State, days: int = 14) -> dict[str, Any]:
    """The fund's books: what each desk turned into submittable alphas per simulation."""
    return await state.yields.summary(days=days)


@router.get("/pm")
async def pm_brief(state: State, days: int = 14) -> dict[str, Any]:
    """Everything the PM is shown before it decides. Costs nothing."""
    return await state.pm.brief(days=days)


class LuckyRequest(ScopedRequest):
    target: int = Field(default=ASSUMED_DAILY, ge=1, le=50_000)
    model: str | None = None
    days: int = Field(default=14, ge=1, le=90, description="How far back the PM looks")
    dry_run: bool = Field(
        default=True,
        description="True previews the allocation without spending anything.",
    )


@router.post("/lucky")
async def lucky(body: LuckyRequest, state: State) -> dict[str, Any]:
    """The one button. The PM reads the books and allocates the day across desks.

    Defaults to a preview, because the honest order is decide, look, then spend. Pass
    ``dryRun: false`` to queue it.
    """
    allocation = await state.pm.allocate(
        cores=state.engine.slots, target=body.target, model=body.model, days=body.days
    )
    funded = allocation["allocations"]
    if not funded or body.dry_run:
        return {**allocation, "started": None}

    started = await state.dispatcher.run(allocations=funded, scope=body.scope(), target=body.target)
    return {**allocation, "started": started, "status": await state.engine.status()}


class AdviseRequest(ScopedRequest):
    tracks: int = Field(default=3, ge=1, le=MAX_TRACKS)
    target: int = Field(default=ASSUMED_DAILY, ge=1, le=50_000)
    note: str = Field(
        default="",
        max_length=2000,
        description="Anything they want out of today, in their own words. Optional.",
    )
    model: str | None = None
    reasoning: str = "normal"
    neutralization: str = "SUBINDUSTRY"


@router.post("/advise")
async def advise(body: AdviseRequest, state: State) -> dict[str, Any]:
    """Ask the assistant what to work on today.

    Falls back to an ordinary drawn plan rather than failing: someone who opened this
    for ten minutes must never end up with nothing to press.
    """
    return await state.advisor.suggest(
        scope=body.scope(),
        tracks=body.tracks,
        target=body.target,
        note=body.note,
        model=body.model,
        reasoning=body.reasoning,
        neutralization=body.neutralization,
    )


class StartRequest(ScopedRequest):
    tracks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Straight back from /suggest. Empty draws a fresh plan.",
    )
    track_count: int = Field(default=3, ge=1, le=MAX_TRACKS)
    target: int = Field(default=ASSUMED_DAILY, ge=1, le=50_000)
    neutralization: str = "SUBINDUSTRY"
    replace: bool = Field(
        default=True, description="Stop whatever is already queued for today first"
    )


@router.post("/start")
async def start(body: StartRequest, state: State) -> dict[str, Any]:
    """Queue the day's work and give each track its share of the slots."""
    planner: DayPlanner = state.planner
    proposals = body.tracks or planner.suggest(
        tracks=body.track_count, target=body.target, neutralization=body.neutralization
    )
    result = await planner.start(body.scope(), proposals, replace=body.replace)
    return {**result, "status": await state.engine.status()}


@router.get("")
async def status(state: State) -> dict[str, Any]:
    """Today's plan and how much of the allowance is still unspent."""
    return {**await state.planner.status(), "engine": await state.engine.status()}


@router.post("/stop")
async def stop_all(state: State) -> dict[str, Any]:
    """Stop everything still waiting. Anything already sent to BRAIN keeps running."""
    dropped = await state.planner.stop_all()
    return {"dropped": dropped, **await state.planner.status()}


@router.post("/stop/{task}")
async def stop_one(task: str, state: State) -> dict[str, Any]:
    dropped = await state.planner.stop(task)
    return {"dropped": dropped, "task": task, **await state.planner.status()}
