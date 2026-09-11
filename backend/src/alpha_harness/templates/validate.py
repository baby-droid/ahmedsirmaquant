"""Validating a template before it costs anything.

Every check here exists because failing it later is expensive. A field that does not
exist in the chosen scope fails at simulation time and still spends daily quota; an
alpha over the Power Pool operator limit runs perfectly and is simply never eligible.

Four sources of truth, none of them hardcoded:

* operators — ``GET /operators``, cached on login
* data fields — the locally synced catalog, per (instrumentType, region, delay, universe)
* settings — ``OPTIONS /simulations``, which is recursive and region-dependent
* constraints — the template's own limits, plus the Power Pool rules from the docs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..brain.settings_schema import validate_settings
from ..schemas import camel_dict
from .schema import DataFieldVar, TemplateSpec, setting_options

#: Grouping fields do not count toward the Power Pool field limit.
GROUPING_FIELDS = frozenset(
    {"country", "industry", "subindustry", "currency", "market", "sector", "exchange"}
)

#: These two are excluded from operator counts by the platform's own rules.
UNCOUNTED_OPERATORS = frozenset({"ts_backfill", "group_backfill"})

#: Power Pool: at most 8 operators and 3 unique non-grouping fields.
POWER_POOL_MAX_OPERATORS = 8
POWER_POOL_MAX_FIELDS = 3

#: A bare identifier followed by "(" is an operator call.
CALL = re.compile(r"\b([a-zA-Z_]\w*)\s*\(")
#: A bare identifier not followed by "(" is a data field or a variable.
IDENTIFIER = re.compile(r"\b([a-zA-Z_]\w*)\b(?!\s*\()")

#: Fast Expression keywords and literals that are not fields.
NON_FIELDS = frozenset({"true", "false", "nan", "inf"})


@dataclass(slots=True)
class Problem:
    """One thing wrong, phrased for whoever is editing the template."""

    severity: str  # "error" | "warning"
    message: str
    where: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return camel_dict(self)


@dataclass(slots=True)
class Report:
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    def error(self, message: str, where: str | None = None) -> None:
        self.problems.append(Problem("error", message, where))

    def warn(self, message: str, where: str | None = None) -> None:
        self.problems.append(Problem("warning", message, where))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": [p.to_dict() for p in self.problems],
        }


def operators_in(expression: str) -> list[str]:
    """Every operator call, repeats included — the platform counts repeats."""
    return CALL.findall(expression)


def count_operators(expression: str) -> int:
    """Operator count under the platform's rules.

    ``ts_backfill`` and ``group_backfill`` are excluded, so handling missing data does
    not count against an alpha's complexity budget.
    """
    return sum(1 for name in operators_in(expression) if name not in UNCOUNTED_OPERATORS)


def fields_in(expression: str) -> set[str]:
    """Identifiers that look like data fields.

    Anything followed by ``(`` is an operator, and named arguments such as
    ``rettype=2`` are excluded. Approximate by design: it is used for limits and
    warnings, never to reject an expression outright.
    """
    calls = set(operators_in(expression))
    # Strip string literals first: in `driver="gaussian"` neither the argument name
    # nor the quoted value is a data field.
    without_strings = re.sub(r"\"[^\"]*\"|'[^']*'", " ", expression)
    without_kwargs = re.sub(r"\b\w+\s*=", " ", without_strings)
    found = set(IDENTIFIER.findall(without_kwargs))
    return {f for f in found - calls if f.lower() not in NON_FIELDS and not f.isdigit()}


def unique_data_fields(expression: str) -> set[str]:
    """Data fields excluding grouping fields, which the platform does not count."""
    return {f for f in fields_in(expression) if f not in GROUPING_FIELDS}


def check_constraints(spec: TemplateSpec, expression: str) -> str | None:
    """Return why this expression violates the template's limits, or ``None``.

    Shaped as a callable for :func:`alpha_harness.templates.expand.expand`, which
    filters the grid with it.
    """
    constraints = spec.constraints
    operators = count_operators(expression)
    fields = unique_data_fields(expression)

    max_operators = constraints.max_operators
    max_fields = constraints.max_unique_fields
    if constraints.power_pool:
        max_operators = min(max_operators or POWER_POOL_MAX_OPERATORS, POWER_POOL_MAX_OPERATORS)
        max_fields = min(max_fields or POWER_POOL_MAX_FIELDS, POWER_POOL_MAX_FIELDS)

    if max_operators is not None and operators > max_operators:
        return f"{operators} operators, limit is {max_operators}"
    if max_fields is not None and len(fields) > max_fields:
        return f"{len(fields)} data fields, limit is {max_fields}"
    return None


def validate_template(
    spec: TemplateSpec,
    *,
    known_operators: set[str] | None = None,
    settings_schema: dict[str, Any] | None = None,
    available_fields: set[str] | None = None,
    scope_label: str | None = None,
    available_by_scope: dict[str, set[str]] | None = None,
) -> Report:
    """Check a template against everything we know.

    Every argument is optional: offline, or before the catalog is synced, the checks
    that cannot run are skipped rather than blocking the user.

    ``available_by_scope`` supersedes ``available_fields`` when given. A template that
    sweeps region runs in several scopes, and a field missing from one of them fails
    only there — which is far harder to diagnose than being told up front.
    """
    report = Report()

    if not spec.expr.strip():
        report.error("The template has no expression.", "expr")
        return report

    for name in sorted(spec.unused_vars):
        report.warn(f"${name} is declared but never used in the expression.", f"vars.{name}")

    _check_operators(spec, report, known_operators)
    if available_by_scope:
        for label, available in available_by_scope.items():
            _check_fields(spec, report, available, label)
    else:
        _check_fields(spec, report, available_fields, scope_label)
    _check_settings(spec, report, settings_schema)
    _check_constraints(spec, report)
    _check_size(spec, report)

    return report


def _check_operators(spec: TemplateSpec, report: Report, known_operators: set[str] | None) -> None:
    if not known_operators:
        return
    # A choice variable used as an operator (``$op(x)``) is not itself an operator name.
    used = set(operators_in(re.sub(r"\$\{?\w+\}?\s*\(", "(", spec.expr)))
    for name in sorted(used - known_operators):
        suggestion = _closest(name, known_operators)
        hint = f" Did you mean {suggestion}?" if suggestion else ""
        report.error(f"{name} is not an operator on this platform.{hint}", "expr")


def _check_fields(
    spec: TemplateSpec,
    report: Report,
    available: set[str] | None,
    scope_label: str | None,
) -> None:
    if available is None:
        return

    where = f" in {scope_label}" if scope_label else ""

    # Literal fields written directly into the expression.
    declared_vars = set(spec.vars)
    for name in sorted(unique_data_fields(spec.expr) - declared_vars):
        if name not in available:
            report.warn(
                f"{name} is not in the synced catalog{where}. If it is a real field, "
                "sync this scope; otherwise the simulation will fail.",
                "expr",
            )

    # Fields listed explicitly on a datafield variable.
    for var_name, variable in spec.vars.items():
        if not isinstance(variable, DataFieldVar) or not variable.values:
            continue
        missing = [v for v in variable.values if v not in available]
        if missing:
            shown = ", ".join(missing[:5]) + ("…" if len(missing) > 5 else "")
            report.error(
                f"${var_name} lists {len(missing)} field(s) not available{where}: {shown}",
                f"vars.{var_name}",
            )


def _check_settings(spec: TemplateSpec, report: Report, schema: dict[str, Any] | None) -> None:
    if not schema:
        return

    # Every swept combination must be legal, not just the first — universes and
    # neutralizations are region-dependent.
    names = list(spec.settings)
    domains = [setting_options(spec.settings[n]) for n in names]

    from itertools import product

    seen: set[str] = set()
    for combo in product(*domains) if domains else [()]:
        candidate = dict(zip(names, combo, strict=True))
        for problem in validate_settings(schema, candidate):
            if problem not in seen:
                seen.add(problem)
                report.error(problem, "settings")
        if len(seen) > 8:  # enough to act on; the rest are noise
            break


def _check_constraints(spec: TemplateSpec, report: Report) -> None:
    if spec.constraints.power_pool:
        operators = count_operators(spec.expr)
        if operators > POWER_POOL_MAX_OPERATORS:
            report.error(
                f"Power Pool allows {POWER_POOL_MAX_OPERATORS} operators "
                f"(ts_backfill and group_backfill excepted); this uses {operators} "
                "before any variable is substituted.",
                "expr",
            )


def _check_size(spec: TemplateSpec, report: Report) -> None:
    """Warn when a template will pack badly, before it is run."""
    splitting = spec.batch_splitting_sweeps
    if splitting:
        report.warn(
            "Sweeping "
            + " and ".join(splitting)
            + " splits the work into separate batches, because a multi-simulation's "
            "children must share those fields. The sweep still runs; it just uses more "
            "of the eight concurrent slots than a sweep over universe or decay would.",
            "settings",
        )


def _closest(name: str, candidates: set[str]) -> str | None:
    """Nearest operator name, for a typo hint."""
    import difflib

    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.75)
    return matches[0] if matches else None
