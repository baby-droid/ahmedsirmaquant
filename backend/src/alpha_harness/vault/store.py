"""Keeping every alpha and its daily returns.

Two reasons this exists, and the second is the interesting one.

**Nothing should be simulated twice.** An alpha is a permanent result. Once the platform
has computed it there is no reason to spend quota on it again, and a local copy makes
that free rather than a round trip.

**Daily returns are what make mixing possible.** The platform reports one Sharpe per
alpha; the daily profit-and-loss series behind it is 2,500 numbers, and two alphas with
mediocre Sharpe that move independently combine into something better than either. That
question — which pairs move independently — is only answerable if the series are kept.

The series is also *sufficient*: Sharpe recomputed as ``mean / stdev * sqrt(252)`` from
the stored daily PnL matches the platform's own figure to within 0.01, verified against
live alphas. So a mix's Sharpe can be predicted before a single simulation is spent on
it, which is the entire point of the exercise.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from itertools import accumulate
from typing import Any

import structlog

from ..brain.schemas import Alpha
from ..db.duck import ALPHA_COLUMNS, TRAIN_COLUMNS, Catalog
from ..plan.yields import is_promising

log = structlog.get_logger(__name__)

#: Trading days in a year. The platform annualises Sharpe with this, and matching it is
#: what makes a locally computed Sharpe comparable to a reported one.
TRADING_DAYS = 252


def checks_json(alpha: Alpha) -> str | None:
    """An alpha's submission checks as the string the vault stores.

    Pulled out of :func:`alpha_row` because the backfill has to read the same array to
    decide whether the platform is worth asking to finish checking it, and two spellings
    of "the checks" would eventually disagree.
    """
    stats = alpha.in_sample
    if stats is None:
        return None
    return json.dumps([c.model_dump(by_alias=True) for c in stats.checks])


def alpha_row(alpha: Alpha, fetched_at: datetime) -> tuple:
    """One alpha flattened for storage."""
    stats = alpha.in_sample
    settings = alpha.settings
    code = alpha.regular or alpha.combo or alpha.selection
    return (
        alpha.id,
        (code.code if code else None),
        str(alpha.type) if alpha.type else None,
        settings.instrument_type if settings else None,
        settings.region if settings else None,
        settings.delay if settings else None,
        settings.universe if settings else None,
        settings.neutralization if settings else None,
        settings.decay if settings else None,
        settings.truncation if settings else None,
        stats.sharpe if stats else None,
        stats.fitness if stats else None,
        stats.turnover if stats else None,
        stats.returns if stats else None,
        stats.drawdown if stats else None,
        stats.margin if stats else None,
        stats.long_count if stats else None,
        stats.short_count if stats else None,
        alpha.grade,
        alpha.stage,
        alpha.status,
        (code.operator_count if code else None),
        alpha.date_created,
        checks_json(alpha),
        fetched_at,
        alpha.name,
        alpha.date_submitted,
    )


#: What the Simulations table may sort and filter on, as SQL over ``alpha a``. Anything
#: not in here is ignored rather than interpolated.
ALPHA_METRICS: dict[str, str] = {
    "sharpe": "a.sharpe",
    "fitness": "a.fitness",
    "turnover": "a.turnover",
    "returns": "a.returns",
    "drawdown": "a.drawdown",
    "margin": "a.margin",
    "operator_count": "a.operator_count",
    "k_ratio": "a.k_ratio",
    "calmar": "CASE WHEN a.drawdown > 0 THEN a.returns / a.drawdown END",
    "date_created": "a.date_created",
    "date_submitted": "a.date_submitted",
}

#: Anything past UNSUBMITTED (ACTIVE, DECOMMISSIONED, ...) has been submitted.
SUBMITTED = "(a.status IS NOT NULL AND a.status <> 'UNSUBMITTED')"


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _page_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "alphaId": r["alpha_id"],
        "name": r["name"],
        "type": r["sim_type"],
        "status": r["status"],
        "region": r["region"],
        "universe": r["universe"],
        "delay": r["delay"],
        "neutralization": r["neutralization"],
        "decay": r["decay"],
        "truncation": r["truncation"],
        "expression": r["expression"],
        "sharpe": r["sharpe"],
        "fitness": r["fitness"],
        "turnover": r["turnover"],
        "returns": r["returns"],
        "drawdown": r["drawdown"],
        "margin": r["margin"],
        "operatorCount": r["operator_count"],
        "kRatio": r["k_ratio"],
        "calmar": r["calmar"],
        "dateCreated": _iso(r["date_created"]),
        "dateSubmitted": _iso(r["date_submitted"]),
        "hasPnl": bool(r["has_pnl"]),
    }


def pnl_rows(alpha_id: str, rows: list[dict[str, Any]]) -> list[tuple]:
    """The daily series flattened for storage, skipping days with no value."""
    out: list[tuple] = []
    for row in rows:
        raw_date, raw_pnl = row.get("date"), row.get("pnl")
        if raw_date is None or raw_pnl is None:
            continue
        out.append((alpha_id, _as_date(raw_date), float(raw_pnl)))
    return out


def k_ratio(daily_pnl: list[float]) -> float | None:
    """Kestner's K-Ratio (2003 form) of a daily PnL series.

    A straight line is fitted to cumulative PnL against the day number; the slope over
    its standard error, divided by the square root of the number of days, rewards steady
    growth over the same total earned in a few jumps.
    """
    n = len(daily_pnl)
    if n < 3:
        return None
    y = list(accumulate(daily_pnl))
    x_mean = (n + 1) / 2
    y_mean = sum(y) / n
    xs = [i - x_mean for i in range(1, n + 1)]
    sxx = sum(x * x for x in xs)
    slope = sum(x * (v - y_mean) for x, v in zip(xs, y, strict=True)) / sxx
    sse = sum((v - y_mean - slope * x) ** 2 for x, v in zip(xs, y, strict=True))
    if sse <= 0:
        return None
    standard_error = math.sqrt(sse / (n - 2) / sxx)
    return slope / (standard_error * math.sqrt(n))


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class AlphaVault:
    """The local record of every alpha and its returns."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    # -- writing ---------------------------------------------------------

    async def save_alpha(self, alpha: Alpha, *, fetched_at: datetime | None = None) -> None:
        from ..db.models import utcnow

        await self._save([alpha], fetched_at or utcnow())

    async def save_alphas(self, alphas: list[Alpha]) -> int:
        """A page of alphas in one write. Ids must be distinct within the page."""
        from ..db.models import utcnow

        return await self._save(alphas, utcnow())

    async def _save(self, alphas: list[Alpha], fetched_at: datetime) -> int:
        """Train statistics go in the same write, and only for Alphas that carry them.

        A harvest therefore never reads a stored child without them, and a listing that
        leaves them out never blanks them.
        """
        written = await self.catalog.upsert_alphas(
            [alpha_row(a, fetched_at) for a in alphas if a.train is None]
        )
        return written + await self.catalog.upsert(
            "alpha",
            ALPHA_COLUMNS + TRAIN_COLUMNS,
            [
                (*alpha_row(a, fetched_at), a.train.sharpe, a.train.fitness)
                for a in alphas
                if a.train
            ],
        )

    async def save_pnl(self, alpha_id: str, rows: list[dict[str, Any]]) -> int:
        flattened = pnl_rows(alpha_id, rows)
        if not flattened:
            return 0
        await self.catalog.upsert_pnl(flattened)
        return len(flattened)

    async def save_checks(self, alpha_id: str, checks: list[dict[str, Any]]) -> None:
        """Replace one alpha's check array and touch nothing else.

        A partial-column upsert: the platform's ``/check`` endpoint returns resolved
        checks and no metrics, so writing a whole row from it would blank out the Sharpe
        this alpha was stored with.
        """
        await self.catalog.upsert("alpha", ("alpha_id", "checks"), [(alpha_id, json.dumps(checks))])

    # -- reading ---------------------------------------------------------

    async def counts(self) -> dict[str, Any]:
        rows = await self.catalog.query(
            """
            SELECT
                (SELECT count(*) FROM alpha)                              AS alphas,
                (SELECT count(DISTINCT alpha_id) FROM alpha_pnl)          AS with_returns,
                (SELECT count(*) FROM alpha_pnl)                          AS daily_rows,
                (SELECT count(*) FROM alpha WHERE sharpe IS NOT NULL)     AS scored
            """
        )
        row = rows[0] if rows else {}
        return {
            "alphas": int(row.get("alphas") or 0),
            "withReturns": int(row.get("with_returns") or 0),
            "dailyRows": int(row.get("daily_rows") or 0),
            "scored": int(row.get("scored") or 0),
            "missingReturns": int(row.get("alphas") or 0) - int(row.get("with_returns") or 0),
        }

    async def scopes(self) -> list[dict[str, Any]]:
        """Which scopes hold alphas. Mixing only ever happens inside one of these."""
        return await self.catalog.query(
            """
            SELECT instrument_type, region, delay, universe,
                   count(*) AS alphas,
                   count(*) FILTER (WHERE sharpe IS NOT NULL) AS scored,
                   max(sharpe) AS best_sharpe
            FROM alpha
            WHERE region IS NOT NULL
            GROUP BY 1, 2, 3, 4
            ORDER BY alphas DESC
            """
        )

    async def without_returns(self, limit: int = 5000) -> list[str]:
        """Alphas whose daily series has not been fetched yet."""
        rows = await self.catalog.query(
            """
            SELECT a.alpha_id FROM alpha a
            LEFT JOIN (SELECT DISTINCT alpha_id FROM alpha_pnl) p USING (alpha_id)
            WHERE p.alpha_id IS NULL
            ORDER BY a.sharpe DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        )
        return [str(r["alpha_id"]) for r in rows]

    async def awaiting_checks(self, limit: int = 200) -> list[str]:
        """Alphas the platform has not finished judging, best first.

        ``LIKE '%PENDING%'`` is a cheap prefilter over the stored JSON — the caller
        decides what is actually worth asking about, because "nothing has failed yet"
        needs the array parsed.
        """
        rows = await self.catalog.query(
            """
            SELECT alpha_id, checks FROM alpha
            WHERE checks LIKE '%PENDING%'
              AND (status IS NULL OR status = 'UNSUBMITTED')
            ORDER BY sharpe DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        )
        return [str(r["alpha_id"]) for r in rows if is_promising(r.get("checks"))]

    async def latest_created(self) -> datetime | None:
        """The newest alpha stored, which is where an incremental sync resumes."""
        return await self.catalog.scalar("SELECT max(date_created) FROM alpha")

    async def page(
        self,
        *,
        submitted: bool = False,
        sort_by: str = "date_created",
        sort_desc: bool = True,
        regions: list[str] | None = None,
        delays: list[int] | None = None,
        universes: list[str] | None = None,
        minimum: dict[str, float] | None = None,
        maximum: dict[str, float] | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """One page of the Simulations table: sorted, filtered, with the total."""
        clauses = ["a.date_created IS NOT NULL", SUBMITTED if submitted else f"NOT {SUBMITTED}"]
        params: list[Any] = []
        for column, values in (("region", regions), ("delay", delays), ("universe", universes)):
            if values:
                clauses.append(f"a.{column} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        for bounds, op in ((minimum, ">="), (maximum, "<=")):
            for key, value in (bounds or {}).items():
                if key in ALPHA_METRICS and not key.startswith("date_"):
                    clauses.append(f"({ALPHA_METRICS[key]}) {op} ?")
                    params.append(value)
        if search:
            needle = f"%{search.lower()}%"
            clauses.append(
                "(lower(a.alpha_id) LIKE ? OR lower(coalesce(a.expression, '')) LIKE ? "
                "OR lower(coalesce(a.name, '')) LIKE ?)"
            )
            params.extend([needle, needle, needle])

        where = " AND ".join(clauses)
        order = ALPHA_METRICS.get(sort_by, ALPHA_METRICS["date_created"])
        direction = "DESC" if sort_desc else "ASC"
        total = await self.catalog.scalar(f"SELECT count(*) FROM alpha a WHERE {where}", params)
        rows = await self.catalog.query(
            f"""
            SELECT a.alpha_id, a.name, a.sim_type, a.status, a.region, a.universe, a.delay,
                   a.neutralization, a.decay, a.truncation, a.expression, a.sharpe,
                   a.fitness, a.turnover, a.returns, a.drawdown, a.margin,
                   a.operator_count, a.k_ratio, {ALPHA_METRICS["calmar"]} AS calmar,
                   a.date_created, a.date_submitted,
                   EXISTS (SELECT 1 FROM alpha_pnl p WHERE p.alpha_id = a.alpha_id) AS has_pnl
            FROM alpha a
            WHERE {where}
            ORDER BY {order} {direction} NULLS LAST, a.alpha_id
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        return {"total": int(total or 0), "results": [_page_row(r) for r in rows]}

    async def k_ratio(self, alpha_id: str) -> float | None:
        """K-Ratio from the stored daily series, cached on the alpha row."""
        rows = await self.catalog.query(
            "SELECT pnl FROM alpha_pnl WHERE alpha_id = ? ORDER BY date", [alpha_id]
        )
        value = k_ratio([float(r["pnl"]) for r in rows])
        if value is not None:
            await self.catalog.upsert("alpha", ("alpha_id", "k_ratio"), [(alpha_id, value)])
        return value

    async def series_length(self, alpha_id: str) -> int:
        value = await self.catalog.scalar(
            "SELECT count(*) FROM alpha_pnl WHERE alpha_id = ?", [alpha_id]
        )
        return int(value or 0)

    async def train_pnl(self, alpha_ids: list[str]) -> dict[str, dict[date, float]]:
        """Each Alpha's daily PnL before its last two years, which a train/test split holds out.

        Only Alphas with a stored series appear.
        """
        if not alpha_ids:
            return {}
        placeholders = ", ".join("?" for _ in alpha_ids)
        rows = await self.catalog.query(
            f"""
            SELECT p.alpha_id, p.date, p.pnl FROM alpha_pnl p
            JOIN (
                SELECT alpha_id, max(date) - INTERVAL 2 YEAR AS cutoff FROM alpha_pnl
                WHERE alpha_id IN ({placeholders}) GROUP BY alpha_id
            ) c ON p.alpha_id = c.alpha_id
            WHERE p.date < c.cutoff
            """,
            list(alpha_ids),
        )
        out: dict[str, dict[date, float]] = {}
        for row in rows:
            out.setdefault(str(row["alpha_id"]), {})[row["date"]] = float(row["pnl"] or 0.0)
        return out

    async def alphas(
        self,
        *,
        region: str | None = None,
        delay: int | None = None,
        universe: str | None = None,
        instrument_type: str | None = None,
        min_sharpe: float | None = None,
        with_returns: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["1 = 1"]
        params: list[Any] = []
        for column, value in (
            ("region", region),
            ("delay", delay),
            ("universe", universe),
            ("instrument_type", instrument_type),
        ):
            if value is not None:
                clauses.append(f"a.{column} = ?")
                params.append(value)
        if min_sharpe is not None:
            clauses.append("abs(a.sharpe) >= ?")
            params.append(min_sharpe)
        if with_returns:
            clauses.append("EXISTS (SELECT 1 FROM alpha_pnl p WHERE p.alpha_id = a.alpha_id)")

        return await self.catalog.query(
            f"""
            SELECT a.*,
                   (SELECT count(*) FROM alpha_pnl p WHERE p.alpha_id = a.alpha_id) AS days
            FROM alpha a
            WHERE {" AND ".join(clauses)}
            ORDER BY a.sharpe DESC NULLS LAST
            LIMIT ?
            """,
            [*params, limit],
        )

    async def by_ids(self, alpha_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Look several alphas up at once, keyed by id."""
        if not alpha_ids:
            return {}
        placeholders = ", ".join("?" for _ in alpha_ids)
        rows = await self.catalog.query(
            f"SELECT * FROM alpha WHERE alpha_id IN ({placeholders})", list(alpha_ids)
        )
        return {str(r["alpha_id"]): r for r in rows}

    async def local_sharpe(self, alpha_id: str) -> float | None:
        """Sharpe recomputed from the stored series.

        Used to check the stored series against the platform's own figure — they agree
        to within 0.01, which is what licenses predicting a mix before running it.
        """
        rows = await self.catalog.query(
            f"""
            SELECT avg(pnl) / nullif(stddev_samp(pnl), 0) * sqrt({TRADING_DAYS}) AS sharpe
            FROM alpha_pnl WHERE alpha_id = ?
            """,
            [alpha_id],
        )
        value = rows[0]["sharpe"] if rows else None
        return float(value) if value is not None else None
