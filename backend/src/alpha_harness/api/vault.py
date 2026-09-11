"""The alpha vault: what you have run, and what is worth running next.

The endpoint that matters here is ``/mix/candidates``. It answers a question that costs
nothing to ask and would otherwise cost hundreds of simulations to answer: *which two of
my existing alphas, combined, would be better than either?* The daily returns are already
stored, so the combined Sharpe is computed rather than guessed, and only the best few
pairs ever reach the queue.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..brain.filters import AlphaQuery
from ..catalog.queries import Tuple4
from ..plan.yields import PLATFORM_ALPHA_URL
from ..vault.combine import Strategy, build, recommend, strategies
from ..vault.mixing import (
    DEFAULT_MAX_CORRELATION,
    DEFAULT_MIN_SHARPE,
    DEFAULT_MIN_UPLIFT,
)
from .deps import State

router = APIRouter(prefix="/api/vault", tags=["vault"])


class Scope(BaseModel):
    instrument_type: str = "EQUITY"
    region: str
    delay: int
    universe: str

    def to_tuple(self) -> Tuple4:
        return Tuple4(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )


@router.get("")
async def overview(state: State) -> dict[str, Any]:
    """How much of your history is stored, and where it is."""
    return {
        "counts": await state.alphas.counts(),
        "scopes": await state.alphas.scopes(),
        "backfilling": state.backfill.busy,
        "lastSync": state.backfill.last,
    }


@router.get("/submittable")
async def submittable(
    state: State,
    region: str | None = None,
    delay: int | None = None,
    universe: str | None = None,
    instrument_type: str = "EQUITY",
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Alphas that passed every submission check, ready to submit on BRAIN.

    The end of the whole pipeline. Each entry carries the platform's own check results,
    the numbers behind them, and a link to the alpha on BRAIN — which is where it gets
    submitted. This application never submits.
    """
    return await state.yields.submittable(
        region=region,
        delay=delay,
        universe=universe,
        instrument_type=instrument_type,
        limit=limit,
    )


