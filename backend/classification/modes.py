"""The configurable, modular layer on top of Jev's three wire-level
primitives (see primitives.py). Each mode is a `ClassificationSpec`
variant plus a `(build_questions, interpret)` pair registered in
MODE_HANDLERS. classify() (classifier.py) dispatches by `spec.mode` and
never branches on mode name itself — adding a new mode later means
adding one *Spec variant, one handler pair, and one registry line here;
nothing else in this package changes.

Jev's `Choice` primitive is single-select only (its docs: one `choice`
returned, probabilities sum to 1). There is no native multi-select.
TypeSafe's documented workaround for "does this item belong to several
of these categories at once" is to ask one independent Noul question per
category and combine the answers — `multi_select` below is exactly that,
built in as a first-class mode instead of left for every caller to
hand-roll.
"""

from typing import Literal

from pydantic import BaseModel

from .primitives import ChoiceQuestion, JevAnswer, JevQuestion, NoulQuestion, ScoreQuestion
from .results import ClassificationResult


class SentimentSpec(BaseModel):
    mode: Literal["sentiment"] = "sentiment"


class BooleanSpec(BaseModel):
    mode: Literal["boolean"] = "boolean"
    question: str


class ChoiceSpec(BaseModel):
    mode: Literal["choice"] = "choice"
    question: str
    labels: dict[str, str | None]  # label -> description, max 255


class MultiSelectSpec(BaseModel):
    mode: Literal["multi_select"] = "multi_select"
    labels: dict[str, str | None]  # label -> description, e.g. {"affects_downstream_distributors": "..."}
    threshold: float = 0.5
    question: str | None = None  # per-label wording with a {label} placeholder; None = the category question


class ScoreSpec(BaseModel):
    mode: Literal["score"] = "score"
    question: str
    levels: list[str]  # 2-10 ordered levels, low to high


ClassificationSpec = SentimentSpec | BooleanSpec | ChoiceSpec | MultiSelectSpec | ScoreSpec

_SENTIMENT_LABELS = {
    "positive": None,
    "negative": None,
    "neutral": None,
}
_MAIN_QUESTION_ID = "main"


def _build_sentiment(spec: SentimentSpec, state: str) -> dict[str, JevQuestion]:
    return {
        _MAIN_QUESTION_ID: ChoiceQuestion(
            instructions="What is the overall sentiment of this content?",
            criteria=_SENTIMENT_LABELS,
        )
    }


def _interpret_sentiment(spec: SentimentSpec, answers: dict[str, JevAnswer]) -> ClassificationResult:
    answer = answers[_MAIN_QUESTION_ID]
    return ClassificationResult(
        mode=spec.mode,
        label=answer.choice,
        probability=answer.probabilities[answer.choice],
        probabilities=answer.probabilities,
        confidence=answer.confidence,
        raw={_MAIN_QUESTION_ID: answer.model_dump()},
    )


def _build_boolean(spec: BooleanSpec, state: str) -> dict[str, JevQuestion]:
    return {_MAIN_QUESTION_ID: NoulQuestion(instructions=spec.question)}


def _interpret_boolean(spec: BooleanSpec, answers: dict[str, JevAnswer]) -> ClassificationResult:
    answer = answers[_MAIN_QUESTION_ID]
    return ClassificationResult(
        mode=spec.mode,
        label="yes" if answer.noul >= 0.5 else "no",
        probability=answer.noul,
        raw={_MAIN_QUESTION_ID: answer.model_dump()},
    )


def _build_choice(spec: ChoiceSpec, state: str) -> dict[str, JevQuestion]:
    return {_MAIN_QUESTION_ID: ChoiceQuestion(instructions=spec.question, criteria=spec.labels)}


def _interpret_choice(spec: ChoiceSpec, answers: dict[str, JevAnswer]) -> ClassificationResult:
    answer = answers[_MAIN_QUESTION_ID]
    return ClassificationResult(
        mode=spec.mode,
        label=answer.choice,
        probability=answer.probabilities[answer.choice],
        probabilities=answer.probabilities,
        confidence=answer.confidence,
        raw={_MAIN_QUESTION_ID: answer.model_dump()},
    )


def _build_multi_select(spec: MultiSelectSpec, state: str) -> dict[str, JevQuestion]:
    return {
        label: NoulQuestion(
            instructions=(spec.question.format(label=label) if spec.question
                          else f"Does this content apply to the category {label!r}?")
            + (f" ({description})" if description else "")
        )
        for label, description in spec.labels.items()
    }


def _interpret_multi_select(spec: MultiSelectSpec, answers: dict[str, JevAnswer]) -> ClassificationResult:
    probabilities = {label: answer.noul for label, answer in answers.items()}
    selected = [label for label, prob in probabilities.items() if prob >= spec.threshold]
    return ClassificationResult(
        mode=spec.mode,
        label=selected,
        probabilities=probabilities,
        raw={label: answer.model_dump() for label, answer in answers.items()},
    )


def _build_score(spec: ScoreSpec, state: str) -> dict[str, JevQuestion]:
    return {_MAIN_QUESTION_ID: ScoreQuestion(instructions=spec.question, criteria=spec.levels)}


def _interpret_score(spec: ScoreSpec, answers: dict[str, JevAnswer]) -> ClassificationResult:
    answer = answers[_MAIN_QUESTION_ID]
    return ClassificationResult(
        mode=spec.mode,
        label=None,
        score=answer.score,
        probabilities=answer.probabilities,
        confidence=answer.confidence,
        raw={_MAIN_QUESTION_ID: answer.model_dump()},
    )


MODE_HANDLERS = {
    "sentiment": (_build_sentiment, _interpret_sentiment),
    "boolean": (_build_boolean, _interpret_boolean),
    "choice": (_build_choice, _interpret_choice),
    "multi_select": (_build_multi_select, _interpret_multi_select),
    "score": (_build_score, _interpret_score),
}
