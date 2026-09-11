"""The template language.

A template is one alpha idea with holes in it::

    name: reversion on a ranked field
    expr: rank(ts_zscore($field, $lookback))
    vars:
      field:
        type: datafield
        category: fundamental
        max_alpha_count: 50
      lookback:
        type: int
        grid: [5, 22, 63, 252]
    settings:
      region: USA
      delay: 1
      universe: [TOP3000, TOP1000]
      neutralization: [SUBINDUSTRY, INDUSTRY]

Two consumers read the same document, which is why variables describe themselves twice:

* **Expansion** enumerates every combination — a grid sweep.
* **Optimization** samples from it — each variable also yields an Optuna distribution.

Settings are variables too. Region, delay, universe and neutralization are as much part
of an alpha as the expression is, and sweeping them is ordinary practice. One caveat
carries real cost: region, delay, instrument type and language are four of the five
fields a multi-simulation's children must share, so sweeping them fragments batches.
:mod:`alpha_harness.templates.expand` reports that rather than letting throughput
quietly collapse.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: ``$name`` or ``${name}`` in an expression.
PLACEHOLDER = re.compile(r"\$\{(\w+)\}|\$(\w+)")

#: Settings that force separate batches when swept. See the module docstring.
BATCH_SPLITTING_SETTINGS = frozenset({"region", "delay", "instrumentType", "language", "type"})


def placeholders(expression: str) -> set[str]:
    """Every variable named in an expression."""
    return {a or b for a, b in PLACEHOLDER.findall(expression)}


def substitute(expression: str, values: dict[str, Any]) -> str:
    """Replace ``$name`` with its value.

    Longest names first, so ``$lookback`` is not partially matched by ``$look``.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return str(values.get(name, match.group(0)))

    return PLACEHOLDER.sub(replace, expression)


class VariableBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = None


class DataFieldVar(VariableBase):
    """A data field, chosen from the synced catalog.

    The filters narrow which fields qualify. ``max_alpha_count`` is the interesting
    one: a high-coverage field almost nobody has used is where an edge is likely to
    still exist.
    """

    type: Literal["datafield"] = "datafield"

    #: Explicit list. When given, the catalog filters are ignored.
    values: list[str] | None = None

    dataset: str | None = None
    category: str | None = None
    subcategory: str | None = None
    field_type: str | None = Field(default=None, description="MATRIX, VECTOR or GROUP")
    min_coverage: float | None = None
    max_alpha_count: int | None = None
    min_alpha_count: int | None = None
    search: str | None = None

    #: With a ``limit``, the ordering decides which fields are actually simulated, so it
    #: is stated rather than assumed. Widest coverage first by default: a field present
    #: on 4% of the universe makes a sparse alpha however good the idea behind it is.
    order_by: Literal["coverage", "alpha_count", "user_count", "pyramid_multiplier", "field_id"] = (
        "coverage"
    )
    order_desc: bool = True

    #: Cap on how many fields this variable contributes, so one loose filter cannot
    #: turn a template into ten thousand simulations by accident.
    limit: int = Field(default=25, ge=1, le=1000)


class IntVar(VariableBase):
    """An integer, either an explicit grid or a range.

    Prefer a grid of conventional windows (5, 22, 63, 252) over a dense range: the
    platform's own guidance is that searching 37 and 14 alongside 21 is how templates
    end up overfitted rather than informative.
    """

    type: Literal["int"] = "int"
    grid: list[int] | None = None
    low: int | None = None
    high: int | None = None
    step: int = Field(default=1, ge=1)
    log: bool = False

    @model_validator(mode="after")
    def _needs_a_domain(self) -> IntVar:
        if self.grid is None and (self.low is None or self.high is None):
            raise ValueError("an int variable needs either `grid` or both `low` and `high`")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("`low` must not exceed `high`")
        return self


class FloatVar(VariableBase):
    type: Literal["float"] = "float"
    grid: list[float] | None = None
    low: float | None = None
    high: float | None = None
    step: float | None = None
    log: bool = False

    @model_validator(mode="after")
    def _needs_a_domain(self) -> FloatVar:
        if self.grid is None and (self.low is None or self.high is None):
            raise ValueError("a float variable needs either `grid` or both `low` and `high`")
        return self


class ChoiceVar(VariableBase):
    """One of a fixed set. Also used for operator choice, so a template can ask
    whether ts_mean or ts_median works better without being rewritten."""

    type: Literal["choice"] = "choice"
    values: list[Any] = Field(min_length=1)


Variable = Annotated[
    DataFieldVar | IntVar | FloatVar | ChoiceVar,
    Field(discriminator="type"),
]