@router.get("/alphas")
async def list_alphas(
    state: State,
    region: str | None = None,
    delay: int | None = None,
    universe: str | None = None,
    instrument_type: str | None = None,
    min_sharpe: float | None = None,
    with_returns: bool = False,
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Stored alphas. Instant — this reads local data, not the platform."""
    return await state.alphas.alphas(
        region=region,
        delay=delay,
        universe=universe,
        instrument_type=instrument_type,
        min_sharpe=min_sharpe,
        with_returns=with_returns,
        limit=limit,
    )


class BackfillRequest(BaseModel):
    include_returns: bool = Field(
        default=True,
        description="Also fetch each alpha's daily series — one throttled request each",
    )
    limit: int = Field(default=5000, ge=1, le=100_000)


@router.post("/backfill")
async def start_backfill(body: BackfillRequest, state: State) -> dict[str, Any]:
    """Import your whole alpha pool and its daily returns.

    Runs in the background and reports through the task indicator. Spends no simulation
    quota — everything here already exists on the platform.
    """
    try:
        task_id = await state.backfill.start(include_returns=body.include_returns, limit=body.limit)
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": "already_running", "message": str(exc)}) from exc
    return {"taskId": task_id}


@router.get("/alphas/{alpha_id}/returns")
async def returns(alpha_id: str, state: State) -> dict[str, Any]:
    """The stored daily series, and the Sharpe it implies.

    ``localSharpe`` is recomputed from the series and should match the platform's own
    figure closely. When it does not, the stored series is incomplete and anything built
    on it — correlations, mix predictions — should be treated with suspicion.
    """
    days = await state.alphas.series_length(alpha_id)
    return {
        "alphaId": alpha_id,
        "days": days,
        "localSharpe": await state.alphas.local_sharpe(alpha_id) if days else None,
    }


# --- the simulations table ------------------------------------------------


class AlphaPageRequest(BaseModel):
    submitted: bool = False
    sort_by: str = "date_created"
    sort_desc: bool = True
    regions: list[str] | None = None
    delays: list[int] | None = None
    universes: list[str] | None = None
    minimum: dict[str, float] = Field(default_factory=dict)
    maximum: dict[str, float] = Field(default_factory=dict)
    search: str | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


@router.post("/alphas/query")
async def query_alphas(body: AlphaPageRequest, state: State) -> dict[str, Any]:
    """The Simulations table: stored alphas, sorted and filtered locally."""
    return await state.alphas.page(**body.model_dump())


@router.post("/sync")
async def sync_alphas(state: State) -> dict[str, Any]:
    """Bring the stored alphas up to date with BRAIN.

    Incremental — only alphas newer than the newest stored, with a day of overlap because
    the platform's date filter works in whole days — once the store holds at least as
    many alphas as BRAIN reports. Otherwise everything is listed, which is also what
    finishes an earlier sync that was interrupted. Only the listing itself — a hundred alphas a
    request, with their metrics — and nothing per alpha: no daily PnL, no submission checks.
    """
    remote = await state.endpoints.list_alphas(AlphaQuery(limit=1, hidden=None))
    stored = await state.catalog.scalar("SELECT count(*) FROM alpha WHERE date_created IS NOT NULL")
    latest = await state.alphas.latest_created()
    complete = latest is not None and int(stored or 0) >= int(remote["count"])
    since = latest - timedelta(days=1) if complete and latest else None
    try:
        task_id = await state.backfill.start(
            include_returns=False, limit=100_000, since=since, resolve_checks=False
        )
    except RuntimeError as exc:
        raise HTTPException(409, detail={"code": "already_running", "message": str(exc)}) from exc
    return {"taskId": task_id, "since": since.isoformat() if since else None}


@router.get("/alphas/{alpha_id}/detail")
async def alpha_detail(alpha_id: str, state: State) -> dict[str, Any]:
    """One alpha with its checks and PnL curve. Downloads the daily PnL the first time."""
    row = (await state.alphas.by_ids([alpha_id])).get(alpha_id)
    if row is None:
        raise HTTPException(
            404,
            detail={
                "code": "unknown_alpha",
                "message": f"{alpha_id} is not stored here yet. Sync from BRAIN first.",
            },
        )
    problem = None
    if await state.alphas.series_length(alpha_id) == 0:
        try:
            await state.backfill.fetch_returns(alpha_id)
        except Exception as exc:
            problem = f"Could not download the daily PnL: {exc}"
    rows = await state.catalog.query(
        "SELECT date, pnl FROM alpha_pnl WHERE alpha_id = ? ORDER BY date", [alpha_id]
    )
    # Stored rows are daily PnL; the chart is the running total, every day with its date. A
    # few thousand points is nothing for the chart, and a sparkline showed years as "120 days".
    values: list[float] = []
    total = 0.0
    for r in rows:
        total += float(r["pnl"])
        values.append(round(total, 2))
    dates = [str(r["date"]) for r in rows]
    return {
        "alphaId": alpha_id,
        "expression": row.get("expression"),
        "settings": {
            k: row.get(k)
            for k in ("region", "universe", "delay", "neutralization", "decay", "truncation")
        },
        "checks": json.loads(row["checks"]) if row.get("checks") else [],
        "pnl": values,
        "dates": dates,
        "days": len(values),
        "kRatio": await state.alphas.k_ratio(alpha_id) if values else None,
        "problem": problem,
        "brainUrl": f"{PLATFORM_ALPHA_URL}{alpha_id}",
    }


class KRatioRequest(BaseModel):
    alpha_ids: list[str] = Field(min_length=1, max_length=100)


#: Background K-Ratio jobs, held so none is garbage-collected mid-flight.
_jobs: set[asyncio.Task[None]] = set()


@router.post("/alphas/k-ratio")
async def k_ratios(body: KRatioRequest, state: State) -> dict[str, Any]:
    """Download daily PnL where missing and compute K-Ratio, in the background.

    One Retry-After request per alpha without a stored series, which is why it is
    capped at 100 at a time.
    """
    ids = list(dict.fromkeys(body.alpha_ids))
    task = await state.tasks.start("k-ratio", f"K-Ratio for {len(ids)} Alphas")

    async def run() -> None:
        failed = 0
        for done, alpha_id in enumerate(ids, start=1):
            try:
                if await state.alphas.series_length(alpha_id) == 0:
                    await state.backfill.fetch_returns(alpha_id)
                if await state.alphas.k_ratio(alpha_id) is None:
                    failed += 1
            except Exception:
                failed += 1
            await state.tasks.update(
                task,
                progress=done / len(ids),
                detail=f"K-Ratio: {done} of {len(ids)}"
                + (f", {failed} without a usable daily PnL" if failed else ""),
            )
        await state.tasks.finish(task)

    job = asyncio.create_task(run(), name="k-ratio")
    _jobs.add(job)
    job.add_done_callback(_jobs.discard)
    return {"taskId": task.id, "alphas": len(ids)}


# --- mixing ---------------------------------------------------------------


class MixQuery(BaseModel):
    scope: Scope
    sizes: list[int] = Field(
        default_factory=lambda: [2],
        description="How many alphas per mix: 2, 3 or 4. Ask for several at once.",
    )
    neutralization: str | None = Field(
        default=None,
        description=(
            "Restrict to one neutralization. Strongly recommended — neutralization is "
            "linear, so a prediction built from finished PnL series only holds when "
            "every member was neutralized the same way."
        ),
    )
    min_sharpe: float = Field(
        default=DEFAULT_MIN_SHARPE,
        description=(
            "Only use alphas at or above this. Below it there is rarely anything to rescue."
        ),
    )
    max_correlation: float = Field(
        default=DEFAULT_MAX_CORRELATION,
        ge=0.0,
        le=1.0,
        description="Above this the signals are the same and mixing buys nothing",
    )
    min_uplift: float | None = Field(
        default=DEFAULT_MIN_UPLIFT,
        description=(
            "Only show mixes that beat the best of their members. Null shows everything, "
            "including mixes that would be a step backwards."
        ),
    )
    include_submitted: bool = Field(
        default=True,
        description=(
            "Use already-submitted alphas as ingredients. A submitted alpha is still a "
            "signal, and mixing one with something new can produce a distinct alpha."
        ),
    )
    check_self_correlation: bool = Field(
        default=True,
        description="Predict whether each mix would pass the platform's self-correlation test",
    )
    population: int = Field(default=120, ge=2, le=200)
    limit: int = Field(default=40, ge=1, le=500)


@router.get("/mix/strategies")
async def combine_strategies() -> dict[str, Any]:
    """The ways signals can be put together, and when each is the right one."""
    return {
        "strategies": strategies(),
        "sizes": [2, 3, 4],
        "note": (
            "Which one is right depends on what the expressions already are. Two signals "
            "that are both already ranked share a scale and can be added directly; a raw "
            "ratio and a rank do not, and adding them would weight them arbitrarily. Each "
            "candidate carries the recommendation for its own members."
        ),
    }


@router.post("/mix/candidates")
async def mix_candidates(body: MixQuery, state: State) -> dict[str, Any]:
    """Groups worth simulating, best first.

    Each carries the Sharpe its combination would have — computed from the stored daily
    series rather than estimated — how much that beats its best member, and whether it
    would pass the platform's self-correlation test. A hundred candidates cost one query
    instead of a hundred simulations.
    """
    scope = body.scope
    result = await state.mixer.candidates(
        region=scope.region,
        delay=scope.delay,
        universe=scope.universe,
        instrument_type=scope.instrument_type,
        neutralization=body.neutralization,
        sizes=body.sizes,
        min_sharpe=body.min_sharpe,
        max_correlation=body.max_correlation,
        min_uplift=body.min_uplift,
        population=body.population,
        limit=body.limit,
        include_submitted=body.include_submitted,
        check_self_correlation=body.check_self_correlation,
    )
    return {
        **result,
        "count": len(result["groups"]),
        "prediction": (
            "Combined Sharpe is computed from the stored daily returns and assumes the "
            "mix holds the sum of its members' positions. The platform's truncation will "
            "move it, so treat this as a ranking and let the simulation settle the number."
        ),
    }


@router.post("/mix/correlations")
async def correlations(body: MixQuery, state: State) -> dict[str, Any]:
    """The full pairwise correlation grid for one scope."""
    scope = body.scope
    return await state.mixer.correlation_matrix(
        region=scope.region,
        delay=scope.delay,
        universe=scope.universe,
        instrument_type=scope.instrument_type,
        neutralization=body.neutralization,
        min_sharpe=body.min_sharpe,
        limit=min(body.population, 60),
    )


class MixPreview(BaseModel):
    """Ask what a specific group would look like, before committing to it."""

    members: list[str] = Field(min_length=2, max_length=4)
    strategy: str | None = Field(
        default=None, description="Omit to use whichever suits these expressions"
    )
    weights: list[float] | None = None


@router.post("/mix/preview")
async def preview_mix(body: MixPreview, state: State) -> dict[str, Any]:
    """The expression a group would produce, and why it is put together that way."""
    rows = await state.alphas.by_ids(body.members)
    missing = [m for m in body.members if m not in rows]
    if missing:
        raise HTTPException(
            404,
            detail={
                "code": "not_in_vault",
                "message": f"Not in the vault: {', '.join(missing)}. Run a backfill first.",
            },
        )

    expressions = [str(rows[m]["expression"] or "") for m in body.members]
    advice = recommend(expressions)
    strategy = Strategy(body.strategy) if body.strategy else advice.strategy

    problems = _scope_problems([rows[m] for m in body.members])
    try:
        expression = build(expressions, strategy=strategy, weights=body.weights)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "cannot_combine", "message": str(exc)}) from exc

    return {
        "members": body.members,
        "expression": expression,
        "strategy": str(strategy),
        "recommended": advice.to_dict(),
        "problems": problems,
    }


class MixRun(BaseModel):
    """Turn chosen groups into simulations."""

    groups: list[list[str]] = Field(description="Each entry is 2-4 alpha ids to combine")
    strategy: str | None = Field(
        default=None, description="Omit to choose per group from what the expressions are"
    )
    task: str = Field(default="mix", description="Slot-quota group")
    skip_duplicates: bool = True
    dry_run: bool = False


@router.post("/mix/run")
async def run_mixes(body: MixRun, state: State) -> dict[str, Any]:
    """Combine each group into one new alpha and queue it.

    Members must share a scope *and* a neutralization. The scope because combining a
    USA alpha with a CHN one is not a trade; the neutralization because the combined
    alpha gets exactly one, and applying industry-neutrality to a mix that included a
    market-neutral signal is not the alpha whose Sharpe was predicted.
    """
    from ..brain.schemas import SimulationRequest, SimulationSettings

    requests: list[SimulationRequest] = []
    built: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []

    lookup = await state.alphas.by_ids(sorted({m for group in body.groups for m in group}))

    for group in body.groups:
        if not 2 <= len(group) <= 4:
            problems.append({"group": group, "reason": "A mix needs between two and four alphas."})
            continue

        rows = [lookup.get(m) for m in group]
        if any(r is None for r in rows):
            missing = [m for m in group if m not in lookup]
            problems.append(
                {
                    "group": group,
                    "reason": f"Not in the vault: {', '.join(missing)}. Run a backfill first.",
                }
            )
            continue

        members = [r for r in rows if r is not None]
        blockers = _scope_problems(members)
        if blockers:
            problems.append({"group": group, "reason": " ".join(blockers)})
            continue

        expressions = [str(m["expression"] or "") for m in members]
        strategy = Strategy(body.strategy) if body.strategy else recommend(expressions).strategy
        try:
            expression = build(expressions, strategy=strategy)
        except ValueError as exc:
            problems.append({"group": group, "reason": str(exc)})
            continue

        first = members[0]
        requests.append(
            SimulationRequest(
                type="REGULAR",
                settings=SimulationSettings(
                    instrumentType=first["instrument_type"],
                    region=first["region"],
                    delay=first["delay"],
                    universe=first["universe"],
                    neutralization=first["neutralization"] or "SUBINDUSTRY",
                    # Decay and truncation are properties of the new alpha, not
                    # inherited averages. The members' own values shaped series that
                    # have already been measured; the mix starts clean.
                    decay=0,
                    truncation=0.08,
                ),
                regular=expression,
            )
        )
        built.append({"group": group, "expression": expression, "strategy": str(strategy)})

    if body.dry_run or not requests:
        return {"built": built, "problems": problems, "queued": [], "dryRun": True}

    result = await state.engine.enqueue(
        requests, task=body.task, skip_duplicates=body.skip_duplicates
    )
    return {
        "built": built,
        "problems": problems,
        **result,
        "status": await state.engine.status(),
    }


def _scope_problems(members: list[dict[str, Any]]) -> list[str]:
    """Why these alphas cannot be mixed, if they cannot.

    Scope and neutralization both have to match, and the neutralization reason is the
    subtle one: it is a linear operation, so the prediction that a mix's Sharpe can be
    read off its members' finished PnL series only holds when they were all neutralized
    the same way.
    """
    problems: list[str] = []

    scopes = {(m["instrument_type"], m["region"], m["delay"], m["universe"]) for m in members}
    if len(scopes) > 1:
        labels = sorted(f"{s[1]}/D{s[2]}/{s[3]}" for s in scopes)
        problems.append(
            f"These ran in different scopes ({', '.join(labels)}), so combining them "
            "would not be a tradeable alpha."
        )

    neutralizations = {m.get("neutralization") for m in members}
    if len(neutralizations) > 1:
        named = sorted(str(n) for n in neutralizations)
        problems.append(
            f"They were neutralized differently ({', '.join(named)}). The combined alpha "
            "gets only one neutralization, so its behaviour would not be the sum of "
            "these — and the predicted Sharpe would not mean anything."
        )

    return problems
