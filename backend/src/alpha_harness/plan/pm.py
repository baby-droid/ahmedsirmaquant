"""The PM: a model that decides which desks get compute today.

A consultant account is a hedge fund. The user is the CEO — they approve how the day is
spent and leave. The research labs are the desks. Compute is the capital: eight cores and
five thousand simulations, gone at midnight. This module is the portfolio manager sitting
between them.

**It runs once or twice a day, not per click.** Its value is judgement, and judgement does
not need to be continuous. Every other part of this product is deterministic precisely so
that this one call can be the only expensive thing in the day.

**What it optimises is Yield Rate**, not Sharpe — submittable alphas per simulation spent.
To a consultant with a fixed daily allowance, a lab returning three brilliant alphas from
two thousand simulations is worse than one returning six adequate alphas from one
thousand. Ranking desks by their best result would fund the expensive one forever;
ranking by conversion funds the one that actually turns capital into product.

**It cannot break the product.** Every allocation it returns is validated against the real
lab set and the real allowance before it becomes work. A lab it invented is dropped. A
lab that cannot run today is dropped. An answer that survives none of that falls back to
an even split across whatever is ready. **The floor of this product is a good ordinary
plan** — the PM is upside, never a dependency.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..labs.registry import LABS, LABS_BY_ID, LabRegistry
from ..llm.service import LLMService
from ..schemas import loads_or
from .day import ASSUMED_DAILY, _split
from .yields import TARGET_YIELD, YieldBook

log = structlog.get_logger(__name__)

#: How many desks can meaningfully share eight cores. Below one core a lab does not run;
#: spreading thinner buys diversity that the levers inside each lab already provide.
MAX_FUNDED = 4

ALLOCATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "briefing": {"type": "string"},
        "allocations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Desks only. A tool aims the other labs rather than being a
                    # direction of research, so it can never be handed cores.
                    "lab": {
                        "type": "string",
                        "enum": [lab.id for lab in LABS if lab.kind == "lab"],
                    },
                    "cores": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["lab", "cores", "why"],
            },
        },
    },
    "required": ["briefing", "allocations"],
}


SYSTEM = """\
You are the portfolio manager of a small quantitative hedge fund. The fund has eight \
cores and a fixed number of simulations that expire tonight, unused or not. Your job is \
to divide the cores between research desks for today.

The person reading you is the CEO. They have no background in finance, will give this two \
minutes, and have stopped believing this works. Write to them in short, plain sentences. \
No jargon. Never "simply" or "just". Never promise a result.

What you are optimising is YIELD: submittable alphas per simulation spent. A desk that \
returns three excellent alphas from 2,000 simulations is worse than one returning six \
adequate alphas from 1,000, because the simulations are the thing that runs out.

How to think:
  - Fund what has been converting. Defund what has had a fair run and has not.
  - A desk marked "not confident" has too little evidence to judge. Give it a chance \
rather than writing it off on a handful of runs.
  - Do not put everything on one desk. Alphas that resemble each other cannot all be \
submitted, so spreading across different DIRECTIONS of research is worth real compute.
  - Prefer a desk that searches an axis nothing else is searching today.

Choose ONLY from the desks listed. Never invent one. Fund at most four. Cores are whole \
numbers and must add up to exactly the number of cores available.

Return JSON:
  "briefing"    — two or three sentences to the CEO. What today is going after, and why \
that is a reasonable bet given what has happened lately. Speak to them, not about them.
  "allocations" — the desks you are funding, each with its cores and one plain sentence \
