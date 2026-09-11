"""Which labs exist, and which you can use right now.

The front door. A consultant who will give this ten minutes should see a short list of
things to press, each with one line saying what it does — and never a door that opens
onto an error.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..labs.registry import LABS_BY_ID, Lab, LabsListing
from .deps import State

router = APIRouter(prefix="/api/labs", tags=["labs"])


@router.get("")
async def labs(state: State) -> LabsListing:
    """Every lab, in the order to offer them, each saying whether it is ready.

    ``suggested`` is what to press when you have no idea — always a ready lab that
    actually spends the allowance, because an unused allowance is the problem this
    application exists to solve.
    """
    return await state.labs.list()


@router.get("/{lab_id}")
async def lab(lab_id: str, state: State) -> Lab:
    from fastapi import HTTPException

    entry = LABS_BY_ID.get(lab_id)
    if entry is None:
        raise HTTPException(
            404,
            detail={
                "code": "no_such_lab",
                "message": f"There is no lab called {lab_id!r}.",
                "available": sorted(LABS_BY_ID),
            },
        )
    listing = await state.labs.list()
    return next(e for e in listing.labs if e.id == lab_id)
