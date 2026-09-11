"""The labs: the handful of ways to turn a day's allowance into alphas.

Everything this application can do is one of a few generators — explore new data, let
the assistant invent expressions, tune something that already works, combine alphas you
already own. Each is wrapped as a **lab**: a name a ten-year-old understands, one line
saying what it does, and a handful of choices.

Two things make this a registry rather than four pages of navigation.

**A lab that cannot run must not be offered.** Combining needs alphas with stored
returns; inventing needs an assistant key; anything touching data needs a synced scope.
Showing a locked door to someone who will give the app ten minutes is how you lose them,
so every lab reports whether it is ready and, if not, the single thing to do about it.

**The choices are the diversity.** Five hundred consultants share one platform, and if
they all press the same button on the same defaults the compute is spent producing the
same alphas. So each lab declares how large its own option space is, and the front door
can lead with the ones that spread people out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

import structlog
from pydantic import ConfigDict, Field

from ..schemas import Out

log = structlog.get_logger(__name__)


class Requirement(Out):
    """Something a lab needs before it can run, and how to get it."""

    model_config = ConfigDict(frozen=True)

    key: str
    #: What is missing, said plainly.
    missing: str
    #: The one action that fixes it.
    fix: str


class Lab(Out):
    """One direction of research.

    Both the authoring format for the table below and the wire shape the front door
    reads. ``needs`` and ``order`` drive that assembly and are excluded from the
    response; ``ready`` and ``blockers`` are the other way round — filled per request by
    :meth:`LabRegistry.list`, since whether a door opens depends on the account.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    #: One line, on the card.
    tagline: str
    #: What it actually does, for someone with no finance background.
    explains: str
    #: When this is the right choice.
    good_for: str
    #: **The axis this lab searches.** Two labs exist separately only if these differ.
    #: Written for the PM, which reads it to decide what a day is missing.
    searches: str = ""
    #: What it deliberately leaves alone. The other half of orthogonality.
    holds_fixed: str = ""
    #: Requirement keys that must be satisfied. Internal: the response carries the
    #: resolved ``blockers`` instead, which say what to do rather than what is missing.
    needs: tuple[str, ...] = Field(default=(), exclude=True)
    #: How many decisions it asks of you. Fewer is better; shown so the tired can pick.
    choices: int = 0
    #: Whether it spends simulation quota. Combining predicts before it spends.
    spends_quota: bool = True
    #: ``lab`` is a direction of research. ``tool`` is a way of feeding one — it belongs
    #: on the page but is not an axis of the search, so it never receives cores.
    kind: str = "lab"
    #: Lower sorts first. Internal: the response is already in the intended order.
    order: int = Field(default=100, exclude=True)
    endpoints: dict[str, str] = Field(default_factory=dict)

    #: Filled per request.
    ready: bool = False
    blockers: tuple[Requirement, ...] = ()


class LabsListing(Out):
    """Every lab, plus what to press and what is standing in the way."""

    labs: list[Lab]
    ready: int
    suggested: str | None
    unlock: Requirement | None
    requirements: dict[str, bool]


REQUIREMENTS: dict[str, Requirement] = {
    "signed-in": Requirement(
        key="signed-in",
        missing="You are not signed in to BRAIN.",
        fix="Sign in with your BRAIN email and password.",
    ),
    "catalog": Requirement(
        key="catalog",
        missing="No market data has been downloaded yet.",
        fix="Pick a market and let it download. It takes a while and only happens once.",
    ),
    "assistant": Requirement(
        key="assistant",
        missing="The assistant has no key.",
        fix="Add a free Google AI Studio key. It takes a minute.",
    ),
    "alphas": Requirement(
        key="alphas",
        missing="You have no finished alphas to work with yet.",
        fix="Run one of the other labs first. Come back when it has finished.",
    ),
}


