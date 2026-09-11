"""Expanding a template into concrete simulations.

Two ways out of one template:

* :func:`expand` enumerates the whole grid — a systematic sweep.
* :func:`realise` builds a single simulation from one assignment of the variables,
  which is what the optimizer calls once per trial.

Both go through the same substitution and settings-merge, so a trial the optimizer runs
is identical to the one a grid sweep would have produced for the same values.

Expansion is ordered so that consecutive simulations share a batch key wherever it can
arrange that. The engine packs on the five fields children must share, so emitting all
of one region's work together is the difference between eight full batches and eighty
singletons.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import product
from typing import Any

import structlog

from ..brain.schemas import SimulationRequest, SimulationSettings
from .schema import (
    ChoiceVar,
    DataFieldVar,
    FloatVar,
    IntVar,
    TemplateSpec,
    placeholders,
    setting_options,
    substitute,
)

log = structlog.get_logger(__name__)

#: A multi-simulation carries ten children, so a sweep is best sized in tens.
BATCH_SIZE = 10


@dataclass(slots=True)
class Expansion:
    """The result of expanding a template."""

    requests: list[SimulationRequest] = field(default_factory=list)
    #: The variable assignment behind each request, index-aligned with ``requests``.
    assignments: list[dict[str, Any]] = field(default_factory=list)
    #: Combinations rejected by the template's own constraints, with the reason.
    rejected: list[dict[str, Any]] = field(default_factory=list)
    #: How many the grid would have produced had ``max_simulations`` not applied.
    total_possible: int = 0
    truncated: bool = False
    #: Settings swept that force separate batches, and therefore cost throughput.
    batch_splitting: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.requests)

    def batch_keys(self) -> dict[tuple, int]:
        """How the expansion will pack: batch key -> number of simulations."""
        counts: dict[tuple, int] = {}
        for request in self.requests:
            key = request.batch_key
            counts[key] = counts.get(key, 0) + 1
        return counts

    def packing_summary(self) -> dict[str, Any]:
        """What this expansion will actually cost in slots.

        Reported because the cost of sweeping a batch-splitting setting is invisible
        otherwise: eighty simulations in one group fill eight full batches, whereas
        eighty spread over eighty groups need eighty.
        """
        counts = self.batch_keys()
        batches = sum(-(-n // BATCH_SIZE) for n in counts.values())
        return {
            "simulations": self.count,
            "batchKeys": len(counts),
            "batches": batches,
            "fullBatches": sum(n // BATCH_SIZE for n in counts.values()),
            "efficiency": round(self.count / (batches * BATCH_SIZE), 3) if batches else 0.0,
            "batchSplittingSweeps": self.batch_splitting,
        }


FieldResolver = Any  # Callable[[DataFieldVar], list[str]]


def variable_domain(name: str, variable: Any, resolve_fields: FieldResolver = None) -> list[Any]:
    """Every value a variable can take, for grid expansion.

    Data fields come from the catalog via ``resolve_fields``; without a resolver only
    an explicit ``values`` list can be used, which keeps this function pure for tests.
    """
    if isinstance(variable, DataFieldVar):
        if variable.values:
            return list(variable.values)
        if resolve_fields is None:
            raise ValueError(
                f"${name} selects data fields from the catalog, so the catalog must be "
                "available. Give it an explicit `values` list to avoid that."
            )
        return list(resolve_fields(variable))

    if isinstance(variable, ChoiceVar):
        return list(variable.values)

    if isinstance(variable, IntVar):
        if variable.grid is not None:
            return list(variable.grid)
        assert variable.low is not None and variable.high is not None
        return list(range(variable.low, variable.high + 1, variable.step))

    if isinstance(variable, FloatVar):
        if variable.grid is not None:
            return list(variable.grid)
        assert variable.low is not None and variable.high is not None
        step = variable.step or (variable.high - variable.low) / 4 or 1.0
        values: list[float] = []
        current = variable.low
        # Guard against a step that would never terminate.
        for _ in range(1000):
            if current > variable.high + 1e-9:
                break
            values.append(round(current, 10))
            current += step
        return values

    raise ValueError(f"${name} has an unknown variable type")


def realise(
    spec: TemplateSpec,
    assignment: dict[str, Any],
    settings_choice: dict[str, Any],
) -> SimulationRequest:
    """Build one simulation from one set of values.

    The single place a template becomes a request, so a grid sweep and an optimizer
    trial cannot drift apart.
    """
    expression = substitute(spec.expr, assignment).strip()

    # The last line of defence. A leftover ``$field`` is a syntax error on the platform,
    # and finding that out costs a slice of the daily quota — so nothing that still
    # contains a placeholder is allowed to become a request, by any path.
    leftover = placeholders(expression)
    if leftover:
        raise ValueError(
            f"Template '{spec.name}' still contains "
            + ", ".join(f"${n}" for n in sorted(leftover))
            + " after substitution. The variable resolved to no values — usually a "
            "catalog filter that matches nothing, or a scope that has not been synced."
        )

    merged: dict[str, Any] = {}
    for name, value in spec.settings.items():
        options = setting_options(value)
        merged[name] = settings_choice.get(name, options[0])
    merged.update({k: v for k, v in settings_choice.items() if k in spec.settings})

    try:
        settings = SimulationSettings.model_validate(merged)
    except Exception as exc:
        missing = sorted({"region", "universe", "delay"} - set(merged))
        hint = f" The template does not set {', '.join(missing)}." if missing else ""
        raise ValueError(
            f"Template '{spec.name}' cannot be turned into a simulation.{hint}"
        ) from exc

    return SimulationRequest(
        type=spec.type,  # type: ignore[arg-type]
        settings=settings,
        regular=expression,
    )


def expand(
    spec: TemplateSpec,
    *,
    resolve_fields: FieldResolver = None,
    check: Any = None,
) -> Expansion:
    """Enumerate the template's grid.

    ``check`` is an optional callable taking ``(expression, assignment)`` and returning
    a reason string when the combination violates a constraint, or ``None`` when it is
    fine. Rejected combinations are reported rather than silently dropped, because
    "why did my 500-simulation sweep produce 12" is otherwise unanswerable.
    """
    var_names = list(spec.vars)
    domains = [variable_domain(n, spec.vars[n], resolve_fields) for n in var_names]

    setting_names = [n for n, v in spec.settings.items() if len(setting_options(v)) > 1]
    setting_domains = [setting_options(spec.settings[n]) for n in setting_names]

    result = Expansion(batch_splitting=spec.batch_splitting_sweeps)

    total = 1
    for domain in [*domains, *setting_domains]:
        total *= max(1, len(domain))
    result.total_possible = total

    limit = spec.constraints.max_simulations

    for var_values, setting_values in _ordered_product(domains, setting_domains):
        if len(result.requests) >= limit:
            result.truncated = True
            break

        assignment = dict(zip(var_names, var_values, strict=True))
        settings_choice = dict(zip(setting_names, setting_values, strict=True))

        expression = substitute(spec.expr, assignment).strip()
        if check is not None:
            reason = check(expression, assignment)
            if reason:
                result.rejected.append(
                    {"expression": expression, "assignment": assignment, "reason": reason}
                )
                continue

        result.requests.append(realise(spec, assignment, settings_choice))
        result.assignments.append({**assignment, **settings_choice})

    if result.truncated:
        log.info(
            "template.truncated",
            name=spec.name,
            produced=len(result.requests),
            possible=total,
        )
    return result


def _ordered_product(
    var_domains: Sequence[Sequence[Any]],
    setting_domains: Sequence[Sequence[Any]],
) -> Iterator[tuple[tuple[Any, ...], tuple[Any, ...]]]:
    """Cartesian product, settings varying slowest.

    Batch-splitting settings change least often, so consecutive simulations share a
    batch key for as long as possible and the engine can fill whole batches from a
    contiguous run of the queue.
    """
    # Lazy on purpose: materialising the full product before truncation exhausted memory
    # on large field-by-grid templates.
    for settings_values in product(*setting_domains):
        for var_values in product(*var_domains):
            yield var_values, settings_values


def round_to_batches(n: int, *, batch_size: int = BATCH_SIZE, slots: int = 8) -> int:
    """Round a trial count down to something that packs into whole batches.

    A multi-simulation holds ten children and there are eight slots, so work arrives
    most efficiently in multiples of ten and ideally in multiples of eighty. Asking an
    optimizer for 47 trials leaves three part-filled batches; asking for 40 does not.
    """
    if n <= 0:
        return 0
    if n < batch_size:
        return n
    full_round = batch_size * slots
    if n >= full_round:
        return (n // full_round) * full_round
    return (n // batch_size) * batch_size
