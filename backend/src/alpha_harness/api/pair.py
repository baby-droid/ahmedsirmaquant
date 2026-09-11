"""Pair: two data fields at once.

Two calls, the same shape as the other labs. The preview costs nothing and shows the
exact pairs and expressions that would run; only ``run`` spends allowance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..harvest.patterns import WINDOWS
from ..labs.pair import MAX_SIMULATIONS, catalogue
from .deps import ScopedRequest, State, plan_summary

router = APIRouter(prefix="/api/pair", tags=["pair"])


@router.get("/shapes")
async def shapes() -> dict[str, Any]:
    """The ways two fields can be related, each with the idea behind it."""
    return {
        "shapes": catalogue(),
        "windows": list(WINDOWS),
        "note": (
            "Pairs are always drawn from two different datasets. Two fields from the "
            "same feed move together, so pairing them says little that either would not."
        ),
    }


class PairRequest(ScopedRequest):
    target: int = Field(default=500, ge=1, le=MAX_SIMULATIONS)
    shapes: list[str] = Field(
        default_factory=list, description="Empty means every shape that can run here"
    )
    windows: list[int] = Field(default_factory=list)
    group: str = Field(default="subindustry", description="What each stock is compared against")
    neutralization: str = "SUBINDUSTRY"
    decay: int = Field(default=4, ge=0)
    truncation: float = Field(default=0.08, gt=0)
    per_dataset: int = Field(default=1, ge=1, le=20)
    seed: int | None = Field(
        default=None, description="Fix the draw. Left unset, it varies by day and market."
    )


async def _plan(body: PairRequest, state: State) -> dict[str, Any]:
    return await state.pairer.plan(
        scope=body.scope(),
        target=body.target,
        shapes=body.shapes or None,
        windows=body.windows or None,
        group=body.group,
        neutralization=body.neutralization,
        decay=body.decay,
        truncation=body.truncation,
        per_dataset=body.per_dataset,
        seed=body.seed,
    )


@router.post("/preview")
async def preview(body: PairRequest, state: State) -> dict[str, Any]:
    """Which pairs would be tried, and what they would look like. Costs nothing."""
    return plan_summary(await _plan(body, state))


class PairRun(PairRequest):
    task: str = Field(default="pair", description="Slot-quota group")


@router.post("/run")
async def run(body: PairRun, state: State) -> dict[str, Any]:
    """Queue the pairs. The only call here that spends allowance."""
    plan = await _plan(body, state)
    if not plan["requests"]:
        return {**plan_summary(plan), "queued": [], "skipped": []}

    outcome = await state.engine.enqueue(plan["requests"], task=body.task)
    return {**plan_summary(plan), **outcome, "task": body.task}
