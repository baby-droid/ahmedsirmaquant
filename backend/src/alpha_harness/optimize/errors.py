"""Errors from setting up or running a study.

Its own module so :mod:`.samplers` and :mod:`.objectives` can raise it without importing
:mod:`.study`, which imports them. Everything here is a mistake in how a study was
configured — a sampler that cannot do what was asked, an objective that does not exist —
so it becomes a 4xx with the message intact, never a stack trace.
"""

from __future__ import annotations


class StudyError(ValueError):
    """A study configured in a way that cannot work.

    A sampler that cannot optimise the requested objectives, an objective that does not
    exist, a template with nothing to search. The request was understood and refused.
    """


class StudyNotFoundError(StudyError):
    """No such study. Separate because "does not exist" is not "cannot work"."""

    def __init__(self, study_id: int | str) -> None:
        super().__init__(f"No study {study_id!r}.")
        self.study_id = study_id
