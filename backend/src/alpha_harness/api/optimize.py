"""Studies: searching a template's space instead of enumerating it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..db.models import StudyStatus
from ..optimize import objectives as obj
from ..optimize import samplers as samp
from ..optimize.errors import StudyNotFoundError
from ..optimize.study import (
    SETTING_PREFIX,
    build_space,
    serialise_study,
    serialise_trial,
)
from ..templates.library import TemplateNotFoundError
from ..templates.schema import parse
from .deps import State

router = APIRouter(prefix="/api/studies", tags=["optimize"])


@router.get("/options")
async def options() -> dict[str, Any]:
    """Samplers and objectives, with the sentence needed to choose between them.

    Served as data rather than hardcoded in the frontend so the tooltips and the
    behaviour can never disagree.
    """
    return {
        "samplers": samp.catalogue(),
        "objectives": obj.catalogue(),
        "defaults": {
            "sampler": samp.DEFAULT_SAMPLER,
            "objectives": list(obj.DEFAULT_OBJECTIVES),
            "batchSize": samp.BATCH_SIZE,
            "slots": samp.SLOTS,
        },
        "batching": {
            "batchSize": samp.BATCH_SIZE,
            "fullRound": samp.BATCH_SIZE * samp.SLOTS,
            "note": (
                "A multi-simulation carries ten children and eight run at once, so a "
                "round of eighty fills the platform exactly. Any other number leaves "
                "slots holding part-empty batches for the length of their run."
            ),
        },
    }


class CreateStudy(BaseModel):
    name: str = Field(description="Unique. Also the default slot-quota group.")
    template_id: int | None = None
    template_source: str | None = Field(
        default=None, description="Inline YAML, when not searching a stored template."
    )
    sampler: str = samp.DEFAULT_SAMPLER
    sampler_params: dict[str, Any] = Field(default_factory=dict)
    objectives: list[str] = Field(default_factory=lambda: list(obj.DEFAULT_OBJECTIVES))
    batch_size: int = Field(default=samp.BATCH_SIZE, ge=1, le=80)
    max_trials: int = Field(default=80, ge=1, le=10_000)
    seed: int | None = None
    task: str | None = None
    search_batch_settings: bool = Field(
        default=False,
        description=(
            "Let the sampler vary region, delay, instrument type or language. Off by "
            "default: those are four of the five fields a batch's children must share, "
            "so searching them gives almost every trial its own batch."
        ),
    )
    start: bool = Field(default=False, description="Begin immediately after creating.")


@router.post("", status_code=201)
async def create_study(body: CreateStudy, state: State) -> dict[str, Any]:
    source = body.template_source
    if source is None:
        if body.template_id is None:
            raise TemplateNotFoundError("none given")
        row = await state.templates.get(body.template_id)
        if row is None:
            raise TemplateNotFoundError(body.template_id)
        source = row.source

    params = dict(body.sampler_params)
    if body.search_batch_settings:
        params["search_batch_settings"] = True

    study = await state.optimizer.create(
        name=body.name,
        template_source=source,
        template_id=body.template_id,
        sampler=body.sampler,
        sampler_params=params,
        objective_keys=body.objectives,
        batch_size=body.batch_size,
        max_trials=body.max_trials,
        seed=body.seed,
        task=body.task,
        search_batch_settings=body.search_batch_settings,
    )
    if body.start:
        study = await state.optimizer.set_status(study.id, StudyStatus.RUNNING)
    return serialise_study(study, await state.optimizer.counts(study.id))


class PreviewStudy(BaseModel):
    """What a study would search, before committing to it."""

    template_id: int | None = None
    template_source: str | None = None
    sampler: str = samp.DEFAULT_SAMPLER
    sampler_params: dict[str, Any] = Field(default_factory=dict)
    objectives: list[str] = Field(default_factory=lambda: list(obj.DEFAULT_OBJECTIVES))
    batch_size: int = Field(default=samp.BATCH_SIZE, ge=1, le=80)
    search_batch_settings: bool = False


@router.post("/preview")
async def preview(body: PreviewStudy, state: State) -> dict[str, Any]:
    """The search space, the sampler's adjustments, and what got pinned.

    Costs nothing and touches no platform quota, so the study form can call it on every
    change.
    """
    source = body.template_source
    if source is None and body.template_id is not None:
        row = await state.templates.get(body.template_id)
        source = row.source if row else None
    if source is None:
        raise TemplateNotFoundError(body.template_id or "none given")

    spec = parse(source)
    # The same review the Template Studio shows. A variable that resolves to nothing
    # simply disappears from the search space, so without this the preview would report
    # a smaller space and say nothing about why.
    review = await state.studio.review(spec, expand_grid=False)
    prepared = review.prepared
    space = build_space(spec, prepared, search_batch_settings=body.search_batch_settings)
    resolved = obj.resolve(body.objectives)
    _, notes = samp.build(
        body.sampler,
        batch_size=body.batch_size,
        n_objectives=len(resolved),
        params=body.sampler_params,
        search_space={k: v.get("choices", []) for k, v in space.items()},
    )

    pinned = [
        name for name in spec.batch_splitting_sweeps if f"{SETTING_PREFIX}{name}" not in space
    ]
    # Only meaningful when every dimension is a finite set. A continuous range has no
    # grid size, and reporting one anyway would make an unbounded space look countable.
    grid: int | None = 1
    for entry in space.values():
        if entry["kind"] != "categorical":
            grid = None
            break
        grid *= max(1, len(entry.get("choices", [])))

    return {
        "template": spec.name,
        "space": {
            name: {
                **entry,
                "isSetting": name.startswith(SETTING_PREFIX),
                "size": len(entry.get("choices", [])) if entry["kind"] == "categorical" else None,
            }
            for name, entry in space.items()
        },
        "dimensions": len(space),
        "gridSize": grid,
        "objectives": [o.to_dict() for o in resolved],
        "samplerNotes": notes,
        "pinnedSettings": pinned,
        "validation": review.report.to_dict(),
        "resolution": prepared.to_dict() if prepared else None,
    }


@router.get("")
async def list_studies(state: State) -> list[dict[str, Any]]:
    return [serialise_study(row, counts) for row, counts in await state.optimizer.list_all()]


@router.get("/{study_id}")
async def get_study(study_id: int, state: State) -> dict[str, Any]:
    row = await state.optimizer.get(study_id)
    if row is None:
        raise StudyNotFoundError(study_id)
    return serialise_study(row, await state.optimizer.counts(study_id))


@router.get("/{study_id}/trials")
async def list_trials(
    study_id: int, state: State, limit: int = Query(500, ge=1, le=10_000)
) -> list[dict[str, Any]]:
    """Every trial, with its parameters, expression, objective values and checks.

    Nothing summarised away — this is the raw table a researcher sorts and filters.
    """
    return [serialise_trial(t) for t in await state.optimizer.trials(study_id, limit=limit)]


@router.get("/{study_id}/pareto")
async def pareto(study_id: int, state: State) -> dict[str, Any]:
    """The frontier: trials nothing else beats on every objective at once."""
    return await state.optimizer.pareto(study_id)


@router.post("/{study_id}/start")
async def start_study(study_id: int, state: State) -> dict[str, Any]:
    row = await state.optimizer.set_status(study_id, StudyStatus.RUNNING)
    return serialise_study(row, await state.optimizer.counts(study_id))


@router.post("/{study_id}/pause")
async def pause_study(study_id: int, state: State) -> dict[str, Any]:
    """Stop asking for new trials.

    Simulations already in flight are left alone — they have been paid for, and their
    results are still worth telling the sampler about.
    """
    row = await state.optimizer.set_status(study_id, StudyStatus.PAUSED)
    return serialise_study(row, await state.optimizer.counts(study_id))


@router.post("/{study_id}/advance")
async def advance_study(study_id: int, state: State) -> dict[str, Any]:
    """Run one round now: harvest what finished, then ask for the next batch."""
    result = await state.optimizer.advance(study_id)
    row = await state.optimizer.get(study_id)
    return {
        **result,
        "study": serialise_study(row, await state.optimizer.counts(study_id)) if row else None,
    }


@router.delete("/{study_id}", status_code=204)
async def delete_study(study_id: int, state: State) -> None:
    await state.optimizer.delete(study_id)
