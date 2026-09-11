"""Repair: the fix for what a near miss failed.

Most alphas do not fail because the idea was wrong. They fail on one measurable thing —
they trade too much, they only work on small companies, too much of the money sits in a
handful of stocks, they look too much like something already submitted. Across the
reference material the same three checks account for most rejections, and every one of
them has a documented remedy that leaves the idea intact.

That is the axis this lab searches: **the fix for the specific check that failed**, with
the idea and its data field held still. Every other lab looks for a new idea; this one
takes an idea that nearly worked and asks what the platform actually objected to. It is
the cheapest research in the product, because the expensive part — finding a signal —
has already happened.

**One change at a time.** Each repair alters exactly the thing the check complained
about. Stacking two fixes would leave nobody able to say which one worked, and the
platform's verdict is the only teacher here.

**A shape the market has already answered is not repaired.** If a hundred alphas of this
shape have been tried and none passed, the failure is the shape and no wrapper fixes it.
The skeleton ledger says which, and those seeds are skipped with the reason reported.

**Nothing is repaired twice.** An alpha already wrapped in a repair is left alone:
``ts_decay_linear(ts_decay_linear(x, 5), 10)`` is not a second idea, it is the first idea
with its window quietly doubled, and Deepen owns that axis.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from ..catalog.queries import Tuple4
from ..plan.yields import FIXABLE_CHECKS, failed_checks, judged_results
from ..templates.validate import (
    POWER_POOL_MAX_FIELDS,
    POWER_POOL_MAX_OPERATORS,
    count_operators,
    unique_data_fields,
)
from ..vault.mixing import correlation
from ..vault.store import AlphaVault

log = structlog.get_logger(__name__)

#: Never more than this in one go, matching the other labs.
MAX_SIMULATIONS = 5_000

#: How many near misses to read. Each one yields a handful of repairs, so this is
#: already more seeds than a day's allowance can work through.
SEEDS = 40

#: A reliably negative Sharpe is a signal with its sign the wrong way round, and the sign
#: is free to change. Set well below zero: a Sharpe of -0.2 is noise, not an inverted
#: signal, and flipping it produces another -0.2.
FLIP_SHARPE = -1.25

#: Wrappers this lab adds. An expression already carrying one at the root has been
#: repaired before, and repairing it again is tuning rather than repair.
REPAIR_WRAPPERS = frozenset({"ts_decay_linear", "hump", "trade_when", "vector_neut", "winsorize"})

#: Which failure to work on first when an alpha failed two checks. Sub-universe and
#: fitness come first because their fixes are the ones that most often turn a near miss
#: into a pass without touching what the alpha is measuring.
PRIORITY: tuple[str, ...] = (
    "LOW_SUB_UNIVERSE_SHARPE",
    "LOW_FITNESS",
    "CONCENTRATED_WEIGHT",
    "HIGH_TURNOVER",
    "LOW_TURNOVER",
    "SELF_CORRELATION",
)

#: What each check means, in the words of someone who has never read the documentation.
#: The platform's own name for the check is shown next to it, so the two vocabularies
#: stay attached to each other rather than replacing one another.
SAYS: dict[str, str] = {
    "HIGH_TURNOVER": "It trades too much. Slow it down and it keeps more of what it earns.",
    "LOW_TURNOVER": "It barely trades at all. Make it react faster.",
    "LOW_FITNESS": "It earns too little for the amount of trading it does.",
    "CONCENTRATED_WEIGHT": "Too much of the money sits in a handful of stocks.",
    "LOW_SUB_UNIVERSE_SHARPE": "It works on the smaller companies but not the larger ones.",
    "SELF_CORRELATION": "It is too much like an alpha you already have.",
    "LOW_SHARPE": "It loses money steadily, which is a real signal pointing the wrong way.",
}

#: Where to move neutralization when the platform's own list is not cached. These three
#: exist in every region the platform offers; anything more adventurous is only offered
#: when the settings schema has been read and says it is legal here.
NEXT_NEUTRALIZATION: dict[str, str] = {
    "NONE": "MARKET",
    "MARKET": "INDUSTRY",
    "INDUSTRY": "SUBINDUSTRY",
    "SUBINDUSTRY": "INDUSTRY",
    "SECTOR": "INDUSTRY",
}

_TOP = re.compile(r"^TOP(\d+)U?$")
_LEADING_CALL = re.compile(r"([a-zA-Z_]\w*)\s*\(")


@dataclass(slots=True)
class Repair:
    """One change to one near miss, and what it is meant to fix."""

    #: The alpha being repaired.
    alpha_id: str
    #: BRAIN's own name for the check that failed.
    check: str
    #: Short id for the change itself, for grouping in the UI.
    kind: str
    expression: str
    #: What changed, in plain words.
    changed: str
    #: Settings to patch onto the seed's own. Empty means the expression carries the fix.
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alphaId": self.alpha_id,
            "check": self.check,
            "says": SAYS.get(self.check, ""),
            "kind": self.kind,
            "expression": self.expression,
            "changed": self.changed,
            "settings": self.settings,
        }


# -- the rules -------------------------------------------------------------


def repairs_for(
    row: dict[str, Any],
    check: str,
    *,
    partner: str | None = None,
    universes: Sequence[str] = (),
    neutralizations: Sequence[str] = (),
) -> list[Repair]:
    """Every documented way of answering one failed check on one alpha.

    A pure function of the alpha's row so the whole rule table can be read, tested and
    argued with without a database. ``universes`` and ``neutralizations`` are what the
    platform says is legal in this region; empty means the settings schema has not been
    read yet, and the fixes that need it are simply not offered.
    """
    alpha_id = str(row.get("alpha_id") or "")
    expression = str(row.get("expression") or "")
    decay = int(row.get("decay") or 0)
    if not expression:
        return []

    def make(kind: str, wrapped: str, changed: str, **settings: Any) -> Repair:
        return Repair(alpha_id, check, kind, wrapped, changed, settings)

    out: list[Repair] = []

    if check in {"HIGH_TURNOVER", "LOW_FITNESS"}:
        # Turnover and fitness share a remedy: fitness is returns against the square root
        # of turnover, so trading less raises it even when the signal is unchanged.
        for window in (5, 10, 21):
            out.append(
                make(
                    "decay-linear",
                    _wrap(expression, f"ts_decay_linear({{alpha}}, {window})"),
                    f"averaged over the last {window} days, so it changes its mind less often",
                )
            )
        out.append(
            make(
                "hump",
                _wrap(expression, "hump({alpha}, hump = 0.01)"),
                "small changes in the signal no longer move the money at all",
            )
        )
        out.append(
            make(
                "trade-when",
                _wrap(expression, "trade_when(volume >= ts_mean(volume, 5), {alpha}, -1)"),
                "only trades on days the stock is actually being traded",
            )
        )
        for step in _decay_ladder(decay):
            out.append(make("decay", expression, f"decay {decay} → {step}", decay=step))

    elif check == "LOW_TURNOVER":
        # The mirror image. Decay is what holds a position, so removing it is the first
        # thing to try, and trading the change rather than the level is the second.
        if decay:
            out.append(make("decay", expression, f"decay {decay} → 0", decay=0))
        out.append(
            make(
                "delta",
                _wrap(expression, "ts_delta({alpha}, 5)"),
                "trades the change in the signal rather than its level",
            )
        )

    elif check == "LOW_SUB_UNIVERSE_SHARPE":
        out.append(
            make(
                "group-rank",
                _wrap(expression, "group_rank({alpha}, subindustry)"),
                "compares each company only against others doing the same thing",
            )
        )
        out.append(
            make(
                "size-neutral",
                _wrap(
                    expression,
                    'group_neutralize({alpha}, densify(bucket(rank(cap), range = "0.1, 1, 0.1")))',
                ),
                "removes the tilt towards small companies",
            )
        )

    elif check == "CONCENTRATED_WEIGHT":
        out.append(
            make(
                "rank",
                _wrap(expression, "rank({alpha})"),
                "ranks the stocks instead of using the raw numbers, so no one of them can dominate",
            )
        )
        out.append(
            make(
                "winsorize",
                _wrap(expression, "winsorize({alpha}, std = 4)"),
                "trims the extreme values back to the edge of normal",
            )
        )
        truncation = float(row.get("truncation") or 0.08)
        if truncation > 0.05:
            out.append(
                make(
                    "truncation",
                    expression,
                    f"most a single stock can hold: {truncation:.2f} → 0.05",
                    truncation=0.05,
                )
            )

    elif check == "SELF_CORRELATION":
        if partner:
            out.append(
                make(
                    "vector-neut",
                    _wrap(expression, f"vector_neut({{alpha}}, {partner})"),
                    "the part of this idea your closest existing alpha does not already have",
                )
            )
        narrower = _narrower(str(row.get("universe") or ""), universes)
        if narrower:
            out.append(
                make(
                    "universe",
                    expression,
                    f"run on the {narrower} stocks instead of {row.get('universe')}",
                    universe=narrower,
                )
            )
        moved = _next_neutralization(str(row.get("neutralization") or ""), neutralizations)
        if moved:
            out.append(
                make(
                    "neutralization",
                    expression,
                    f"compared against {moved.lower()} instead of "
                    f"{str(row.get('neutralization') or '').lower()}",
                    neutralization=moved,
                )
            )

    elif check == "LOW_SHARPE":
        out.append(
            make(
                "flip",
                _wrap(expression, "-({alpha})"),
                "the same idea with its sign turned round",
            )
        )

    return [r for r in out if _within_power_pool(r.expression)]


# -- the lab ---------------------------------------------------------------


class Repairer:
    """Turns near misses into the one change each of them needs."""

    def __init__(
        self,
        vault: AlphaVault,
        skeletons: Any = None,
        mixer: Any = None,
        auth: Any = None,
    ) -> None:
        self.vault = vault
        self.skeletons = skeletons
        #: Optional. Only used to find the alpha a self-correlated one is closest to.
        self.mixer = mixer
        #: Optional. Only used to read what settings this region actually allows.
        self.auth = auth

    async def plan(
        self,
        *,
        scope: Tuple4,
        target: int = 300,
        checks: list[str] | None = None,
        seeds: int = SEEDS,
    ) -> dict[str, Any]:
        """The near misses in this market and what to change about each. Costs nothing."""
        target = max(1, min(target, MAX_SIMULATIONS))
        wanted = {c.upper() for c in (checks or [])} or None

        rows = await self.vault.alphas(
            region=scope.region,
            delay=scope.delay,
            universe=scope.universe,
            instrument_type=scope.instrument_type,
            limit=500,
        )
        candidates = [row for row in rows if _failures(row)]
        if not candidates:
            return _nothing(
                "Nothing here has come close enough to be worth repairing yet. This lab "
                "works on alphas that failed one thing and passed the rest, so run a day "
                "of exploring first and come back to what nearly made it."
            )

        candidates, worked_out = await self._drop_worked_out(scope, candidates)
        candidates.sort(key=_rank)
        candidates = candidates[: max(1, seeds)]

        universes, neutralizations = await self._legal(scope)
        partners = await self._partners(scope, candidates)

        planned: list[list[Repair]] = []
        for row in candidates:
            for check in _failures(row):
                if wanted and check not in wanted:
                    continue
                planned.append(
                    repairs_for(
                        row,
                        check,
                        partner=partners.get(str(row["alpha_id"])),
                        universes=universes,
                        neutralizations=neutralizations,
                    )
                )
        repairs = _interleave(planned)[:target]

        by_id = {str(r["alpha_id"]): r for r in candidates}
        requests = [_request(repair, by_id[repair.alpha_id], scope) for repair in repairs]

        log.info(
            "repair.planned",
            scope=scope.label,
            seeds=len(candidates),
            repairs=len(repairs),
            checks=sorted({r.check for r in repairs}),
        )
        return {
            "requests": requests,
            "repairs": [r.to_dict() for r in repairs[:24]],
            "seeds": [_seed_dict(row) for row in candidates[:24]],
            "possible": sum(len(group) for group in planned),
            "nearMisses": len(candidates),
            "checks": sorted({r.check for r in repairs}),
            "workedOut": worked_out,
            "note": (
                f"{len(candidates)} alphas here failed one thing and passed everything "
                "else. Each of these changes exactly what the platform objected to and "
                "leaves the idea alone."
            ),
        }

    # -- what to work on --------------------------------------------------

    async def _drop_worked_out(
        self, scope: Tuple4, candidates: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Leave out seeds whose shape this market has already answered."""
        if self.skeletons is None or not candidates:
            return candidates, []
        try:
            saturated = await self.skeletons.saturated(scope)
        except Exception:
            log.warning("repair.skeletons_unavailable", scope=scope.label, exc_info=True)
            return candidates, []
        if not saturated:
            return candidates, []

        from ..plan.skeletons import skeleton

        shapes = {skeleton(row.get("expression")) for row in candidates}
        keep = [row for row in candidates if skeleton(row.get("expression")) not in saturated]
        # A filter narrows what a lab finds; it must never be the reason it finds nothing.
        if not keep:
            return candidates, []
        return keep, sorted(shapes & set(saturated))

    async def _legal(self, scope: Tuple4) -> tuple[list[str], list[str]]:
        """What the platform says this region allows, or nothing if it has not said."""
        if self.auth is None:
            return [], []
        try:
            schema = await self.auth.cached_settings_schema()
        except Exception:
            schema = None
        if not schema:
            return [], []

        from ..brain.settings_schema import valid_values

        settings = {
            "instrumentType": scope.instrument_type,
            "region": scope.region,
            "delay": scope.delay,
            "universe": scope.universe,
        }
        return (
            [str(v) for v in valid_values(schema, "universe", settings)],
            [str(v) for v in valid_values(schema, "neutralization", settings)],
        )

    async def _partners(self, scope: Tuple4, candidates: list[dict[str, Any]]) -> dict[str, str]:
        """For each self-correlated alpha, the owned alpha it most resembles.

        Only computed when something actually failed self-correlation: it reads every
        stored daily series in the scope, which is wasted work otherwise.
        """
        wanted = {
            str(row["alpha_id"]) for row in candidates if "SELF_CORRELATION" in _failures(row)
        }
        if self.mixer is None or not wanted:
            return {}
        try:
            series, _ = await self.mixer.load(
                region=scope.region,
                delay=scope.delay,
                universe=scope.universe,
                instrument_type=scope.instrument_type,
                min_sharpe=0.0,
                limit=120,
            )
        except Exception:
            log.warning("repair.partners_unavailable", scope=scope.label, exc_info=True)
            return {}

        out: dict[str, str] = {}
        for seed in (s for s in series if s.alpha_id in wanted):
            best, closest = 0.0, None
            for other in series:
                # A partner is inlined whole, so a multi-statement one cannot be used:
                # its working lines would be left behind and the result would not parse.
                if other.alpha_id == seed.alpha_id or not other.expression:
                    continue
                if ";" in other.expression:
                    continue
                value = abs(correlation(seed.values, other.values))
                if value > best:
                    best, closest = value, other.expression
            if closest:
                out[seed.alpha_id] = closest
        return out


