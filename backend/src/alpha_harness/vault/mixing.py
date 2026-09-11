"""Finding groups worth mixing, and predicting them before they cost anything.

The idea in one sentence: **several mediocre alphas that move independently combine into
a better one than any of them.** Adding independent signals adds their means while
adding their variances in quadrature, so two Sharpe-1 alphas with zero correlation give
roughly 1.4 and four give roughly 2.0 — the difference between unsubmittable and
submittable.

What makes it practical rather than theoretical is that **nothing has to be simulated to
find out**. The stored daily series reproduces the platform's own Sharpe to within 0.01,
so a group's combined series can simply be added up and measured. Thousands of
candidates are ranked offline and only the best few ever cost quota.

Three things keep the suggestions honest.

**Scope, and neutralization with it.** Every member must share instrument type, region,
delay and universe — mixing a USA alpha with a CHN one is not a trade. Neutralization
has to match too, and for a less obvious reason: neutralization is a linear operation,
so ``neutralize(a + b) == neutralize(a) + neutralize(b)`` *when both were neutralized
the same way*. That identity is what licenses predicting the mix from the two finished
PnL series. Mix an industry-neutral alpha with a market-neutral one and the prediction
quietly stops meaning anything, so by default it is not offered.

**Uplift, not headline Sharpe.** Adding a weak signal to a strong one produces a
respectable-looking number that is still worse than the strong alpha alone. Groups are
ranked and filtered by how much they beat their own best member.

**Whether it could actually be submitted.** The platform's self-correlation test is
computable here: a candidate passes if its PnL correlates below 0.7 with every alpha you
have already submitted, or if its Sharpe is at least 10% higher than each one it exceeds
that against. Both are checked before a simulation is spent.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import structlog

from ..db.duck import Catalog
from .combine import Strategy, build, recommend
from .store import TRADING_DAYS

log = structlog.get_logger(__name__)

#: Groups are formed from alphas at or above this Sharpe.
DEFAULT_MIN_SHARPE = 0.7

#: Above this, two alphas are effectively the same signal and mixing buys nothing.
DEFAULT_MAX_CORRELATION = 0.5

#: A mix is only worth a simulation if it beats the better of its parts.
DEFAULT_MIN_UPLIFT = 0.0

#: Days two alphas must share before their correlation means anything.
MIN_OVERLAP = 250

#: The platform's self-correlation cutoff, and the margin that excuses exceeding it.
SELF_CORRELATION_LIMIT = 0.7
SHARPE_MARGIN = 1.10

#: How many alphas to consider. Groups grow combinatorially, so the population is capped
#: and the search beyond pairs is greedy rather than exhaustive.
MAX_POPULATION = 200
#: Pairs kept as seeds when growing to three and four members.
BEAM = 40


@dataclass(slots=True)
class Series:
    """One alpha's daily returns, aligned to a shared calendar."""

    alpha_id: str
    values: list[float]
    sharpe: float
    expression: str | None = None
    submitted: bool = False
    neutralization: str | None = None


def sharpe_of(values: list[float]) -> float:
    """Annualised Sharpe, measured the way the platform measures it.

    Zero for the degenerate cases — fewer than two points, or a series that never
    moves — rather than the exception ``statistics`` raises. Callers reduce over these
    with ``max()``, and a flat series is an ordinary thing to find in the vault.
    """
    if len(values) < 2:
        return 0.0
    try:
        deviation = statistics.stdev(values)
    except statistics.StatisticsError:
        return 0.0
    if deviation <= 0:
        return 0.0
    return statistics.fmean(values) / deviation * math.sqrt(TRADING_DAYS)


def correlation(left: list[float], right: list[float]) -> float:
    """Pearson correlation of two aligned series.

    Same zero-rather-than-raise contract as :func:`sharpe_of`. A length mismatch is the
    exception: two series that do not line up is a bug in the caller, not a degenerate
    input, so it stays loud.
    """
    if len(left) != len(right):
        raise ValueError("correlation needs two series of the same length")
    if len(left) < 2:
        return 0.0
    try:
        return statistics.correlation(left, right)
    except statistics.StatisticsError:
        return 0.0


