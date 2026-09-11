"""One path from a YAML document to queued simulations.

The Template Studio, the grid sweep and the optimizer all need the same four steps, in
the same order, and getting them out of order is expensive:

1. **parse** — reject bad YAML before anything else runs
2. **resolve** — ask the catalog what ``$field`` actually means here
3. **validate** — operators, field availability, settings legality, constraints
4. **expand** — enumerate the grid, or realise one trial

They live here together so a template previewed in the editor and a template run by a
study cannot diverge. A preview that says "40 simulations, 4 full batches" is the same
computation that later produces those 40 simulations.
"""

from __future__ import annotations

from typing import Any

import structlog

from ..catalog.queries import CatalogQueries
from ..services.auth import AuthService
from .expand import Expansion, expand
from .resolver import Prepared, Resolver, scopes_of
from .schema import TemplateSpec, parse
from .validate import Report, check_constraints, validate_template

log = structlog.get_logger(__name__)

#: Availability is checked per scope, and each scope costs one query. A sweep over
#: region x universe can name many; check the first few and say so rather than
#: stalling the editor on a keystroke.
MAX_CHECKED_SCOPES = 6

#: How many expanded expressions a preview returns. Enough to see the shape of the
#: sweep without shipping five hundred strings to a text editor.
PREVIEW_SAMPLE = 24


class Review:
    """Everything known about a template before it costs anything."""

    def __init__(
        self,
        spec: TemplateSpec,
        report: Report,
        prepared: Prepared | None,
        expansion: Expansion | None,
    ) -> None:
        self.spec = spec
        self.report = report
        self.prepared = prepared
        self.expansion = expansion

    def to_dict(self, *, sample: int = PREVIEW_SAMPLE) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.spec.name,
            "expr": self.spec.expr,
            "validation": self.report.to_dict(),
            "variables": sorted(self.spec.vars),
            "unusedVariables": sorted(self.spec.unused_vars),
            "sweptSettings": self.spec.swept_settings,
        }
        if self.prepared is not None:
            payload["resolution"] = self.prepared.to_dict()
        if self.expansion is None:
            payload["expansion"] = None
            return payload

        payload["expansion"] = {
            **self.expansion.packing_summary(),
            "totalPossible": self.expansion.total_possible,
            "truncated": self.expansion.truncated,
            "rejected": len(self.expansion.rejected),
            "rejectedSample": self.expansion.rejected[:sample],
            "sample": [
                {
                    "expression": request.regular,
                    "settings": request.settings.model_dump(by_alias=True, exclude_none=True),
                    "assignment": assignment,
                }
                for request, assignment in zip(
                    self.expansion.requests[:sample],
                    self.expansion.assignments[:sample],
                    strict=True,
                )
            ],
        }
        return payload


class Studio:
    """Composes the catalog, the platform metadata cache and the template modules."""

    def __init__(self, queries: CatalogQueries, auth: AuthService) -> None:
        self.resolver = Resolver(queries)
        self.auth = auth

    async def review(
        self,
        document: str | dict[str, Any] | TemplateSpec,
        *,
        expand_grid: bool = True,
    ) -> Review:
        """Parse, resolve, validate and (optionally) expand.

        Never raises for a template that is merely *wrong* — that is what the report is
        for. It raises only when the document cannot be parsed at all, because there is
        then nothing to report on.
        """
        spec = document if isinstance(document, TemplateSpec) else parse(document)

        prepared: Prepared | None = None
        resolution_error: str | None = None
        try:
            prepared = await self.resolver.prepare(spec)
        except ValueError as exc:
            resolution_error = str(exc)

        report = await self.validate(spec)
        if resolution_error:
            report.error(resolution_error, "vars")
        if prepared is not None:
            for name, resolution in prepared.named.items():
                if not resolution.values:
                    report.error(
                        f"${name} matched no fields in the synced catalog. Loosen its "
                        "filters, or sync this scope in the Data Explorer first.",
                        f"vars.{name}",
                    )
                elif resolution.dropped:
                    where = ", ".join(sorted(resolution.dropped))
                    report.warn(
                        f"${name} lost {resolution.dropped_count} field(s) because they do "
                        f"not exist in {where}. A template only uses fields present in "
                        "every scope it sweeps.",
                        f"vars.{name}",
                    )

        expansion: Expansion | None = None
        if expand_grid and report.ok:
            try:
                expansion = self.expand(spec, prepared)
            except ValueError as exc:
                report.error(str(exc), "settings")

        return Review(spec, report, prepared, expansion)

    async def validate(self, spec: TemplateSpec) -> Report:
        """Check a template against cached platform metadata and the local catalog."""
        operators = await self.auth.cached_operators()
        # Only operators published for this alpha type: combo_a and reduce_* exist on the
        # platform but are rejected inside a REGULAR expression.
        kind = spec.type.upper()
        known = (
            {
                str(o.get("name"))
                for o in operators
                if not isinstance(o.get("scope"), list) or not o["scope"] or kind in o["scope"]
            }
            if operators
            else None
        )
        schema = await self.auth.cached_settings_schema()

        by_scope: dict[str, set[str]] = {}
        for scope in scopes_of(spec)[:MAX_CHECKED_SCOPES]:
            available = await self.resolver.available(scope)
            # An unsynced scope is empty, and reporting every field as missing would be
            # noise dressed as an error. Say the one useful thing instead.
            if available:
                by_scope[scope.label] = available

        report = validate_template(
            spec,
            known_operators=known,
            settings_schema=schema,
            available_by_scope=by_scope or None,
        )

        unsynced = [
            s.label for s in scopes_of(spec)[:MAX_CHECKED_SCOPES] if s.label not in by_scope
        ]
        if unsynced:
            report.warn(
                "Not synced yet: "
                + ", ".join(unsynced)
                + ". Field availability cannot be checked there, so a missing field will "
                "only show up when the simulation fails.",
                "settings",
            )
        return report

    def expand(self, spec: TemplateSpec, prepared: Prepared | None) -> Expansion:
        """Enumerate the grid using already-resolved fields."""
        return expand(
            spec,
            resolve_fields=prepared,
            check=lambda expression, _assignment: check_constraints(spec, expression),
        )
