"""Turning ``$field`` into real data fields.

A datafield variable can name its values outright, or describe them::

    field:
      type: datafield
      category: fundamental
      min_coverage: 0.9
      max_alpha_count: 50
      limit: 20

The description is answered from the locally synced catalog, which is the only place
that knows what exists. Two things about that matter enough to be design decisions
rather than details:

**Availability is per scope.** A field lives in the catalog once per
``(instrumentType, region, delay, universe)``. A template that sweeps region therefore
has several scopes, and a field present in only some of them produces simulations that
are guaranteed to fail while still spending daily quota. So a variable resolves to the
*intersection* across every scope the template will run in — see :meth:`Resolver.resolve`.

**Order decides the answer.** With a ``limit``, whatever the ordering puts first is what
gets simulated. That is too consequential to leave implicit, so it is part of the
variable (``order_by`` / ``order_desc``) and defaults to widest coverage first: a field
present on 4% of the universe makes a sparse alpha regardless of how good the idea is.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import structlog

from ..catalog.queries import CatalogQueries, FieldFilter, Tuple4
from .schema import DataFieldVar, TemplateSpec, setting_options

log = structlog.get_logger(__name__)

#: How far past ``limit`` to look when candidates still have to survive an intersection
#: across several scopes. Bounded so a broad filter cannot pull the whole catalog.
OVERFETCH = 6
MAX_CANDIDATES = 2000


def scopes_of(spec: TemplateSpec, *, instrument_type: str | None = None) -> list[Tuple4]:
    """Every catalog scope this template will run in.

    Derived from the settings, including swept ones — a sweep over region or universe
    genuinely changes which fields exist.
    """
    settings = spec.settings

    def options(name: str, fallback: list[Any]) -> list[Any]:
        if name not in settings:
            return fallback
        return setting_options(settings[name]) or fallback

    instruments = [instrument_type] if instrument_type else options("instrumentType", ["EQUITY"])
    scopes: list[Tuple4] = []
    for it in instruments:
        for region in options("region", []):
            for delay in options("delay", []):
                for universe in options("universe", []):
                    scopes.append(
                        Tuple4(
                            instrument_type=str(it),
                            region=str(region),
                            delay=int(delay),
                            universe=str(universe),
                        )
                    )
    return scopes


@dataclass(slots=True)
class Resolution:
    """What a variable resolved to, and what it cost.

    ``dropped`` is reported rather than swallowed: "I asked for 20 fields and got 6" has
    exactly one useful answer, which is *which scope removed them*.
    """

    values: list[str] = dc_field(default_factory=list)
    candidates: int = 0
    dropped: dict[str, list[str]] = dc_field(default_factory=dict)
    truncated: bool = False

    @property
    def dropped_count(self) -> int:
        return len({f for fields in self.dropped.values() for f in fields})


@dataclass(slots=True)
class Prepared:
    """Pre-resolved domains for a template's datafield variables.

    Expansion is synchronous and pure; the catalog is not. Resolving up front keeps that
    separation intact — :func:`~alpha_harness.templates.expand.expand` receives a plain
    callable and never learns that a database exists.
    """

    #: Keyed by the variable's filter, so two variables asking the same question share
    #: one catalog query.
    by_variable: dict[str, Resolution] = dc_field(default_factory=dict)
    #: The same resolutions keyed by variable name, so a report can name them.
    named: dict[str, Resolution] = dc_field(default_factory=dict)
    scopes: list[Tuple4] = dc_field(default_factory=list)

    def __call__(self, variable: DataFieldVar) -> list[str]:
        if variable.values:
            return list(variable.values)
        key = _key(variable)
        resolution = self.by_variable.get(key)
        if resolution is None:
            raise ValueError(
                "This data field variable was not resolved against the catalog. "
                "Sync the scope first, or give it an explicit `values` list."
            )
        return list(resolution.values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scopes": [s.label for s in self.scopes],
            "variables": {
                name: {
                    "values": r.values,
                    "count": len(r.values),
                    "candidates": r.candidates,
                    "droppedCount": r.dropped_count,
                    "dropped": r.dropped,
                    "truncated": r.truncated,
                }
                for name, r in self.named.items()
            },
        }


def _key(variable: DataFieldVar) -> str:
    """Identity of a variable's *filter*, not of the object.

    Two variables asking the same question have the same answer, so they share a lookup
    key and the catalog is queried once.
    """
    return variable.model_dump_json(exclude={"description"})


class Resolver:
    """Answers datafield variables from the synced catalog."""

    def __init__(self, queries: CatalogQueries) -> None:
        self.queries = queries

    async def prepare(
        self,
        spec: TemplateSpec,
        *,
        instrument_type: str | None = None,
    ) -> Prepared:
        """Resolve every datafield variable in a template."""
        scopes = scopes_of(spec, instrument_type=instrument_type)
        prepared = Prepared(scopes=scopes)

        for name, variable in spec.vars.items():
            if not isinstance(variable, DataFieldVar) or variable.values:
                continue
            if not scopes:
                raise ValueError(
                    f"${name} selects fields from the catalog, but the template does not "
                    "set region, delay and universe, so there is no scope to search."
                )
            key = _key(variable)
            if key not in prepared.by_variable:
                prepared.by_variable[key] = await self.resolve(variable, scopes)
            prepared.named[name] = prepared.by_variable[key]

        return prepared

    async def resolve(self, variable: DataFieldVar, scopes: list[Tuple4]) -> Resolution:
        """Fields matching this variable, present in *every* scope.

        The primary scope supplies the ranking; the rest only remove candidates. A field
        missing from one scope of a sweep would fail there, so it is dropped everywhere
        rather than producing a partly-broken sweep.
        """
        if variable.values:
            return Resolution(values=list(variable.values), candidates=len(variable.values))
        if not scopes:
            return Resolution()

        primary, *others = scopes
        want = min(variable.limit * (OVERFETCH if others else 1), MAX_CANDIDATES)
        page = await self.queries.fields(primary, self._filter(variable, want))
        candidates = [str(row["field_id"]) for row in page["results"]]

        resolution = Resolution(candidates=len(candidates))
        surviving = list(candidates)

        for scope in others:
            present = await self._present(scope, surviving)
            missing = [f for f in surviving if f not in present]
            if missing:
                resolution.dropped[scope.label] = missing
            surviving = [f for f in surviving if f in present]

        resolution.values = surviving[: variable.limit]
        resolution.truncated = len(surviving) > variable.limit or int(page["total"]) > want

        if not resolution.values:
            log.info(
                "template.resolver.empty",
                scope=primary.label,
                candidates=len(candidates),
                scopes=len(scopes),
            )
        return resolution

    def _filter(self, variable: DataFieldVar, limit: int) -> FieldFilter:
        return FieldFilter(
            search=variable.search,
            dataset_ids=[variable.dataset] if variable.dataset else [],
            category_ids=[variable.category] if variable.category else [],
            subcategory_ids=[variable.subcategory] if variable.subcategory else [],
            field_types=[variable.field_type] if variable.field_type else [],
            coverage_min=variable.min_coverage,
            alpha_count_min=variable.min_alpha_count,
            alpha_count_max=variable.max_alpha_count,
            sort_by=variable.order_by,
            sort_desc=variable.order_desc,
            limit=limit,
        )

    async def _present(self, scope: Tuple4, field_ids: list[str]) -> set[str]:
        """Which of these fields exist in this scope."""
        if not field_ids:
            return set()
        placeholders = ", ".join("?" for _ in field_ids)
        rows = await self.queries.catalog.query(
            f"SELECT field_id FROM data_field "
            f"WHERE {Tuple4.WHERE} AND field_id IN ({placeholders})",
            [*scope.params, *field_ids],
        )
        return {str(row["field_id"]) for row in rows}

    async def available(self, scope: Tuple4) -> set[str]:
        """Every field id in a scope — the set the validator checks literals against."""
        rows = await self.queries.catalog.query(
            f"SELECT field_id FROM data_field WHERE {Tuple4.WHERE}", scope.params
        )
        return {str(row["field_id"]) for row in rows}
