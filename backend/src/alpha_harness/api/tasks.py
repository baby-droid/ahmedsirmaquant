"""What is running right now.

One endpoint behind the indicator in the top bar. A catalog sync, a returns backfill and
a template sweep all report into the same registry, so "is anything happening?" has a
single answer instead of three places to look.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .deps import State

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
async def running(state: State) -> dict[str, Any]:
    """Everything in flight, plus the one-glance summary.

    ``progress`` averages only the tasks that can report it — a sync that knows it is
    40% done should not be dragged toward zero by one that genuinely cannot say.
    Finished tasks linger briefly so a job that completes between two polls is still
    seen rather than vanishing as though it never ran.
    """
    return await state.tasks.summary()
