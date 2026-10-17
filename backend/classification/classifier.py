"""Public entry point: classify(title, text, spec) -> ClassificationResult.

Operates on plain text, not a source's ContentItem class — same reasoning
as entities/matcher.py's EntityMatcher.match(title, text): news and
Twitter ContentItems are two separate, unrelated Pydantic classes, so any
ingestion source can use this without importing it.
"""

from .jev_client import call_jev
from .modes import MODE_HANDLERS, ClassificationSpec
from .results import ClassificationResult

__all__ = ["classify", "ClassificationResult"]


def classify(title: str, text: str | None, spec: ClassificationSpec) -> ClassificationResult:
    """Classify title+text against `spec` via Jev. Raises ValueError if
    spec.mode isn't registered in MODE_HANDLERS, or jev_client.JevError
    if the Jev call itself fails.
    """
    handlers = MODE_HANDLERS.get(spec.mode)
    if handlers is None:
        raise ValueError(f"Unregistered classification mode: {spec.mode!r}")
    build_questions, interpret = handlers

    state = f"{title}\n\n{text}" if text else title
    questions = build_questions(spec, state)
    answers = call_jev(state, questions)
    return interpret(spec, answers)
