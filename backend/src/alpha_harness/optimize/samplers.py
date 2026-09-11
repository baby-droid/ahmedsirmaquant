"""Choosing a search strategy, and making it batch properly.

The platform runs eight concurrent simulations, each carrying up to ten children — so
work arrives eighty at a time when it packs. Optimizers, though, are written for the
opposite rhythm: ask for one point, learn from it, ask for a better one. Asking for
eighty points before learning anything from any of them is *not free*, and it costs
different samplers different amounts:

* **Population samplers (NSGA-II, NSGA-III)** pay nothing. A generation is evaluated as
  a block by design, so a population that is a multiple of the batch size fills whole
  batches and wastes no slot. This is the natural fit and the default.
* **TPE** pays real sample efficiency. Eighty points drawn from one unchanged posterior
  are eighty near-duplicates. ``constant_liar=True`` exists for exactly this: pending
  trials are temporarily assumed to have come back poor, so the next ask moves away from
  them. It is forced on whenever the batch is larger than one.
* **Random, QMC, Grid** are indifferent — they never look at results.
* **CMA-ES** is single-objective only and manages its own population internally.

None of that is hidden. Every sampler here carries the sentence a researcher needs to
choose between them, and :func:`build` states in ``notes`` what it changed and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from ..schemas import camel_dict
from .errors import StudyError

log = structlog.get_logger(__name__)

#: A multi-simulation carries ten children.
BATCH_SIZE = 10
#: Concurrent simulations, so a full round of work is eighty.
SLOTS = 8


@dataclass(frozen=True, slots=True)
class SamplerInfo:
    """One search strategy, described for someone choosing between them."""

    name: str
    label: str
    #: One sentence, shown as the tooltip. Written for a researcher, not a statistician.
    summary: str
    #: What it costs when asked for a whole batch at once.
    batching: str
    multi_objective: bool
    #: Sensible for a first study.
    recommended: bool = False
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return camel_dict(self)


SAMPLERS: dict[str, SamplerInfo] = {
    "nsga3": SamplerInfo(
        name="nsga3",
        label="NSGA-III",
        summary=(
            "Evolves a whole population at once, keeping a spread of good trade-offs "
            "rather than one winner. Best when you are optimising three or more things "
            "and want the choice between them left open."
        ),
        batching=(
            "Free. A generation is evaluated as a block, so a population that is a "
            "multiple of ten fills whole batches."
        ),
        multi_objective=True,
        recommended=True,
        params={"population_size": 80, "mutation_prob": None, "crossover_prob": 0.9},
    ),
    "nsga2": SamplerInfo(
        name="nsga2",
        label="NSGA-II",
        summary=(
            "The same idea as NSGA-III but tuned for two objectives — typically Sharpe "
            "against turnover. Simpler and slightly faster to converge on two."
        ),
        batching="Free, for the same reason as NSGA-III.",
        multi_objective=True,
        recommended=True,
        params={"population_size": 80, "mutation_prob": None, "crossover_prob": 0.9},
    ),
    "tpe": SamplerInfo(
        name="tpe",
        label="TPE (Bayesian)",
        summary=(
            "Builds a model of which parameter values worked and concentrates its guesses "
            "there. The most sample-efficient choice when simulations are scarce, which "
            "on a daily quota they are."
        ),
        batching=(
            "Costs adaptivity. Points asked for together cannot learn from each other, so "
            "constant_liar is switched on to keep a batch from collapsing into near "
            "duplicates. A smaller batch searches better; a larger one searches faster."
        ),
        multi_objective=True,
        params={"n_startup_trials": 20, "multivariate": True, "group": True},
    ),
    "cmaes": SamplerInfo(
        name="cmaes",
        label="CMA-ES",
        summary=(
            "Strong on smooth, continuous parameters such as decay and truncation. "
            "Optimises one number only — pick a single objective before choosing it."
        ),
        batching="Free. It works in populations of its own.",
        multi_objective=False,
        params={"n_startup_trials": 10, "sigma0": None},
    ),
    "qmc": SamplerInfo(
        name="qmc",
        label="Quasi-random (Sobol)",
        summary=(
            "Covers the search space far more evenly than chance does. A good first pass "
            "to map the terrain before letting a smarter sampler refine it."
        ),
        batching="Free. It never looks at results.",
        multi_objective=True,
        params={"qmc_type": "sobol", "scramble": True},
    ),
    "random": SamplerInfo(
        name="random",
        label="Random",
        summary=(
            "Draws uniformly at random. Not a strategy so much as a control — if a "
            "smarter sampler cannot beat this, the signal probably is not there."
        ),
        batching="Free. It never looks at results.",
        multi_objective=True,
        params={},
    ),
    "brute": SamplerInfo(
        name="brute",
        label="Exhaustive",
        summary=(
            "Tries every combination exactly once, then stops. Honest and complete, but "
            "only viable when the grid is small — check the expansion count first."
        ),
        batching="Free, and it will never repeat a point.",
        multi_objective=True,
        params={},
    ),
}

DEFAULT_SAMPLER = "nsga3"


def catalogue() -> list[dict[str, Any]]:
    """The sampler list the UI renders, tooltips included."""
    return [s.to_dict() for s in SAMPLERS.values()]


def round_population(size: int, batch_size: int = BATCH_SIZE) -> int:
    """Snap a population to a whole number of batches.

    A population of 75 with a batch of 10 leaves every generation ending in a
    half-filled multi-simulation, which wastes a concurrent slot for the whole of its
    run. 80 does not.

    Ties round up, rather than using Python's round-half-to-even — 145 becoming 140 for
    no visible reason is the kind of surprise that costs an afternoon.
    """
    if batch_size <= 1:
        return max(2, size)
    batches = (size + batch_size // 2) // batch_size
    return max(batch_size, batches * batch_size)


def build(
    name: str,
    *,
    batch_size: int = BATCH_SIZE,
    n_objectives: int = 1,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    search_space: dict[str, Any] | None = None,
) -> tuple[Any, list[str]]:
    """Construct a sampler, adjusted for how it will actually be used.

    Returns the sampler and the list of adjustments made, so the UI can show what was
    changed rather than silently overriding what the user picked.
    """
    from optuna.samplers import (
        BruteForceSampler,
        CmaEsSampler,
        NSGAIIISampler,
        NSGAIISampler,
        QMCSampler,
        RandomSampler,
        TPESampler,
    )

    info = SAMPLERS.get(name)
    if info is None:
        raise StudyError(
            f"{name!r} is not a sampler. Choose one of: {', '.join(sorted(SAMPLERS))}."
        )

    notes: list[str] = []
    options = {**info.params, **(params or {})}

    if n_objectives > 1 and not info.multi_objective:
        raise StudyError(
            f"{info.label} optimises a single number, but this study has {n_objectives} "
            "objectives. Choose NSGA-II, NSGA-III or TPE, or reduce to one objective."
        )

    # No ``constraints_func``: it is deprecated in Optuna 5 and, more to the point, it
    # is the wrong shape here. Constraints come from BRAIN's submission checks, which
    # are not reproducible locally and arrive already named — so they are attached to
    # each trial with ``set_constraint(name, value)`` and every sampler that understands
    # constraints picks them up from there.
    match name:
        case "nsga2" | "nsga3":
            requested = int(options.get("population_size") or batch_size * SLOTS)
            population = round_population(requested, batch_size)
            if population != requested:
                notes.append(
                    f"Population {requested} → {population} so each generation fills "
                    f"whole batches of {batch_size}."
                )
            cls = NSGAIISampler if name == "nsga2" else NSGAIIISampler
            sampler = cls(
                population_size=population,
                mutation_prob=options.get("mutation_prob"),
                crossover_prob=float(options.get("crossover_prob") or 0.9),
                seed=seed,
            )

        case "tpe":
            liar = batch_size > 1
            if liar:
                notes.append(
                    f"constant_liar on: {batch_size} trials are asked for before any "
                    "result comes back, and without it they would be near-identical."
                )
            sampler = TPESampler(
                n_startup_trials=int(options.get("n_startup_trials") or 20),
                multivariate=bool(options.get("multivariate", True)),
                group=bool(options.get("group", True)),
                constant_liar=liar,
                seed=seed,
            )

        case "cmaes":
            sampler = CmaEsSampler(
                n_startup_trials=int(options.get("n_startup_trials") or 10),
                sigma0=options.get("sigma0"),
                seed=seed,
            )

        case "qmc":
            sampler = QMCSampler(
                qmc_type=str(options.get("qmc_type") or "sobol"),
                scramble=bool(options.get("scramble", True)),
                seed=seed,
                warn_independent_sampling=False,
            )

        case "random":
            sampler = RandomSampler(seed=seed)

        case "brute":
            if search_space and _grid_size(search_space) > 10_000:
                notes.append(
                    "This grid is very large; an exhaustive search will not finish "
                    "within any reasonable daily quota."
                )
            sampler = BruteForceSampler(seed=seed)

        case _:  # pragma: no cover - guarded by the lookup above
            raise ValueError(f"Unhandled sampler {name!r}")

    log.info("optimize.sampler", name=name, batch_size=batch_size, notes=notes)
    return sampler, notes


def _grid_size(search_space: dict[str, Any]) -> int:
    total = 1
    for values in search_space.values():
        total *= max(1, len(values) if isinstance(values, list | tuple) else 1)
    return total
