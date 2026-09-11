"""Relocate: run a formula that works in another market.

Two calls. The preview costs nothing and shows exactly what would run, including the
moves that are impossible and why. Only ``run`` spends allowance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..labs.relocate import DEFAULT_MIN_SHARPE, MAX_SIMULATIONS
from .deps import ScopedRequest, State, plan_summary

router = APIRouter(prefix="/api/relocate", tags=["relocate"])


class RelocateRequest(ScopedRequest):
    min_sharpe: float = Field(
        default=DEFAULT_MIN_SHARPE,
        ge=0.0,
        description="Carrying a weak signal mostly produces another weak signal.",
    )
    limit: int = Field(default=20, ge=1, le=200, description="How many alphas to carry")
    neutralizations: list[str] = Field(
        default_factory=lambda: ["SUBINDUSTRY"],
        description="Groupings to try. Legality is region-dependent — take these from "
        "the platform's own settings schema rather than guessing.",
    )
    target: int = Field(default=500, ge=1, le=MAX_SIMULATIONS)


async def _plan(body: RelocateRequest, state: State) -> dict[str, Any]:
    return await state.relocator.plan(
        origin=body.scope(),
        min_sharpe=body.min_sharpe,
        limit=body.limit,
        neutralizations=body.neutralizations,
        target=body.target,
    )


@router.post("/preview")
async def preview(body: RelocateRequest, state: State) -> dict[str, Any]:
    """What would travel where. Costs nothing."""
    return plan_summary(await _plan(body, state))


class RelocateRun(RelocateRequest):
    task: str = Field(default="relocate", description="Slot-quota group")


@router.post("/run")
async def run(body: RelocateRun, state: State) -> dict[str, Any]:
    """Queue the moves that are possible. The only call here that spends allowance."""
    plan = await _plan(body, state)
    if not plan["requests"]:
        return {**plan_summary(plan), "queued": [], "skipped": []}

    outcome = await state.engine.enqueue(plan["requests"], task=body.task)
    return {**plan_summary(plan), **outcome, "task": body.task}
