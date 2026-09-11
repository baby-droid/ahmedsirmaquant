"""Asking the assistant what to work on today.

The random draw in :mod:`.day` already spreads five hundred consultants across the option
space, which is the requirement that matters. This adds the thing the draw cannot do:
**a reason**. Someone who reads "because nobody has looked at analyst-estimate data in
this market for months" starts believing there is something to find, and belief is the
scarce resource here — these are people who have stopped expecting alphas to work.

It is deliberately narrow. The assistant does not write expressions, choose fields, or
set anything numeric. It picks from the same levers a person picks from, and every choice
is checked against the real option space before it becomes work: a model that invents a
lever gets its track dropped, not silently defaulted. So the worst case is a plan that is
merely as good as the random one, which is the floor this is built on.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..catalog.queries import Tuple4
from ..llm.service import LLMService
from ..schemas import loads_or
from .day import (
    ASSUMED_DAILY,
    COMPARED,
    CROWDING,
    DEPTHS,
    MAX_TRACKS,
    SHAPES,
    SPEEDS,
    DayPlanner,
    resolve,
)

log = structlog.get_logger(__name__)

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "opening": {"type": "string"},
        "tracks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speed": {"type": "string", "enum": [k for k, _, _ in SPEEDS]},
                    "shape": {"type": "string", "enum": [k for k, _, _ in SHAPES]},
                    "crowding": {"type": "string", "enum": [k for k, _, _ in CROWDING]},
                    "compared": {"type": "string", "enum": [k for k, _, _ in COMPARED]},
                    "depth": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["speed", "shape", "crowding", "compared", "depth", "why"],
            },
        },
    },
    "required": ["opening", "tracks"],
}


SYSTEM = """\
You are planning one day of quantitative research for someone with no background in \
finance, who will give this ten minutes, and who has stopped believing this works. Your \
job is to choose a few different lines of research and say why each one is worth their \
time.

Write like you are talking to a bright ten-year-old. Short sentences. No jargon. Never \
"simply" or "just". Never promise a result.

Choose ONLY from the lever values listed below. Do not invent values — an invented one \
means that whole line of research is thrown away and they get a worse day.

Make the lines DIFFERENT from each other. Three tracks that are nearly the same waste \
the day: they will find the same thing three times. Vary the levers, and give each track \
a different "depth" so they look at different data.

Return JSON:
  "opening" — two sentences to open with. What today's plan is going after, and why that \
is a reasonable place to look. Speak to them, not about them.
  "tracks"  — the lines of research, each with its levers and a "why": one plain sentence \
on what makes this one worth running today.
"""


class PlanAdvisor:
    """Lets the assistant choose the day's research, within the same option space."""

    def __init__(self, planner: DayPlanner, llm: LLMService) -> None:
        self.planner = planner
        self.llm = llm

    async def suggest(
        self,
        *,
        scope: Tuple4,
        tracks: int = 3,
        target: int = ASSUMED_DAILY,
        note: str = "",
        model: str | None = None,
        reasoning: str = "normal",
        neutralization: str = "SUBINDUSTRY",
    ) -> dict[str, Any]:
        """A day's plan, chosen and explained by the assistant."""
        from ..llm.chat import REASONING

        if reasoning not in REASONING:
            raise ValueError(
                f"{reasoning!r} is not a reasoning setting. Choose one of: "
                + ", ".join(REASONING)
                + "."
            )
        tracks = max(1, min(tracks, MAX_TRACKS))

        answer = await self.llm.generate(
            system=SYSTEM,
            user=_prompt(scope, tracks, target, note),
            model_id=model,
            temperature=0.9,
            response_schema=PLAN_SCHEMA,
            thinking=REASONING[reasoning]["level"],
        )

        payload = _parse(answer.text)
        combinations, reasons, rejected = [], [], []
        seen: set[Any] = set()
        for choice in payload["tracks"]:
            combo = resolve(choice)
            if combo is None:
                rejected.append(choice)
                continue
            # Two identical tracks is one track with the day split between them. All
            # five axes count: two tracks differing only in what each company is judged
            # against run the same fields through a different grouping, which is
            # genuinely different research rather than a repeat.
            key = tuple(
                str(choice.get(a)) for a in ("speed", "shape", "crowding", "compared", "depth")
            )
            if key in seen:
                rejected.append(choice)
                continue
            seen.add(key)
            combinations.append(combo)
            reasons.append(str(choice.get("why") or ""))

        if rejected:
            log.warning("plan.advisor_rejected", count=len(rejected), scope=scope.label)

        if not combinations:
            # Never leave them with nothing to press. A random plan is the floor this
            # whole design rests on, and it is a perfectly good day's research.
            log.warning("plan.advisor_empty", scope=scope.label)
            return {
                "opening": (
                    "I could not put a plan together just now, so here is a solid "
                    "ordinary one. It is a good day's work either way."
                ),
                "tracks": self.planner.suggest(
                    tracks=tracks, target=target, neutralization=neutralization
                ),
                "fromAssistant": False,
                "rejected": len(rejected),
                "usage": answer.to_dict()["usage"],
                "model": answer.model,
            }

        return {
            "opening": payload["opening"],
            "tracks": self.planner.build(
                combinations[:tracks],
                target=target,
                neutralization=neutralization,
                reasons=reasons[:tracks],
            ),
            "fromAssistant": True,
            "rejected": len(rejected),
            "usage": answer.to_dict()["usage"],
            "model": answer.model,
            "reasoning": reasoning,
        }


def _prompt(scope: Tuple4, tracks: int, target: int, note: str) -> str:
    def menu(name: str, entries: Any) -> str:
        return f"{name}:\n" + "\n".join(f"  {k} — {label}" for k, label, _ in entries)

    parts = [
        f"MARKET: {scope.label}",
        f"THEY HAVE {target:,} SIMULATIONS TO SPEND TODAY, ACROSS {tracks} LINES OF RESEARCH.",
        menu("SPEED (how fast the signal moves)", SPEEDS),
        menu("SHAPE (what kind of pattern to look for)", SHAPES),
        menu("CROWDING (how picked-over the data is)", CROWDING),
        menu("COMPARED (what each company is judged against)", COMPARED),
        f"DEPTH (which fields to reach for): one of {list(DEPTHS)}, "
        "higher means less obvious data. Give each track a different one.",
    ]
    if note.strip():
        parts.append(f"WHAT THEY SAID THEY WANT TODAY:\n{note.strip()[:1500]}")
    return "\n\n---\n\n".join(parts)


def _parse(text: str) -> dict[str, Any]:
    payload = loads_or(text)
    raw = payload.get("tracks")
    return {
        "opening": str(payload.get("opening") or "").strip(),
        "tracks": [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else [],
    }
