"""Repair: the fix for what a near miss failed.

Two calls, the same shape as the other labs. The preview costs nothing and names every
alpha it would repair, the check it failed and the one thing that would change.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..labs.repair import MAX_SIMULATIONS, PRIORITY, SAYS, SEEDS
from .deps import ScopedRequest, State, plan_summary

router = APIRouter(prefix="/api/repair", tags=["repair"])


@router.get("/checks")
async def checks() -> dict[str, Any]:
    """The failures this lab can answer, in the platform's words and in plain ones."""
    # LOW_SHARPE is last and not in the priority order: no rewrite makes a missing signal
    # appear, and the only answer to it — turning a reliably losing alpha round — applies
    # to a handful of alphas rather than to most of them.
    offered = (*PRIORITY, "LOW_SHARPE")
    return {
        "checks": [{"name": name, "says": SAYS.get(name, "")} for name in offered],
        "note": (
            "These are the checks a good idea usually fails on. Each has a known "
            "answer that leaves the idea itself alone."
        ),
    }


class RepairRequest(ScopedRequest):
    target: int = Field(default=300, ge=1, le=MAX_SIMULATIONS)
    checks: list[str] = Field(
        default_factory=list, description="Empty means every failure this lab can answer"
    )
    seeds: int = Field(default=SEEDS, ge=1, le=200, description="How many near misses to work on")


async def _plan(body: RepairRequest, state: State) -> dict[str, Any]:
    return await state.repairer.plan(
        scope=body.scope(),
        target=body.target,
        checks=body.checks or None,
        seeds=body.seeds,
    )


@router.post("/preview")
async def preview(body: RepairRequest, state: State) -> dict[str, Any]:
    """What would be repaired and how. Costs nothing."""
    return plan_summary(await _plan(body, state))


class RepairRun(RepairRequest):
    task: str = Field(default="repair", description="Slot-quota group")


@router.post("/run")
async def run(body: RepairRun, state: State) -> dict[str, Any]:
    """Queue the repairs. The only call here that spends allowance."""
    plan = await _plan(body, state)
    if not plan["requests"]:
        return {**plan_summary(plan), "queued": [], "skipped": []}

    outcome = await state.engine.enqueue(plan["requests"], task=body.task)
    return {**plan_summary(plan), **outcome, "task": body.task}
