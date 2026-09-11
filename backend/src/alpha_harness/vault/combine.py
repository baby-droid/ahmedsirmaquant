"""Combining signals in a way that is statistically sound.

Adding two alpha expressions is not as simple as writing ``a + b``. An expression's
output has whatever scale its operators left it on: ``rank(x)`` lands in [0, 1],
``zscore(x)`` has roughly unit variance, and a bare ratio like ``ebit / assets`` can be
anything at all. Add two signals on different scales and the louder one drowns the
other — you have not mixed them, you have kept one and added noise.

So the combination depends on what the expressions already are. This module reads that
off the expression and picks accordingly:

* both already ranked, or both already standardised — they share a scale, so adding them
  directly is sound and preserves exactly what each was saying
* anything else — put each on a common footing first

The detection is deliberately conservative. It reads only the *outermost* operator,
because that is what determines the output scale, and anything it does not recognise is
treated as raw rather than assumed safe.

**Multi-statement programs need real work, not wrapping.** Fast Expression allows a
sequence of assignments ending in a value — ``x = ts_backfill(f, 120); y = quantile(x);
rank(y)`` — and a great many alphas written in the platform's own editor look like that.
Wrapping one in ``zscore(...)`` produces a syntax error, and the simulation fails having
spent quota to find out. So the statements are pulled apart, each program's variables are
renamed to keep them from colliding, and only the final values are combined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Operators whose output is a rank in [0, 1]. ``ts_rank`` is included because it is
#: bounded the same way — it ranks a value within its own history rather than across
#: instruments, but the output scale is what matters here.
RANKING = frozenset({"rank", "group_rank", "ts_rank", "quantile", "ts_quantile"})

#: Operators whose output is standardised to roughly zero mean and unit variance.
STANDARDISING = frozenset({"zscore", "ts_zscore", "group_zscore", "normalize", "scale"})

#: Operators that pass their input's scale through, so the answer is inside them.
TRANSPARENT = frozenset(
    {"winsorize", "group_neutralize", "neutralize", "trade_when", "ts_backfill"}
)

#: The outermost call in an expression.
_OUTER = re.compile(r"^\s*([a-zA-Z_]\w*)\s*\((.*)\)\s*$", re.DOTALL)


class Scale(StrEnum):
    """What scale an expression's output is already on."""

    RANK = "rank"
    STANDARD = "standard"
    RAW = "raw"

    @property
    def label(self) -> str:
        return {
            Scale.RANK: "already ranked (0 to 1)",
            Scale.STANDARD: "already standardised",
            Scale.RAW: "raw, on its own scale",
        }[self]


class Strategy(StrEnum):
    """How to put several signals together."""

    #: Standardise each, then add. Safe for anything, keeps relative magnitude.
    ZSCORE_SUM = "zscore_sum"
    #: Rank each, then add. Safe for anything, discards magnitude — robust to outliers.
    RANK_SUM = "rank_sum"
    #: Add as they are. Only sound when every signal is already on the same scale.
    DIRECT_SUM = "direct_sum"
    #: Standardise, add, divide by the count. Ranks identically to ZSCORE_SUM.
    MEAN = "mean"


def outer_call(expression: str) -> tuple[str, str] | None:
    """The outermost operator and its arguments, if the whole expression is one call.

    ``rank(a) + rank(b)`` is deliberately not one — its output scale is not that of
    ``rank``, and treating it as though it were would be exactly the mistake this
    module exists to avoid.
    """
    text = expression.strip()
    match = _OUTER.match(text)
    if match is None:
        return None

    name, inner = match.group(1), match.group(2)
    depth = 0
    for char in inner:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                # The bracket that closed at the end was not the one `name` opened.
                return None
    return (name, inner) if depth == 0 else None


def detect_scale(expression: str, *, depth: int = 0) -> Scale:
    """What scale this expression's output is on.

    Only the outermost operator is read, because that is what sets the scale. Wrappers
    that pass their input through — ``winsorize``, ``group_neutralize`` — are stepped
    into; anything unrecognised is treated as raw rather than assumed safe.
    """
    if depth > 6:
        return Scale.RAW

    if depth == 0:
        # A program's scale is the scale of its final value, not of its first statement.
        # An expression this cannot parse is raw by definition — `build` is where a
        # refusal belongs, not here.
        try:
            expression = parse_program(expression).value
        except ValueError:
            return Scale.RAW

    call = outer_call(expression)
    if call is None:
        return Scale.RAW

    name, inner = call
    if name in RANKING:
        return Scale.RANK
    if name in STANDARDISING:
        return Scale.STANDARD
    if name in TRANSPARENT:
        # The first argument carries the signal; the rest are parameters.
        return detect_scale(_first_argument(inner), depth=depth + 1)
    return Scale.RAW


