"""Diversify: look where this consultant's own alphas are not."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from ..harvest.seeds import MAX_SIMULATIONS
from .deps import ScopedRequest, State

router = APIRouter(prefix="/api/diversify", tags=["diversify"])


class DiversifyRequest(ScopedRequest):
    target: int = Field(default=1000, ge=1, le=MAX_SIMULATIONS)
    per_dataset: int = Field(default=1, ge=1, le=10)
    patterns: list[str] = Field(default_factory=list)
    windows: list[int] = Field(default_factory=list)
    neutralization: str = "SUBINDUSTRY"


async def _plan(body: DiversifyRequest, state: State) -> dict[str, Any]:
    return await state.diversifier.plan(
        scope=body.scope(),
        target=body.target,
        per_dataset=body.per_dataset,
        patterns=body.patterns or None,
        windows=body.windows or None,
        neutralization=body.neutralization,
    )


def _summary(plan: dict[str, Any]) -> dict[str, Any]:
    harvest = plan.pop("harvest", None)
    return {**plan, **(harvest.summary() if harvest else {"simulations": 0})}


@router.post("/preview")
async def preview(body: DiversifyRequest, state: State) -> dict[str, Any]:
    """Which untouched parts of the data would be tried. Costs nothing."""
    return _summary(await _plan(body, state))


class DiversifyRun(DiversifyRequest):
    task: str = Field(default="diversify", description="Slot-quota group")


@router.post("/run")
async def run(body: DiversifyRun, state: State) -> dict[str, Any]:
    """Queue it. The only call here that spends allowance."""
    plan = await _plan(body, state)
    harvest = plan.get("harvest")
    if harvest is None or not harvest.requests:
        return {**_summary(plan), "queued": [], "skipped": []}
    outcome = await state.engine.enqueue(harvest.requests, task=body.task)
    return {**_summary(plan), **outcome, "task": body.task}