saying why it is getting them.
"""


class PortfolioManager:
    """Allocates the day's cores across research desks."""

    def __init__(self, registry: LabRegistry, yields: YieldBook, llm: LLMService) -> None:
        self.registry = registry
        self.yields = yields
        self.llm = llm

    async def brief(self, *, days: int = 14) -> dict[str, Any]:
        """Everything the PM is shown. Useful on its own — this is the fund's report."""
        listing = await self.registry.list()
        book = await self.yields.by_lab(days=days)
        summary = await self.yields.summary(days=days)

        desks = []
        for entry in listing.labs:
            if entry.kind != "lab":
                continue
            record = book.get(entry.id)
            desks.append(
                {
                    "id": entry.id,
                    "name": entry.name,
                    "searches": entry.searches,
                    "ready": entry.ready,
                    "blockedBy": [b.missing for b in entry.blockers],
                    "record": record.to_dict() if record else None,
                }
            )
        return {"desks": desks, "fund": summary}

    async def allocate(
        self,
        *,
        cores: int,
        target: int = ASSUMED_DAILY,
        model: str | None = None,
        days: int = 14,
    ) -> dict[str, Any]:
        """Today's core allocation across desks, with the reasoning behind it."""
        brief = await self.brief(days=days)
        fundable = [d for d in brief["desks"] if d["ready"]]

        if not fundable:
            return {
                "briefing": (
                    "There is nothing that can run yet. Once a market has finished "
                    "downloading, the desks open up."
                ),
                "allocations": [],
                "fromPM": False,
                "fund": brief["fund"],
            }

        try:
            answer = await self.llm.generate(
                system=SYSTEM,
                user=_prompt(brief, fundable, cores, target),
                model_id=model,
                temperature=0.8,
                response_schema=ALLOCATION_SCHEMA,
                thinking="MEDIUM",
            )
        except Exception as exc:
            # The assistant is upside. Losing it costs the reasoning, never the day.
            log.warning("pm.unavailable", error=str(exc)[:200])
            return {**self._even_split(fundable, cores), "fund": brief["fund"]}

        parsed = _parse(answer.text)
        allocations, rejected = self._validate(parsed["allocations"], fundable, cores)

        if not allocations:
            log.warning("pm.no_usable_allocation", rejected=len(rejected))
            return {**self._even_split(fundable, cores), "fund": brief["fund"]}

        return {
            "briefing": parsed["briefing"],
            "allocations": allocations,
            "fromPM": True,
            "rejected": rejected,
            "fund": brief["fund"],
            "usage": answer.to_dict()["usage"],
            "model": answer.model,
        }

    # -- keeping it honest -----------------------------------------------

    def _validate(
        self, raw: list[dict[str, Any]], fundable: list[dict[str, Any]], cores: int
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Drop anything invented or unrunnable, then make the cores add up.

        Rebalancing rather than rejecting: a PM that named four real desks and miscounted
        the cores has still done the useful part of the job, and throwing that away for an
        arithmetic slip would lose the reasoning the CEO is actually here to read.
        """
        allowed = {d["id"] for d in fundable}
        kept: list[dict[str, Any]] = []
        rejected: list[str] = []

        for item in raw:
            lab_id = str(item.get("lab") or "")
            if lab_id not in allowed or any(k["lab"] == lab_id for k in kept):
                rejected.append(lab_id or "(unnamed)")
                continue
            kept.append({"lab": lab_id, "why": str(item.get("why") or "").strip()})
            if len(kept) >= MAX_FUNDED:
                break

        if not kept:
            return [], rejected

        for entry, share in zip(kept, _split(cores, len(kept)), strict=True):
            entry["cores"] = share
            entry["name"] = LABS_BY_ID[entry["lab"]].name
        return kept, rejected

    def _even_split(self, fundable: list[dict[str, Any]], cores: int) -> dict[str, Any]:
        """The floor: spread the cores across whatever can run.

        Chosen by widest coverage of *directions* rather than by past results, because
        this path runs exactly when there is no judgement available to apply.
        """
        chosen = fundable[:MAX_FUNDED]
        shares = _split(cores, len(chosen))
        return {
            "briefing": (
                "Today is split evenly across the research that is ready to run. Each one "
                "looks in a different place, so they are not competing for the same finds."
            ),
            "allocations": [
                {
                    "lab": desk["id"],
                    "name": desk["name"],
                    "cores": share,
                    "why": f"Searches {desk['searches']}.",
                }
                for desk, share in zip(chosen, shares, strict=True)
            ],
            "fromPM": False,
        }


def _prompt(brief: dict[str, Any], fundable: list[dict[str, Any]], cores: int, target: int) -> str:
    lines = ["THE DESKS YOU MAY FUND:"]
    for desk in fundable:
        record = desk["record"]
        if record and record["finished"]:
            note = (
                f"{record['submittable']} submittable from {record['finished']} finished "
                f"(yield {record['yieldRate'] * 100:.2f}%)"
                + ("" if record["confident"] else ", NOT CONFIDENT — too little evidence yet")
            )
        else:
            note = "no record yet — never funded, or nothing has finished"
        lines.append(f"  {desk['id']}: searches {desk['searches']}. Lately: {note}.")

    idle = [d for d in brief["desks"] if not d["ready"]]
    if idle:
        lines.append("\nCLOSED TODAY (do not fund): " + ", ".join(d["id"] for d in idle))

    fund = brief["fund"]
    return "\n".join(
        [
            *lines,
            "",
            f"THE FUND OVERALL: {fund['plainly']} "
            f"The standard to beat is {TARGET_YIELD * 100:.1f}% "
            f"({'met' if fund['meetsTarget'] else 'not met'}).",
            "",
            f"YOU HAVE {cores} CORES AND ABOUT {target:,} SIMULATIONS FOR TODAY.",
        ]
    )


def _parse(text: str) -> dict[str, Any]:
    payload = loads_or(text)
    raw = payload.get("allocations")
    return {
        "briefing": str(payload.get("briefing") or "").strip(),
        "allocations": [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else [],
    }