# -- helpers ---------------------------------------------------------------


def _failures(row: dict[str, Any]) -> list[str]:
    """Which checks this alpha failed that are worth repairing, best first.

    Empty for anything this lab should not touch: already submitted, already repaired,
    failing something no rewrite can answer, or failing so much that the idea rather than
    the detail is what is wrong.
    """
    status = row.get("status")
    if status is not None and str(status).upper() != "UNSUBMITTED":
        return []
    expression = str(row.get("expression") or "")
    if not expression or _root_operator(expression) in REPAIR_WRAPPERS:
        return []

    results = judged_results(row.get("checks"))
    if results is None or set(results) - {"FAIL", "WARNING", "PASS", "PENDING"}:
        return []

    failed = failed_checks(row.get("checks"))
    sharpe = row.get("sharpe")

    # A reliably negative Sharpe is its own case: the platform reports LOW_SHARPE, which
    # no wrapper fixes, but the sign of an alpha is free to change and a signal that
    # loses steadily is a signal.
    flippable = failed <= {"LOW_SHARPE", "LOW_FITNESS"}
    if sharpe is not None and float(sharpe) <= FLIP_SHARPE and flippable:
        return ["LOW_SHARPE"]

    if failed and failed <= FIXABLE_CHECKS and len(failed) <= 2:
        return sorted(failed, key=lambda c: PRIORITY.index(c) if c in PRIORITY else len(PRIORITY))
    return []