def _first_argument(arguments: str) -> str:
    depth = 0
    for index, char in enumerate(arguments):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            return arguments[:index]
    return arguments


@dataclass(frozen=True, slots=True)
class Program:
    """A Fast Expression, split into its assignments and its final value.

    A single-expression alpha is just a program with no assignments, so everything
    downstream can treat both the same way.
    """

    statements: list[tuple[str, str]]
    value: str

    @property
    def multi(self) -> bool:
        return bool(self.statements)

    def rename(self, prefix: str) -> Program:
        """Prefix every variable this program defines.

        Two programs both calling their intermediate ``result`` would otherwise
        overwrite each other when concatenated — silently, producing an alpha that
        simulates fine and computes something nobody asked for.
        """
        if not self.statements:
            return self
        mapping = {name: f"{prefix}{name}" for name, _ in self.statements}
        return Program(
            [(mapping[name], _substitute(body, mapping)) for name, body in self.statements],
            _substitute(self.value, mapping),
        )


def parse_program(expression: str) -> Program:
    """Split ``x = a; y = b; final`` into its parts.

    Semicolons inside brackets are not separators, so an argument list survives intact.
    """
    parts = _split_statements(expression)
    if not parts:
        return Program([], expression.strip())

    if len(parts) == 1:
        return Program([], parts[0])

    *earlier, last = parts
    statements: list[tuple[str, str]] = []
    for part in earlier:
        name, _, body = part.partition("=")
        if not body or not _ASSIGNABLE.fullmatch(name.strip()):
            # Semicolons but not assignments — this is a shape we do not understand, and
            # guessing would produce a syntax error that costs a simulation to discover.
            raise ValueError(
                "This expression has several statements but they are not all "
                f"assignments, so it cannot be combined safely: {part.strip()[:60]!r}"
            )
        statements.append((name.strip(), body.strip()))
    return Program(statements, last.strip())


_ASSIGNABLE = re.compile(r"[A-Za-z_]\w*")