#: The research directions. Two labs are separate only if they search a different axis
#: of ``expression(fields, operators, parameters) + settings`` and neither subsumes the
#: other. Splitting on *method* rather than direction is forbidden: it inflates the menu
#: without widening the search. See INTENT.md for what was deliberately not split out.
LABS: tuple[Lab, ...] = (
    Lab(
        id="sweep",
        name="Go exploring",
        tagline="Try one proven idea across data nobody has looked at.",
        explains=(
            "Takes the most usable piece of information out of each dataset and tries a "
            "handful of simple, proven shapes on every one of them. This is how a day's "
            "allowance becomes hundreds of alphas without you writing anything."
        ),
        good_for="Your first run, and any day you do not know where to start.",
        searches="which data field",
        holds_fixed="the shape of the idea, its settings",
        needs=("signed-in", "catalog"),
        choices=4,
        order=10,
        endpoints={
            "options": "/api/harvest/patterns",
            "preview": "/api/harvest/preview",
            "run": "/api/harvest/run",
        },
    ),
    Lab(
        id="pair",
        name="Put two together",
        tagline="What one number says next to another.",
        explains=(
            "Takes two pieces of information from different datasets and tries the ways "
            "they can be related — one divided by the other, the gap between them, how "
            "tightly they have been moving together. A raw number is not comparable "
            "between a big company and a small one; a ratio is, which is why most of "
            "the ideas that keep working are built this way."
        ),
        good_for="After exploring, when single numbers have been worked through.",
        searches="two data fields at once, and what sits between them",
        holds_fixed="the ways two fields can be related, the settings",
        needs=("signed-in", "catalog"),
        choices=3,
        order=15,
        endpoints={
            "options": "/api/pair/shapes",
            "preview": "/api/pair/preview",
            "run": "/api/pair/run",
        },
    ),
    Lab(
        id="invent",
        name="Let it invent",
        tagline="It writes the formulas itself, and breeds the ones that worked.",
        explains=(
            "Builds formulas nobody wrote down — a piece of data, something that makes "
            "it comparable over time, something that compares it across companies. Each "
            "run starts from the ones that already worked here, crosses and changes "
            "them, and throws in a few from nothing so it never settles."
        ),
        good_for="When the usual shapes have been worked and you want something new.",
        searches="the structure of the formula itself",
        holds_fixed="nothing — this is the widest lab",
        needs=("signed-in", "catalog"),
        choices=3,
        order=20,
        endpoints={
            "options": "/api/invent/grammar",
            "preview": "/api/invent/preview",
            "run": "/api/invent/run",
        },
    ),
    Lab(
        id="deepen",
        name="Tune an idea",
        tagline="Take one idea that works and find its best settings.",
        explains=(
            "Tries hundreds of variations of a single idea, learning from each round "
            "which direction to go next. Slower than exploring, but it digs."
        ),
        good_for="When exploring found something promising and you want the best version.",
        searches="the numbers inside an idea — windows, decay, truncation",
        holds_fixed="the shape, the data field",
        needs=("signed-in", "catalog"),
        choices=3,
        order=30,
        endpoints={
            "templates": "/api/templates",
            "options": "/api/studies/options",
            "create": "/api/studies",
        },
    ),
    Lab(
        id="combine",
        name="Combine what you have",
        tagline="Two mediocre alphas can make one good one.",
        explains=(
            "Looks through everything you have already run for pairs and trios that make "
            "money at different times, and works out what combining them would score — "
            "before spending a single simulation on it. A hundred ordinary alphas that "
            "disagree with each other beat three brilliant ones that agree."
        ),
        good_for="After a big run, when you have plenty of finished alphas.",
        searches="which alphas you already own go well together",
        holds_fixed="everything — it writes no new formulas",
        needs=("signed-in", "alphas"),
        choices=2,
        spends_quota=False,
        order=40,
        endpoints={
            "candidates": "/api/vault/mix/candidates",
            "run": "/api/vault/mix/run",
        },
    ),
    Lab(
        id="relocate",
        name="Try it somewhere else",
        tagline="An idea that works here may work in another market.",
        explains=(
            "Takes an alpha that already works and runs the very same formula in other "
            "markets, universes and groupings. Nothing about the idea changes — only "
            "where it is pointed. It is the cheapest good alpha you will find."
        ),
        good_for="As soon as anything works at all. Almost free, and often surprising.",
        searches="market, universe, grouping, delay",
        holds_fixed="the formula, exactly",
        needs=("signed-in", "catalog", "alphas"),
        choices=2,
        order=50,
        endpoints={
            "preview": "/api/relocate/preview",
            "run": "/api/relocate/run",
        },
    ),
    Lab(
        id="repair",
        name="Fix the near misses",
        tagline="Most good ideas fail on one thing. Change that one thing.",
        explains=(
            "Finds the alphas that passed every test but one, reads which test they "
            "failed, and makes the change that answers it — trading less often, "
            "spreading the money wider, comparing each company against ones its own "
            "size. The idea is left exactly as it was."
        ),
        good_for="When results keep coming back with one thing wrong.",
        searches="the fix for what a near miss failed — turnover, sub-universe, weight, "
        "correlation, sign",
        holds_fixed="the idea and its data field",
        needs=("signed-in", "alphas"),
        choices=2,
        order=55,
        endpoints={
            "options": "/api/repair/checks",
            "preview": "/api/repair/preview",
            "run": "/api/repair/run",
        },
    ),
    Lab(
        id="harden",
        name="Check it is real",
        tagline="Make sure a good result is not a fluke.",
        explains=(
            "Takes something that scored well and nudges it — a slightly different "
            "window, the outliers removed, the values ranked. A real idea barely "
            "notices. A fluke falls apart, and finding that out now costs one simulation "
            "instead of a submission."
        ),
        good_for="Before you submit anything. This is what keeps the good ones good.",
        searches="the neighbourhood around a result",
        holds_fixed="the idea's skeleton",
        needs=("signed-in", "alphas"),
        choices=1,
        order=60,
        endpoints={
            "preview": "/api/harden/preview",
            "run": "/api/harden/run",
        },
    ),
    Lab(
        id="diversify",
        name="Find something different",
        tagline="Deliberately look where your own alphas are not.",
        explains=(
            "Reads what you already own, works out which parts of the data you have "
            "leaned on, and searches the parts you have not. An alpha too similar to "
            "your existing ones cannot be submitted however good it is, so this looks "
            "for difference rather than for quality."
        ),
        good_for="When good results keep getting rejected for being too alike.",
        searches="the data furthest from what you already own",
        holds_fixed="nothing — but the target is distance, not score",
        needs=("signed-in", "catalog", "alphas"),
        choices=2,
        order=70,
        endpoints={
            "preview": "/api/diversify/preview",
            "run": "/api/diversify/run",
        },
    ),
    Lab(
        id="ask",
        name="Ask the assistant",
        tagline="Describe a hunch. It tells you where in the data to look.",
        explains=(
            "You type an idea in your own words — 'companies people are angry about "
            "online' — and it points at the data that could measure it, then picks the "
            "fields for you. It has read the whole catalogue and you have not."
        ),
        good_for="When you have a thought but no idea what it is called.",
        searches="nothing on its own — it aims the other labs",
        holds_fixed="",
        needs=("signed-in", "assistant"),
        choices=1,
        spends_quota=False,
        kind="tool",
        order=80,
        endpoints={
            "chat": "/api/chat",
            "models": "/api/llm/models",
        },
    ),
)

