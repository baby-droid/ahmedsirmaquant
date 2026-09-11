"""Invent: the structure of the formula itself.

Two calls, the same shape as the other labs. The preview costs nothing and shows the
expressions that would run, and where each one came from — crossed, changed, or built
from nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..harvest.patterns import WINDOWS
from ..labs.invent import GROUP_OPS, GROUPINGS, MAX_SIMULATIONS, TS_OPS
from .deps import ScopedRequest, State, plan_summary

router = APIRouter(prefix="/api/invent", tags=["invent"])


@router.get("/grammar")
async def grammar() -> dict[str, Any]:
    """The pieces an invented formula is built out of."""
    return {
        "overTime": list(TS_OPS),
        "acrossStocks": ["rank", "zscore", "-"],
        "grouped": list(GROUP_OPS),
        "groupings": list(GROUPINGS),
        "windows": list(WINDOWS),
        "note": (
            "Every formula is built as a tree rather than written out, so a grouping can "
            "only ever land where a grouping belongs. Each run breeds from the alphas "
            "that already worked here."
        ),
    }


class InventRequest(ScopedRequest):
    target: int = Field(default=500, ge=1, le=MAX_SIMULATIONS)
    neutralization: str = "SUBINDUSTRY"
    decay: int = Field(default=4, ge=0)
    truncation: float = Field(default=0.08, gt=0)
    per_dataset: int = Field(default=2, ge=1, le=20)
    seed: int | None = Field(
        default=None, description="Fix the draw. Left unset, it varies by day and market."
    )


async def _plan(body: InventRequest, state: State) -> dict[str, Any]:
    return await state.inventor.plan(
        scope=body.scope(),
        target=body.target,
        neutralization=body.neutralization,
        decay=body.decay,
        truncation=body.truncation,
        per_dataset=body.per_dataset,
        seed=body.seed,
    )


@router.post("/preview")
async def preview(body: InventRequest, state: State) -> dict[str, Any]:
    """What it would invent, and what it bred each one from. Costs nothing."""
    return plan_summary(await _plan(body, state))


class InventRun(InventRequest):
    task: str = Field(default="invent", description="Slot-quota group")


@router.post("/run")
async def run(body: InventRun, state: State) -> dict[str, Any]:
    """Queue the generation. The only call here that spends allowance."""
    plan = await _plan(body, state)
    if not plan["requests"]:
        return {**plan_summary(plan), "queued": [], "skipped": []}

    outcome = await state.engine.enqueue(plan["requests"], task=body.task)
    return {**plan_summary(plan), **outcome, "task": body.task}