@dataclass(slots=True)
class SelfCorrelation:
    """Whether a candidate would pass the platform's self-correlation test."""

    max_correlation: float | None
    against: str | None
    passes: bool
    #: Set when it exceeds the cutoff but clears it on the Sharpe margin instead.
    passes_on_margin: bool = False
    reason: str = ""
    compared: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxCorrelation": (
                round(self.max_correlation, 4) if self.max_correlation is not None else None
            ),
            "against": self.against,
            "passes": self.passes,
            "passesOnMargin": self.passes_on_margin,
            "reason": self.reason,
            "compared": self.compared,
        }


@dataclass(slots=True)
class MixGroup:
    """Several alphas worth combining, and what the combination would be."""

    members: list[str]
    sharpes: list[float]
    expressions: list[str | None]
    combined_sharpe: float
    #: Largest absolute correlation between any two members.
    max_pair_correlation: float
    overlap: int
    #: Shared by every member, by construction. The combined alpha inherits it.
    neutralization: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    self_correlation: SelfCorrelation | None = None

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def best_member(self) -> float:
        return max(self.sharpes) if self.sharpes else 0.0

    @property
    def uplift(self) -> float:
        return self.combined_sharpe - self.best_member

    def expression(self, strategy: Strategy | None = None) -> str | None:
        parts = [e for e in self.expressions if e]
        if len(parts) != len(self.members):
            return None
        chosen = strategy or recommend(parts).strategy
        try:
            return build(parts, strategy=chosen)
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        parts = [e for e in self.expressions if e]
        advice = recommend(parts) if len(parts) == len(self.members) else None
        return {
            "members": self.members,
            "size": self.size,
            "sharpes": [round(s, 3) for s in self.sharpes],
            "expressions": self.expressions,
            "combinedSharpe": round(self.combined_sharpe, 3),
            "bestMember": round(self.best_member, 3),
            "uplift": round(self.uplift, 3),
            "maxPairCorrelation": round(self.max_pair_correlation, 4),
            "overlap": self.overlap,
            "neutralization": self.neutralization,
            "scope": self.scope,
            "combine": advice.to_dict() if advice else None,
            "expression": self.expression(),
            "selfCorrelation": self.self_correlation.to_dict() if self.self_correlation else None,
        }