class Constraints(BaseModel):
    """Limits an expanded alpha must satisfy.

    Checked before anything is queued, because an alpha that cannot qualify is a
    simulation that spends daily quota for nothing.
    """

    model_config = ConfigDict(extra="forbid")

    max_operators: int | None = Field(default=None, description="Total operators, repeats included")
    max_unique_fields: int | None = Field(
        default=None, description="Distinct data fields, excluding grouping fields"
    )
    #: Power Pool rules from the consultant documentation: Sharpe >= 1.0, at most 8
    #: operators (ts_backfill and group_backfill excluded), at most 3 unique
    #: non-grouping fields. Setting this applies the operator and field limits; the
    #: Sharpe threshold is a property of the result, not of the template.
    power_pool: bool = False

    #: Ceiling on the expansion itself. A template is a search space, and a careless
    #: one multiplies into thousands of simulations.
    max_simulations: int = Field(default=500, ge=1, le=10_000)


class TemplateSpec(BaseModel):
    """A parsed template."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    expr: str
    vars: dict[str, Variable] = Field(default_factory=dict)
    #: Scalar for a fixed setting, list for a swept one. Also accepts ``{grid: [...]}``.
    settings: dict[str, Any] = Field(default_factory=dict)
    constraints: Constraints = Field(default_factory=Constraints)
    type: str = "REGULAR"

    @model_validator(mode="after")
    def _placeholders_are_declared(self) -> TemplateSpec:
        used = placeholders(self.expr)
        declared = set(self.vars)
        missing = used - declared
        if missing:
            raise ValueError(
                "the expression uses "
                + ", ".join(f"${n}" for n in sorted(missing))
                + " but no such variable is declared"
            )
        return self

    @model_validator(mode="after")
    def _settings_have_values(self) -> TemplateSpec:
        """An empty list is not "leave it to the default" — it is no value at all.

        Caught here because everything downstream reasonably assumes a setting has at
        least one option, and the alternative is an IndexError surfacing as a 500 while
        someone is mid-edit.
        """
        empty = sorted(n for n, v in self.settings.items() if not setting_options(v))
        if empty:
            raise ValueError(
                ", ".join(empty)
                + " has no values. Give it a value, a list of values to sweep, or remove"
                " it to use the platform default."
            )
        return self

    @property
    def unused_vars(self) -> set[str]:
        """Declared but never referenced — usually a typo in the expression."""
        return set(self.vars) - placeholders(self.expr)

    @property
    def swept_settings(self) -> dict[str, list[Any]]:
        """Settings with more than one value."""
        swept: dict[str, list[Any]] = {}
        for name, value in self.settings.items():
            options = setting_options(value)
            if len(options) > 1:
                swept[name] = options
        return swept

    @property
    def batch_splitting_sweeps(self) -> list[str]:
        """Swept settings that force work into separate batches."""
        return sorted(set(self.swept_settings) & BATCH_SPLITTING_SETTINGS)


def setting_options(value: Any) -> list[Any]:
    """Normalise a settings entry into the list of values it stands for."""
    if isinstance(value, dict):
        grid = value.get("grid")
        if isinstance(grid, list):
            return list(grid)
        return [value]
    if isinstance(value, list):
        return list(value)
    return [value]


class TemplateError(ValueError):
    """A template that cannot be parsed.

    Typed so the API can answer 422 without a blanket ``ValueError`` handler quietly
    converting genuine bugs into user errors. ``problems`` carries one entry per fault
    with the path into the document, so an editor can point at the right line.
    """

    def __init__(self, message: str, problems: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or [{"where": None, "message": message}]


def parse(document: str | dict[str, Any]) -> TemplateSpec:
    """Parse YAML (or an already-decoded mapping) into a template.

    Raises :class:`TemplateError` with a message written for whoever is editing the
    template. Pydantic's own report is translated rather than passed through — a wall of
    validation objects is not something a first-time user can act on.
    """
    import yaml
    from pydantic import ValidationError

    if isinstance(document, str):
        try:
            data = yaml.safe_load(document)
        except yaml.YAMLError as exc:
            raise TemplateError(f"This is not valid YAML. {exc}") from exc
    else:
        data = document

    if data is None:
        raise TemplateError("This template is empty. It needs at least `name` and `expr`.")
    if not isinstance(data, dict):
        raise TemplateError("A template must be a mapping with at least `name` and `expr`.")

    try:
        return TemplateSpec.model_validate(data)
    except ValidationError as exc:
        problems = [
            {
                "where": ".".join(str(p) for p in error["loc"]) or None,
                "message": _readable(error),
            }
            for error in exc.errors()
        ]
        summary = "; ".join(
            f"{p['where']}: {p['message']}" if p["where"] else str(p["message"])
            for p in problems[:4]
        )
        raise TemplateError(f"This template is not valid. {summary}", problems) from exc


def _readable(error: dict[str, Any]) -> str:
    """One pydantic error, in plain words."""
    match error["type"]:
        case "missing":
            return "this is required but missing"
        case "extra_forbidden":
            return "not a recognised key — check the spelling"
        case "union_tag_invalid" | "union_tag_not_found":
            return "`type` must be one of: datafield, int, float, choice"
        case _:
            return str(error.get("msg", "invalid"))


def to_yaml(spec: TemplateSpec) -> str:
    """Render a template back to YAML, for the editor's other pane."""
    import yaml

    data = spec.model_dump(exclude_none=True, exclude_defaults=False)
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, width=88)
