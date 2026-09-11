"""Harden: find out whether a good result is real before submitting it.

Preview costs nothing and says what each probe would prove. Only ``run`` spends allowance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..labs.harden import MAX_SIMULATIONS
from .deps import ScopedRequest, State, plan_summary

router = APIRouter(prefix="/api/harden", tags=["harden"])


class HardenRequest(ScopedRequest):
    min_sharpe: float = Field(default=1.25, ge=0.0)
    limit: int = Field(default=10, ge=1, le=100, description="How many results to check")
    target: int = Field(default=300, ge=1, le=MAX_SIMULATIONS)


async def _plan(body: HardenRequest, state: State) -> dict[str, Any]:
    return await state.hardener.plan(
        scope=body.scope(), min_sharpe=body.min_sharpe, limit=body.limit, target=body.target
    )


@router.post("/preview")
async def preview(body: HardenRequest, state: State) -> dict[str, Any]:
    """What would be checked, and what each check would prove. Costs nothing."""
    return plan_summary(await _plan(body, state))


class HardenRun(HardenRequest):
    task: str = Field(default="harden", description="Slot-quota group")


@router.post("/run")
async def run(body: HardenRun, state: State) -> dict[str, Any]:
    """Queue the checks. The only call here that spends allowance."""
    plan = await _plan(body, state)
    if not plan["requests"]:
        return {**plan_summary(plan), "queued": [], "skipped": []}
    outcome = await state.engine.enqueue(plan["requests"], task=body.task)
    return {**plan_summary(plan), **outcome, "task": body.task}
