"""The uniform result shape every mode in modes.py interprets its Jev
answer(s) into. Split out from classifier.py so modes.py can import it
without a circular import (classifier.py imports modes.py to dispatch).
"""

from pydantic import BaseModel


class ClassificationResult(BaseModel):
    mode: str
    label: str | list[str] | None = None  # single label, list (multi_select), or None (raw score)
    probability: float | None = None  # noul/boolean, or choice's winning-label probability
    probabilities: dict[str, float] | None = None  # choice/multi_select/score, per-label or per-level
    score: float | None = None  # score mode's raw weighted position
    confidence: float | None = None
    raw: dict  # original Jev answer(s), for debugging/future modes