class Mixer:
    """Correlations, mix candidates, and the expressions that realise them."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    # -- loading ---------------------------------------------------------

    async def load(
        self,
        *,
        region: str,
        delay: int,
        universe: str,
        instrument_type: str = "EQUITY",
        neutralization: str | None = None,
        min_sharpe: float = DEFAULT_MIN_SHARPE,
        limit: int = MAX_POPULATION,
        include_submitted: bool = True,
    ) -> tuple[list[Series], list[str]]:
        """Load one scope's alphas with their series aligned to shared dates.

        Aligned once, in memory: a group of four needs the sum of four series, and doing
        that in SQL means a four-way self-join per candidate. A hundred alphas is about
        a quarter of a million floats, which is nothing.
        """
        clauses = [
            "a.instrument_type = ?",
            "a.region = ?",
            "a.delay = ?",
            "a.universe = ?",
            "a.sharpe IS NOT NULL",
            "abs(a.sharpe) >= ?",
        ]
        params: list[Any] = [instrument_type, region, delay, universe, min_sharpe]
        if neutralization is not None:
            clauses.append("a.neutralization = ?")
            params.append(neutralization)

        rows = await self.catalog.query(
            f"""
            SELECT a.alpha_id, a.expression, a.sharpe, a.status, a.neutralization
            FROM alpha a
            WHERE {" AND ".join(clauses)}
              AND EXISTS (SELECT 1 FROM alpha_pnl p WHERE p.alpha_id = a.alpha_id)
            ORDER BY abs(a.sharpe) DESC
            LIMIT ?
            """,
            [*params, limit],
        )
        if not rows:
            return [], []

        ids = [str(r["alpha_id"]) for r in rows]
        placeholders = ", ".join("?" for _ in ids)
        pnl = await self.catalog.query(
            f"""
            SELECT alpha_id, date, pnl FROM alpha_pnl
            WHERE alpha_id IN ({placeholders})
            ORDER BY date
            """,
            ids,
        )

        by_alpha: dict[str, dict[Any, float]] = {i: {} for i in ids}
        for row in pnl:
            by_alpha[str(row["alpha_id"])][row["date"]] = float(row["pnl"] or 0.0)

        # A shared calendar. Comparing series over different days would make the
        # correlations meaningless, so only days every member has are used.
        common: set[Any] | None = None
        for values in by_alpha.values():
            common = set(values) if common is None else common & set(values)
        dates = sorted(common or [])
        if len(dates) < MIN_OVERLAP:
            return [], dates

        series = [
            Series(
                alpha_id=str(r["alpha_id"]),
                values=[by_alpha[str(r["alpha_id"])][d] for d in dates],
                sharpe=float(r["sharpe"]),
                expression=r.get("expression"),
                submitted=_is_submitted(r.get("status")),
                neutralization=r.get("neutralization"),
            )
            for r in rows
        ]
        if not include_submitted:
            series = [s for s in series if not s.submitted]
        return series, dates

    # -- searching -------------------------------------------------------

    async def candidates(
        self,
        *,
        region: str,
        delay: int,
        universe: str,
        instrument_type: str = "EQUITY",
        neutralization: str | None = None,
        sizes: list[int] | None = None,
        min_sharpe: float = DEFAULT_MIN_SHARPE,
        max_correlation: float = DEFAULT_MAX_CORRELATION,
        min_uplift: float | None = DEFAULT_MIN_UPLIFT,
        population: int = 120,
        limit: int = 40,
        include_submitted: bool = True,
        check_self_correlation: bool = True,
    ) -> dict[str, Any]:
        """Groups worth spending a simulation on, best first.

        Pairs are exhaustive. Three and four members are grown greedily from the best
        pairs — the exhaustive count is in the millions for a population of a hundred,
        and the best group of four almost always contains a good pair.
        """
        wanted = sorted({s for s in (sizes or [2]) if 2 <= s <= 4})
        series, dates = await self.load(
            region=region,
            delay=delay,
            universe=universe,
            instrument_type=instrument_type,
            neutralization=neutralization,
            min_sharpe=min_sharpe,
            limit=min(population, MAX_POPULATION),
            include_submitted=include_submitted,
        )
        scope = {
            "instrumentType": instrument_type,
            "region": region,
            "delay": delay,
            "universe": universe,
            "neutralization": neutralization,
        }
        if len(series) < 2:
            return {
                "groups": [],
                "population": len(series),
                "overlap": len(dates),
                "scope": scope,
                "note": _sparse_note(len(series), len(dates)),
            }

        overlap = len(dates)
        pairs = self._pairs(series, max_correlation)
        found: list[MixGroup] = []

        if 2 in wanted:
            found.extend(g for _, g in pairs)
        for size in (s for s in wanted if s > 2):
            found.extend(self._grow(series, pairs, size, max_correlation, overlap, scope))

        for group in found:
            group.scope = scope

        if min_uplift is not None:
            found = [g for g in found if g.uplift >= min_uplift]
        found.sort(key=lambda g: (g.uplift, -g.max_pair_correlation), reverse=True)
        found = found[:limit]

        if check_self_correlation and found:
            await self._score_self_correlation(found, series, dates, scope)

        log.info(
            "vault.mix_candidates",
            scope=f"{region}/D{delay}/{universe}",
            population=len(series),
            sizes=wanted,
            groups=len(found),
        )
        return {
            "groups": [g.to_dict() for g in found],
            "population": len(series),
            "overlap": overlap,
            "scope": scope,
            "note": None if found else _no_candidates_note(len(series), pairs),
        }

    def _pairs(self, series: list[Series], max_correlation: float) -> list[tuple[float, MixGroup]]:
        """Every pair below the correlation cutoff, ranked by uplift."""
        out: list[tuple[float, MixGroup]] = []
        for left, right in combinations(series, 2):
            # Neutralization must match even when the caller did not pin one. It is a
            # linear operation, so predicting the mix from finished PnL series only
            # holds when both were neutralized the same way — and offering a group that
            # `run` would then refuse is worse than not offering it.
            if left.neutralization != right.neutralization:
                continue
            corr = correlation(left.values, right.values)
            if abs(corr) > max_correlation:
                continue
            combined = sharpe_of([a + b for a, b in zip(left.values, right.values, strict=True)])
            group = MixGroup(
                members=[left.alpha_id, right.alpha_id],
                sharpes=[left.sharpe, right.sharpe],
                expressions=[left.expression, right.expression],
                combined_sharpe=combined,
                max_pair_correlation=abs(corr),
                overlap=len(left.values),
                neutralization=left.neutralization,
            )
            out.append((group.uplift, group))
        out.sort(key=lambda entry: entry[0], reverse=True)
        return out

    def _grow(
        self,
        series: list[Series],
        pairs: list[tuple[float, MixGroup]],
        size: int,
        max_correlation: float,
        overlap: int,
        scope: dict[str, Any],
    ) -> list[MixGroup]:
        """Extend the best pairs to ``size`` members, one alpha at a time.

        Greedy: exhaustive search over four-member groups of a hundred alphas is four
        million combinations, and the best of them nearly always contains a good pair.
        """
        by_id = {s.alpha_id: s for s in series}
        current = [g for _, g in pairs[:BEAM]]

        for _ in range(size - 2):
            grown: dict[tuple[str, ...], MixGroup] = {}
            for group in current:
                members = [by_id[m] for m in group.members]
                base = [sum(v) for v in zip(*(m.values for m in members), strict=True)]

                for candidate in series:
                    if candidate.alpha_id in group.members:
                        continue
                    if candidate.neutralization != members[0].neutralization:
                        continue
                    worst = max(abs(correlation(candidate.values, m.values)) for m in members)
                    if worst > max_correlation:
                        continue

                    key = tuple(sorted([*group.members, candidate.alpha_id]))
                    if key in grown:
                        continue
                    combined = sharpe_of(
                        [a + b for a, b in zip(base, candidate.values, strict=True)]
                    )
                    grown[key] = MixGroup(
                        members=[*group.members, candidate.alpha_id],
                        sharpes=[*group.sharpes, candidate.sharpe],
                        expressions=[*group.expressions, candidate.expression],
                        combined_sharpe=combined,
                        max_pair_correlation=max(group.max_pair_correlation, worst),
                        overlap=overlap,
                        scope=scope,
                        neutralization=group.neutralization,
                    )

            current = sorted(grown.values(), key=lambda g: g.uplift, reverse=True)[:BEAM]
            if not current:
                break

        return current

    async def _score_self_correlation(
        self,
        groups: list[MixGroup],
        series: list[Series],
        dates: list[Any],
        scope: dict[str, Any],
    ) -> None:
        """Would each candidate pass the platform's self-correlation test?

        Computable here because the test is a PnL correlation against alphas already
        submitted, and those series are stored. Passing needs correlation below 0.7 with
        every one, or a Sharpe at least 10% above each it exceeds that against.
        """
        rows = await self.catalog.query(
            """
            SELECT a.alpha_id, a.sharpe FROM alpha a
            WHERE a.instrument_type = ? AND a.region = ? AND a.delay = ? AND a.universe = ?
              AND a.status IS NOT NULL AND upper(a.status) <> 'UNSUBMITTED'
              AND EXISTS (SELECT 1 FROM alpha_pnl p WHERE p.alpha_id = a.alpha_id)
            """,
            [scope["instrumentType"], scope["region"], scope["delay"], scope["universe"]],
        )
        if not rows:
            for group in groups:
                group.self_correlation = SelfCorrelation(
                    max_correlation=None,
                    against=None,
                    passes=True,
                    reason=(
                        "You have no submitted alphas in this scope yet, so there is "
                        "nothing for a new one to correlate against."
                    ),
                    compared=0,
                )
            return

        ids = [str(r["alpha_id"]) for r in rows]
        sharpes = {str(r["alpha_id"]): float(r["sharpe"] or 0.0) for r in rows}
        placeholders = ", ".join("?" for _ in ids)
        pnl = await self.catalog.query(
            f"SELECT alpha_id, date, pnl FROM alpha_pnl WHERE alpha_id IN ({placeholders})",
            ids,
        )
        lookup: dict[str, dict[Any, float]] = {i: {} for i in ids}
        for row in pnl:
            lookup[str(row["alpha_id"])][row["date"]] = float(row["pnl"] or 0.0)

        aligned = {
            alpha_id: [values[d] for d in dates]
            for alpha_id, values in lookup.items()
            if all(d in values for d in dates)
        }
        by_id = {s.alpha_id: s for s in series}

        for group in groups:
            mixed = [sum(v) for v in zip(*(by_id[m].values for m in group.members), strict=True)]
            worst, worst_id = 0.0, None
            breaches: list[str] = []
            for alpha_id, values in aligned.items():
                corr = abs(correlation(mixed, values))
                if corr > worst:
                    worst, worst_id = corr, alpha_id
                if corr > SELF_CORRELATION_LIMIT:
                    breaches.append(alpha_id)

            if not breaches:
                group.self_correlation = SelfCorrelation(
                    max_correlation=worst,
                    against=worst_id,
                    passes=True,
                    reason=(
                        f"Highest correlation with anything you have submitted is "
                        f"{worst:.2f}, below the {SELF_CORRELATION_LIMIT} cutoff."
                    ),
                    compared=len(aligned),
                )
                continue

            # The escape: a correlated alpha still qualifies if it is at least 10%
            # better than every alpha it correlates with.
            beats_all = all(
                group.combined_sharpe >= sharpes.get(a, 0.0) * SHARPE_MARGIN for a in breaches
            )
            group.self_correlation = SelfCorrelation(
                max_correlation=worst,
                against=worst_id,
                passes=beats_all,
                passes_on_margin=beats_all,
                reason=(
                    f"Correlates {worst:.2f} with {worst_id}, above the "
                    f"{SELF_CORRELATION_LIMIT} cutoff, but its predicted Sharpe is more "
                    f"than 10% higher than every alpha it exceeds that against — which "
                    "the platform accepts."
                    if beats_all
                    else (
                        f"Correlates {worst:.2f} with {worst_id}, above the "
                        f"{SELF_CORRELATION_LIMIT} cutoff, and is not 10% better than it. "
                        "The platform would reject this on self-correlation."
                    )
                ),
                compared=len(aligned),
            )

    # -- the full grid ---------------------------------------------------

    async def correlation_matrix(
        self,
        *,
        region: str,
        delay: int,
        universe: str,
        instrument_type: str = "EQUITY",
        neutralization: str | None = None,
        min_sharpe: float = DEFAULT_MIN_SHARPE,
        limit: int = 60,
    ) -> dict[str, Any]:
        """Pairwise correlation of daily returns within one scope."""
        series, dates = await self.load(
            region=region,
            delay=delay,
            universe=universe,
            instrument_type=instrument_type,
            neutralization=neutralization,
            min_sharpe=min_sharpe,
            limit=limit,
        )
        if len(series) < 2:
            return {"alphas": [], "pairs": [], "note": _sparse_note(len(series), len(dates))}

        pairs = [
            {
                "left_id": left.alpha_id,
                "right_id": right.alpha_id,
                "correlation": round(correlation(left.values, right.values), 4),
                "overlap": len(dates),
            }
            for left, right in combinations(series, 2)
        ]
        return {
            "alphas": [
                {
                    "alpha_id": s.alpha_id,
                    "sharpe": s.sharpe,
                    "expression": s.expression,
                    "submitted": s.submitted,
                }
                for s in series
            ],
            "pairs": pairs,
            "overlap": len(dates),
            "note": None,
        }


def _is_submitted(status: Any) -> bool:
    return bool(status) and str(status).upper() != "UNSUBMITTED"


def _sparse_note(population: int, overlap: int) -> str:
    if population == 0:
        return (
            "No alphas here have their daily returns stored yet. Run the returns "
            "backfill, or simulate something in this scope first."
        )
    if population == 1:
        return "Only one alpha here has stored returns. Mixing needs at least two."
    return (
        f"These alphas share only {overlap} trading days, which is too few for a "
        f"correlation to mean anything (at least {MIN_OVERLAP} are needed)."
    )


def _no_candidates_note(population: int, pairs: list[tuple[float, MixGroup]]) -> str:
    """Why a scope produced nothing, in terms of what to do about it."""
    if not pairs:
        return (
            f"No two of the {population} alphas here can be mixed: either they move "
            "together — variations on one idea, which mixing does not help — or they "
            "were neutralized differently, which makes the combination unpredictable. "
            "Run a harvest across different datasets, with one neutralization, to get "
            "genuinely different signals to mix."
        )
    return (
        f"None of the {len(pairs)} uncorrelated pairs beats the better of its two "
        "alphas. That usually means one strong alpha and several weaker ones: adding a "
        "weak signal to a strong one dilutes it. More independent alphas, not looser "
        "thresholds, is the way through."
    )