def _rank(row: dict[str, Any]) -> tuple[int, float]:
    """Rarest fixable failure first, then the strongest alpha."""
    failures = _failures(row)
    first = PRIORITY.index(failures[0]) if failures and failures[0] in PRIORITY else len(PRIORITY)
    return (first, -abs(float(row.get("sharpe") or 0.0)))


def _interleave(groups: list[list[Repair]]) -> list[Repair]:
    """One repair from each seed, then the next, until they run out.

    So that a target smaller than everything possible spends itself across many near
    misses rather than on every variation of the first one.
    """
    out: list[Repair] = []
    for index in range(max((len(g) for g in groups), default=0)):
        out.extend(group[index] for group in groups if index < len(group))
    return out


def _wrap(expression: str, template: str) -> str:
    """Wrap an expression, leaving any preceding statements where they are.

    An alpha can be a small program — ``x = ...; group_rank(x, industry)`` — and wrapping
    the whole thing would produce something that does not parse. Only the last statement
    is the alpha.

    Substituted rather than formatted: a Fast Expression uses no braces, but one arriving
    from the platform with a stray brace must not take the whole lab down with a
    ``KeyError``.
    """
    head, separator, last = expression.rpartition(";")
    wrapped = template.replace("{alpha}", last.strip())
    return f"{head}; {wrapped}" if separator else wrapped