def _split_statements(expression: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in expression:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == ";" and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [p for p in (part.strip() for part in parts) if p]


#: A string literal, an operator call, or a keyword-argument name — none of which is a
#: variable reference, and all of which look like one to a naive identifier match.
_NOT_A_VARIABLE = re.compile(
    r"""(?P<skip> "[^"]*" | '[^']*' | \b[A-Za-z_]\w*\s*(?=\() | \b[A-Za-z_]\w*\s*(?==(?!=)) )
      | (?P<name>\b[A-Za-z_]\w*\b)""",
    re.VERBOSE,
)


def _substitute(text: str, mapping: dict[str, str]) -> str:
    """Rename variables, leaving everything that only looks like one alone.

    Three things are skipped: string literals (``driver="gaussian"``), operator names,
    and keyword-argument names. Renaming any of those would change what the expression
    computes while still parsing, which is the worst kind of failure.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group("skip") is not None:
            return match.group("skip")
        name = match.group("name")
        return mapping.get(name, name)

    return _NOT_A_VARIABLE.sub(replace, text)


@dataclass(frozen=True, slots=True)
class Recommendation:
    """A strategy and why it was chosen."""

    strategy: Strategy
    reason: str
    scales: list[str]
    #: Strategies that would also be sound here, for anyone who wants to choose.
    alternatives: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": str(self.strategy),
            "reason": self.reason,
            "scales": self.scales,
            "alternatives": [str(a) for a in self.alternatives],
        }


def recommend(expressions: list[str]) -> Recommendation:
    """Pick a combination strategy from what the expressions already are."""
    scales = [detect_scale(e) for e in expressions]
    labels = [s.label for s in scales]
    distinct = set(scales)

    if distinct == {Scale.RANK}:
        return Recommendation(
            Strategy.DIRECT_SUM,
            "Every signal is already a rank between 0 and 1, so they share a scale and "
            "can be added as they are. Standardising them again would only discard the "
            "ranking you already asked for.",
            labels,
            [Strategy.ZSCORE_SUM, Strategy.MEAN],
        )

    if distinct == {Scale.STANDARD}:
        return Recommendation(
            Strategy.DIRECT_SUM,
            "Every signal is already standardised, so they share a scale and add "
            "cleanly. Nothing needs doing to them first.",
            labels,
            [Strategy.ZSCORE_SUM, Strategy.RANK_SUM, Strategy.MEAN],
        )

    if Scale.RAW in distinct:
        return Recommendation(
            Strategy.ZSCORE_SUM,
            "At least one signal is on its own scale, so adding them directly would let "
            "whichever happens to be larger drown the others. Standardising each first "
            "puts them on equal footing while keeping the relative size of the moves "
            "within each one.",
            labels,
            [Strategy.RANK_SUM, Strategy.MEAN],
        )

    return Recommendation(
        Strategy.ZSCORE_SUM,
        "The signals are on different scales — one ranked, one standardised — so each "
        "is standardised before adding. A rank and a z-score do not mean the same "
        "thing, and adding them raw would weight them arbitrarily.",
        labels,
        [Strategy.RANK_SUM, Strategy.MEAN],
    )


def build(
    expressions: list[str],
    *,
    strategy: Strategy = Strategy.ZSCORE_SUM,
    weights: list[float] | None = None,
    winsorize_std: float | None = 4.0,
) -> str:
    """Write the combined expression.

    Multi-statement programs are taken apart rather than wrapped: their assignments are
    kept, each program's variables are renamed so two seeds cannot overwrite each
    other's, and only the final values are combined. Wrapping a program in ``zscore(...)``
    would be a syntax error that costs a simulation to discover.

    ``winsorize`` wraps the result by default: adding signals produces a fatter tail
    than either had alone, and one instrument with a huge combined score would take an
    outsized position for reasons that have nothing to do with the idea.
    """
    cleaned = [e.strip() for e in expressions if e and e.strip()]
    if len(cleaned) < 2:
        raise ValueError("Mixing needs at least two signals.")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(
            "The same expression appears more than once, so mixing them changes nothing."
        )
    if weights is not None and len(weights) != len(cleaned):
        raise ValueError(
            f"{len(weights)} weights were given for {len(cleaned)} signals; they must match."
        )

    programs = [parse_program(e).rename(f"m{i}_") for i, e in enumerate(cleaned)]

    wrap = {
        Strategy.ZSCORE_SUM: "zscore({})",
        Strategy.MEAN: "zscore({})",
        Strategy.RANK_SUM: "rank({})",
        Strategy.DIRECT_SUM: "{}",
    }[strategy]

    terms = [wrap.format(p.value) for p in programs]
    if weights is not None:
        terms = [f"{w} * {t}" for w, t in zip(weights, terms, strict=True)]

    combined = " + ".join(terms)
    if strategy is Strategy.MEAN:
        combined = f"({combined}) / {len(cleaned)}"
    if winsorize_std:
        combined = f"winsorize({combined}, std={winsorize_std})"

    preamble = [f"{name} = {body}" for program in programs for name, body in program.statements]
    if not preamble:
        return combined
    return ";\n".join([*preamble, combined])


def operators_added(strategy: Strategy, count: int, *, winsorize: bool = True) -> int:
    """How many operators the combination itself costs.

    Worth knowing before it is written: Power Pool allows eight in total, and a
    four-way mix that standardises each part has already spent five of them.
    """
    per_signal = 0 if strategy is Strategy.DIRECT_SUM else count
    return per_signal + (1 if winsorize else 0) + (1 if strategy is Strategy.MEAN else 0)


def strategies() -> list[dict[str, Any]]:
    """The choices, each with what it does and when it is the right one."""
    return [
        {
            "value": str(Strategy.ZSCORE_SUM),
            "label": "Standardise, then add",
            "description": (
                "Puts every signal on the same footing before adding, keeping the "
                "relative size of moves within each. The safe default, and the right "
                "one whenever the signals are on different scales."
            ),
        },
        {
            "value": str(Strategy.DIRECT_SUM),
            "label": "Add directly",
            "description": (
                "Adds the signals exactly as they are. Sound only when they already "
                "share a scale — all ranked, or all standardised — and the best choice "
                "then, because it changes nothing you did not ask for."
            ),
        },
        {
            "value": str(Strategy.RANK_SUM),
            "label": "Rank, then add",
            "description": (
                "Ranks each signal before adding. Discards how large each move was and "
                "keeps only the order, which makes it the most robust to outliers and "
                "the bluntest."
            ),
        },
        {
            "value": str(Strategy.MEAN),
            "label": "Average",
            "description": (
                "Standardise-then-add divided by the number of signals. It ranks stocks "
                "identically; useful when you want the output on a scale comparable to "
                "a single alpha."
            ),
        },
    ]
