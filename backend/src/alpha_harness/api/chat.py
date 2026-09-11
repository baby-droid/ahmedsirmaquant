"""Talking to the assistant.

The lab where diversity actually comes from. Four dropdowns give the same answer to
everyone who picks the same options; a sentence someone types in their own words does
not. Five hundred consultants describing five hundred hunches end up spread across the
data in a way no menu arranges.

What comes back is not advice — it is a list of **real data fields**, checked against the
catalogue, ready to hand to a lab that runs them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..catalog.queries import Tuple4
from ..llm.chat import DEFAULT_REASONING, reasoning_options
from .deps import State

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Scope(BaseModel):
    instrument_type: str = "EQUITY"
    region: str
    delay: int
    universe: str

    def to_tuple(self) -> Tuple4:
        return Tuple4(
            instrument_type=self.instrument_type,
            region=self.region,
            delay=self.delay,
            universe=self.universe,
        )


@router.get("/options")
async def options(state: State) -> dict[str, Any]:
    """The two choices a conversation offers: which model, and how hard to think."""
    return {
        "models": state.llm.registry.to_dict(),
        "reasoning": reasoning_options(),
        "defaultReasoning": DEFAULT_REASONING,
        "note": (
            "Thinking harder is not free — those tokens come out of the same daily "
            "budget as the answer. Normal is the right choice almost always."
        ),
    }


@router.get("/threads")
async def threads(state: State, limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "title": t.title,
            "scope": f"{t.region}/D{t.delay}/{t.universe}",
            "updatedAt": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in await state.chat.threads(limit)
    ]


@router.get("/threads/{thread_id}")
async def thread(thread_id: int, state: State) -> dict[str, Any]:
    found = await state.chat.thread(thread_id)
    if found is None:
        raise HTTPException(
            404, detail={"code": "no_such_thread", "message": "No such conversation."}
        )
    row, messages = found
    return {
        "id": row.id,
        "title": row.title,
        "scope": {
            "instrumentType": row.instrument_type,
            "region": row.region,
            "delay": row.delay,
            "universe": row.universe,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "text": m.text,
                "meta": m.meta,
                "createdAt": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: int, state: State) -> None:
    await state.chat.delete(thread_id)


class Say(BaseModel):
    """One message. Everything except the text has a sensible default."""

    text: str = Field(description="Your idea, in your own words")
    scope: Scope
    thread_id: int | None = Field(default=None, description="Omit to start a new conversation")
    model: str | None = None
    reasoning: str = Field(default=DEFAULT_REASONING, description="quick | normal | careful | deep")
    dataset_ids: list[str] = Field(
        default_factory=list, description="Narrow it to particular datasets"
    )


@router.post("")
async def say(body: Say, state: State) -> dict[str, Any]:
    """Send a message and get back a reply plus the fields it chose.

    Anything the assistant names that is not really in the catalogue is dropped before
    you see it and reported in ``dropped`` — an invented field costs a simulation to
    discover, and the consultant has a limited number each day.
    """
    return await state.chat.say(
        body.thread_id,
        body.text,
        scope=body.scope.to_tuple(),
        model=body.model,
        reasoning=body.reasoning,
        dataset_ids=body.dataset_ids,
    )