def _last(expression: str) -> str:
    """The statement that actually is the alpha."""
    return expression.rpartition(";")[2].strip()


def _root_operator(expression: str) -> str:
    """The operator wrapping the whole expression, or empty if it is not one call."""
    text = _last(expression)
    match = _LEADING_CALL.match(text)
    if not match:
        return ""
    depth = 0
    for index in range(match.end() - 1, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return match.group(1) if index == len(text) - 1 else ""
    return ""


def _decay_ladder(decay: int) -> list[int]:
    """Slower settings to try. Decay averages the signal over that many days, so a
    larger number holds positions longer and trades less."""
    return sorted({max(4, decay * 2), decay + 8, 20} - {decay})


def _next_neutralization(current: str, legal: Sequence[str]) -> str | None:
    """Somewhere else to compare each stock against."""
    moved = NEXT_NEUTRALIZATION.get(current.upper(), "INDUSTRY")
    if moved == current.upper():
        moved = "SUBINDUSTRY"
    if legal and moved not in legal:
        moved = next((v for v in legal if v.upper() != current.upper()), "")
    return moved or None


def _narrower(universe: str, legal: Sequence[str]) -> str | None:
    """The next smaller universe this region actually offers.

    Only ever chosen from the platform's own list. Guessing that TOP1000 exists wherever
    TOP3000 does is wrong in several regions, and an illegal universe fails the
    simulation while still spending the allowance.
    """
    size = _universe_size(universe)
    if size is None or not legal:
        return None
    smaller = [
        (found, name)
        for name in legal
        if (found := _universe_size(name)) is not None and found < size
    ]
    return max(smaller)[1] if smaller else None


def _universe_size(universe: str) -> int | None:
    match = _TOP.match(universe.upper())
    return int(match.group(1)) if match else None


def _within_power_pool(expression: str) -> bool:
    """A repair that pushes an alpha out of the Power Pool has not repaired it."""
    return (
        count_operators(expression) <= POWER_POOL_MAX_OPERATORS
        and len(unique_data_fields(expression)) <= POWER_POOL_MAX_FIELDS
    )


def _request(repair: Repair, row: dict[str, Any], scope: Tuple4) -> SimulationRequest:
    """The repair, under the seed's own settings with the one patch applied.

    Everything else is carried over deliberately. Changing two things at once would
    leave nobody able to say which of them worked.
    """
    settings = {
        "instrumentType": scope.instrument_type,
        "region": scope.region,
        "delay": scope.delay,
        "universe": str(row.get("universe") or scope.universe),
        "neutralization": str(row.get("neutralization") or "SUBINDUSTRY"),
        "decay": int(row.get("decay") or 0),
        "truncation": float(row.get("truncation") or 0.08),
        **repair.settings,
    }
    return SimulationRequest(
        type="REGULAR",
        settings=SimulationSettings(**settings),
        regular=repair.expression,
    )


def _seed_dict(row: dict[str, Any]) -> dict[str, Any]:
    failures = _failures(row)
    return {
        "alphaId": str(row.get("alpha_id") or ""),
        "expression": row.get("expression"),
        "sharpe": row.get("sharpe"),
        "fitness": row.get("fitness"),
        "turnover": row.get("turnover"),
        "failed": failures,
        "says": [SAYS.get(c, "") for c in failures],
    }


def _nothing(note: str) -> dict[str, Any]:
    return {
        "requests": [],
        "repairs": [],
        "seeds": [],
        "possible": 0,
        "nearMisses": 0,
        "checks": [],
        "workedOut": [],
        "note": note,
    }
