"""Template Lab: templates built from blocks, and the tasks that search them for Sharpe.

Presets are read-only; templates the user saves live in the ``template`` table under the
``template-lab`` origin. A task freezes its template and market when it is added and, like
every lab's task, only runs from the Tasks tab.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db.models import Study, StudyStatus, Template, utcnow
from ..labs import search, template
from ..optimize.study import TEMPLATE_SAMPLER
from .deps import State
from .search_lab import SearchRequest, account_operators, market_for, startup_trials

router = APIRouter(prefix="/api/template-lab", tags=["template-lab"])

ORIGIN = "template-lab"


class TemplateBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    tree: dict[str, Any]


class TemplateTask(SearchRequest):
    tree: dict[str, Any]
    template_id: int | None = None
    template_name: str = Field(default="Template", max_length=128)


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _loaded(tree: dict[str, Any]) -> dict[str, Any]:
    try:
        return template.load(tree)
    except ValueError as exc:
        raise _refuse(422, "template_malformed", str(exc)) from exc


async def _table(state: Any) -> dict[str, template.Block]:
    return template.blocks(await account_operators(state, refresh=False))


@router.get("/options")
async def options(state: State, refresh: bool = False) -> dict[str, Any]:
    """The blocks this account can build with, and what a task can be set to."""
    operators = await account_operators(state, refresh=refresh)
    described = {o.get("name"): o.get("description") for o in operators}
    return {
        "operators": {"synced": bool(operators), "count": len(operators)},
        "blocks": [
            {**block.to_dict(), "description": described.get(block.name)}
            for block in template.blocks(operators).values()
        ],
        "variables": {name: list(values) for name, values in template.VARIABLES.items()},
        "tags": list(template.TAGS),
        "dataFields": list(template.DATA_FIELDS),
        "groupFields": list(template.GROUP_FIELDS),
        "vector": list(search.catalogue(operators).vector),
        "decays": list(search.DECAYS),
        "truncation": search.TRUNCATION,
        "maxCores": search.MAX_CORES,
        "maxSimulations": search.MAX_SIMULATIONS,
        "maxBlocks": template.MAX_NODES,
    }


def _saved(row: Template, table: dict[str, template.Block]) -> dict[str, Any]:
    doc = row.parsed or {"version": template.VERSION, "root": None}
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "preset": False,
        "tree": doc,
        "skeleton": row.source,
        "missing": template.missing(doc, table),
        "source": None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/templates")
async def templates(state: State) -> dict[str, Any]:
    """The presets, then the user's saved templates, newest first."""
    table = await _table(state)
    presets = [
        {
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "preset": True,
            "tree": preset.doc,
            "skeleton": template.skeleton(preset.doc),
            "missing": template.missing(preset.doc, table),
            "source": preset.source,
            "updatedAt": None,
        }
        for preset in template.PRESETS
    ]
    async with state.db.session() as session:
        rows = (
            await session.scalars(
                select(Template)
                .where(Template.origin == ORIGIN)
                .order_by(Template.updated_at.desc())
            )
        ).all()
    return {"templates": [*presets, *(_saved(row, table) for row in rows)]}


async def _name_free(session: Any, name: str, template_id: int | None = None) -> None:
    if any(preset.name.lower() == name.lower() for preset in template.PRESETS):
        raise _refuse(409, "name_taken", f"{name} is the name of a preset.")
    clash = select(Template.id).where(func.lower(Template.name) == name.lower())
    if template_id is not None:
        clash = clash.where(Template.id != template_id)
    if await session.scalar(clash) is not None:
        raise _refuse(409, "name_taken", f"A template named {name} already exists.")


@router.post("/templates", status_code=201)
async def create_template(body: TemplateBody, state: State) -> dict[str, Any]:
    doc, name = _loaded(body.tree), body.name.strip()
    async with state.db.session() as session:
        await _name_free(session, name)
        row = Template(
            name=name,
            description=body.description,
            source=template.skeleton(doc),
            parsed=doc,
            origin=ORIGIN,
            tags=[],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _saved(row, await _table(state))


@router.put("/templates/{template_id}")
async def update_template(template_id: int, body: TemplateBody, state: State) -> dict[str, Any]:
    doc, name = _loaded(body.tree), body.name.strip()
    async with state.db.session() as session:
        row = await session.get(Template, template_id)
        if row is None or row.origin != ORIGIN:
            raise _refuse(404, "template_not_found", "That template no longer exists.")
        await _name_free(session, name, template_id)
        row.name, row.description = name, body.description
        row.source, row.parsed = template.skeleton(doc), doc
        await session.commit()
        await session.refresh(row)
    return _saved(row, await _table(state))


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, state: State) -> dict[str, Any]:
    async with state.db.session() as session:
        row = await session.get(Template, template_id)
        if row is None or row.origin != ORIGIN:
            raise _refuse(404, "template_not_found", "That template no longer exists.")
        await session.delete(row)
        await session.commit()
    return {"removed": template_id}