LABS_BY_ID = {lab.id: lab for lab in LABS}


class LabRegistry:
    """Which labs exist, and which of them you can actually use right now."""

    def __init__(self, state: Any) -> None:
        self.state = state

    async def satisfied(self) -> dict[str, bool]:
        """Check every requirement once, rather than per lab."""
        state = self.state
        signed_in = state.auth.session.authenticated

        keys = await state.llm.keys.list()
        assistant = any(k.enabled for k in keys)

        try:
            scopes = await state.queries.synced_tuples()
        except Exception:
            # A catalog that will not open is a missing catalog as far as the labs are
            # concerned; the Data screen is where that gets diagnosed.
            scopes = []

        try:
            counts = await state.alphas.counts()
            alphas = counts["withReturns"] >= 2
        except Exception:
            alphas = False

        return {
            "signed-in": signed_in,
            "assistant": assistant,
            "catalog": bool(scopes),
            "alphas": alphas,
        }

    async def list(self) -> LabsListing:
        """Every lab, in the order they should be offered."""
        satisfied = await self.satisfied()

        entries = []
        for lab in sorted(LABS, key=lambda lab: lab.order):
            blockers = tuple(
                REQUIREMENTS[key] for key in lab.needs if not satisfied.get(key, False)
            )
            entries.append(lab.model_copy(update={"ready": not blockers, "blockers": blockers}))

        ready = [e for e in entries if e.ready]

        # What to press when you have no idea. Only ever a lab that actually spends the
        # allowance — suggesting a chat to someone with 5,000 unused simulations would
        # be advice against the one thing this application exists to fix.
        suggested = next((e.id for e in ready if e.spends_quota), None)

        return LabsListing(
            labs=entries,
            ready=len(ready),
            suggested=suggested,
            # When nothing that spends the allowance is available, the useful answer is
            # not a different lab — it is the single action that unlocks one.
            unlock=None if suggested else self._unlock(entries),
            requirements=satisfied,
        )

    # ``Sequence`` rather than ``list``: this class has a method called ``list``, which
    # shadows the builtin when the annotation is resolved in class scope.
    def _unlock(self, entries: Sequence[Lab]) -> Requirement | None:
        """The one thing to do to make a quota-spending lab available.

        Whichever blocker stands in front of the most labs, so a single action opens as
        many doors as possible.
        """
        counts: Counter[str] = Counter(
            blocker.key for entry in entries if entry.spends_quota for blocker in entry.blockers
        )

        if not counts:
            return None
        # Signing in comes first when it is needed at all; nothing works without it.
        if "signed-in" in counts:
            return REQUIREMENTS["signed-in"]
        return REQUIREMENTS[counts.most_common(1)[0][0]]
