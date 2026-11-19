"""Single source of truth for `document_kind`.

The generator used to fall back to ``textbook`` for an unknown kind while the
validator and the refiner fell back to ``normative``. A typo'd kind therefore
produced a self-contained question that was then graded under rules demanding a
source citation — a systematic failure that looked like a model problem.
"""

from __future__ import annotations

from typing import Final, FrozenSet, Optional

DEFAULT_DOCUMENT_KIND: Final[str] = "normative"
DOCUMENT_KINDS: Final[FrozenSet[str]] = frozenset(
    {"normative", "textbook", "encyclopedia"}
)


def normalize_document_kind(kind: Optional[str]) -> str:
    """Return a known document kind, falling back to the default."""
    normalized = (kind or "").strip().lower()
    return normalized if normalized in DOCUMENT_KINDS else DEFAULT_DOCUMENT_KIND
