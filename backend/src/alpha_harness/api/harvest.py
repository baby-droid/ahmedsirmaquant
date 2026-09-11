"""From a synced scope to a day's worth of simulations, in a few clicks.

The shape of the interaction this serves: pick a scope, pick a size, look at what it
will run, run it. No expression written, no template authored, no field chosen by hand.

The preview is free and the run is the only step that spends anything, so the count and
a sample of the actual expressions can always be seen before committing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..harvest.patterns import GROUPS, WINDOWS, catalogue
from ..harvest.seeds import DEFAULT_MIN_ALPHAS, DEFAULT_MIN_COVERAGE, MAX_SIMULATIONS
from .deps import ScopedRequest, State

router = APIRouter(prefix="/api/harvest", tags=["harvest"])


@router.get("/patterns")
async def patterns() -> dict[str, Any]:
    """The alpha shapes a harvest uses, each with the idea behind it in plain words."""
    return {
        "patterns": catalogue(),
        "windows": list(WINDOWS),
        "groups": list(GROUPS),
        "maxSimulations": MAX_SIMULATIONS,
        "note": (
            "Everything a harvest generates shares one region, delay, instrument type "
            "and language, which are four of the five fields a batch's children must "
            "agree on — so it packs into full batches from the first slot to the last."
        ),
    }


class HarvestRequest(ScopedRequest):
    target: int = Field(default=1000, ge=1, le=MAX_SIMULATIONS)
    patterns: list[str] = Field(default_factory=list, description="Empty means all of them")
    windows: list[int] = Field(default_factory=list, description="Empty means the conventional set")
    group: str = "industry"

    neutralization: str = "SUBINDUSTRY"
    decay: int = 0
    truncation: float = 0.08

    per_dataset: int = Field(default=1, ge=1, le=10, description="Fields taken from each dataset")
    min_alphas: int = Field(
        default=DEFAULT_MIN_ALPHAS,
        ge=0,
        description="Skip fields nobody has built on — usually there is a reason",
    )
    max_alphas: int | None = Field(
        default=None, description="Cap crowding: set it low to hunt unworked fields"
    )
    min_coverage: float = Field(default=DEFAULT_MIN_COVERAGE, ge=0.0, le=1.0)
    skip: int = Field(
        default=0,
        ge=0,
        le=20,
        description=(
            "Take the next-best fields in each dataset rather than the best. Two runs "
            "with the same options and different skip values touch different data."
        ),
    )
    fields: list[str] = Field(
        default_factory=list,
        description=(
            "Run these exact fields instead of picking automatically. This is how a "
            "conversation becomes a run: the assistant reads a hunch and names the "
            "fields, and they come straight here."
        ),
    )


@router.post("/preview")
async def preview(body: HarvestRequest, state: State) -> dict[str, Any]:
    """What a harvest would run. Costs nothing."""
    result = await _build(body, state)
    return result.summary()


class RunRequest(HarvestRequest):
    # "sweep" is this lab's registry id; yield attribution reads the task prefix.
    task: str = Field(default="sweep", description="Slot-quota group")
    skip_duplicates: bool = True


@router.post("/run")
async def run(body: RunRequest, state: State) -> dict[str, Any]:
    """Queue the harvest. The only call here that spends simulation quota."""
    result = await _build(body, state)
    if not result.requests:
        return {
            **result.summary(),
            "queued": [],
            "skipped": [],
            "message": (
                "None of those fields exist in this market."
                if body.fields
                else (
                    "Nothing matched. Loosen the coverage or alpha-count filters, or "
                    "download this market first."
                )
            ),
        }

    outcome = await state.engine.enqueue(
        result.requests, task=body.task, skip_duplicates=body.skip_duplicates
    )
    return {
        **result.summary(),
        **outcome,
        "task": body.task,
        "status": await state.engine.status(),
    }


@router.post("/fields")
async def fields(body: HarvestRequest, state: State) -> list[dict[str, Any]]:
    """The fields a harvest would pick, so the choice is inspectable rather than magic."""
    return await state.harvester.best_fields(
        body.scope(),
        per_dataset=body.per_dataset,
        min_alphas=body.min_alphas,
        max_alphas=body.max_alphas,
        min_coverage=body.min_coverage,
        limit=400,
        skip=body.skip,
    )


async def _build(body: HarvestRequest, state: State) -> Any:
    return await state.harvester.harvest(
        body.scope(),
        target=body.target,
        patterns=body.patterns or None,
        windows=body.windows or None,
        group=body.group,
        neutralization=body.neutralization,
        decay=body.decay,
        truncation=body.truncation,
        per_dataset=body.per_dataset,
        min_alphas=body.min_alphas,
        max_alphas=body.max_alphas,
        min_coverage=body.min_coverage,
        fields=body.fields or None,
        skip=body.skip,
    )