async def _plan(body: TemplateTask, state: Any) -> dict[str, Any]:
    """Everything a task would search, checked, without queueing anything."""
    problems: list[str] = []
    try:
        doc: dict[str, Any] | None = template.load(body.tree)
    except ValueError as exc:
        doc = None
        problems.append(str(exc))
    use = template.used(doc) if doc is not None else None
    market = await market_for(body, state, need=use.data if use else ())
    if doc is not None:
        problems.extend(template.problems(doc, template.blocks(market["operators"])))
    # Said under the blocks; the market's problems are said with the task's settings.
    template_problems = list(problems)
    problems.extend(market["problems"])
    pool = market["pool"]
    if use and len(use.fields) > 1 and 0 < len(pool.fields) < len(use.fields):
        problems.append(
            f"This template reads {len(use.fields)} different fields, "
            f"but the chosen datasets hold {len(pool.fields)}."
        )

    space = {
        "fields": pool.fields,
        "universes": list(pool.universes),
        "absent": pool.absent,
        "vector": market["vector"],
        "neutralizations": market["neutralizations"],
        "variables": {name: list(values) for name, values in template.VARIABLES.items()},
    }
    sample: list[dict[str, Any]] = []
    problems = list(dict.fromkeys(problems))
    if not problems and doc is not None:
        rng = random.Random()
        run = {"region": body.region, "delay": body.delay, "decay": body.decay}
        run |= {"tree": doc, "space": space}
        choices = search.field_choices(space)
        for _ in range(25):  # a draw can land two FIELD tags on one field
            if len(sample) == 5:
                break
            _, request = template.draw(search.RandomTrial(rng), run, choices)
            if request is not None:
                settings = request.settings.model_dump(by_alias=True, exclude_none=True)
                sample.append({"expression": request.regular, "settings": settings})

    matrix = sum(1 for t in pool.fields.values() if t == "MATRIX")
    return {
        "round": body.cores * 10,
        "fields": {
            "total": len(pool.fields),
            "matrix": matrix,
            "vector": len(pool.fields) - matrix,
        },
        "leftOut": {"vector": pool.vector_skipped},
        "universes": list(pool.universes),
        "neutralizations": market["neutralizations"],
        "skeleton": template.skeleton(doc) if doc is not None else "?",
        "sample": sample,
        "problems": problems,
        "templateProblems": template_problems,
        "warnings": market["warnings"],
        "space": space,
        "tree": doc,
    }


@router.post("/preview")
async def preview(body: TemplateTask, state: State) -> dict[str, Any]:
    """What a task would search. Free; queues nothing."""
    return {k: v for k, v in (await _plan(body, state)).items() if k not in ("space", "tree")}


@router.post("/tasks", status_code=201)
async def add_task(body: TemplateTask, state: State) -> dict[str, Any]:
    """Add the template's search to Tasks, not started. It spends nothing until run there."""
    if body.simulations < 1:
        raise _refuse(422, "no_simulations", "Assign the simulations for this task.")
    plan = await _plan(body, state)
    if plan["problems"]:
        raise _refuse(422, "template_blocked", plan["problems"][0])

    now = utcnow()
    per_round, size = plan["round"], body.simulations
    task = f"template-{now:%y%m%d%H%M%S%f}"
    async with state.db.session() as session:
        template_id = body.template_id
        if template_id is not None:
            saved = await session.get(Template, template_id)
            template_id = saved.id if saved is not None and saved.origin == ORIGIN else None
        row = Study(
            name=f"Template Lab · {task}",
            template_id=template_id,
            template_name=body.template_name.strip() or "Template",
            template_source=plan["skeleton"],
            sampler=TEMPLATE_SAMPLER,
            sampler_params={
                "tree": plan["tree"],
                "space": plan["space"],
                "region": body.region,
                "delay": body.delay,
                "decay": body.decay,
                "cores": body.cores,
                "datasetIds": body.dataset_ids,
                "n_startup_trials": startup_trials(len(plan["space"]["fields"]), size, per_round),
                "multivariate": True,
                "group": True,
            },
            objectives=["sharpe"],
            directions=["maximize"],
            batch_size=per_round,
            max_trials=size,
            task=task,
            status=StudyStatus.IDLE,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await state.optimizer._notify()
    return {"id": row.id, "name": row.name}
