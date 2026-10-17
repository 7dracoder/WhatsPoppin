"""Wire-level types for Jev's (typesafe/jev-1.13, via OpenRouter) three
question primitives: Noul (yes/no -> probability), Choice (one label from
a set -> label + per-label probabilities), Score (ordered rubric ->
weighted position). 1:1 with the documented request/response contract —
no project-specific shaping here, that's modes.py's job.
"""

from typing import Literal

from pydantic import BaseModel


class NoulQuestion(BaseModel):
    type: Literal["noul"] = "noul"
    instructions: str


class ChoiceQuestion(BaseModel):
    type: Literal["choice"] = "choice"
    instructions: str
    criteria: dict[str, str | None]  # option -> description, max 255 options


class ScoreQuestion(BaseModel):
    type: Literal["score"] = "score"
    instructions: str
    criteria: list[str]  # 2-10 ordered levels, low to high


JevQuestion = NoulQuestion | ChoiceQuestion | ScoreQuestion


class NoulAnswer(BaseModel):
    type: Literal["noul"] = "noul"
    noul: float  # probability the answer is yes, 0-1


class ChoiceAnswer(BaseModel):
    type: Literal["choice"] = "choice"
    choice: str  # highest-probability option
    probabilities: dict[str, float]  # sums to 1
    confidence: float


class ScoreAnswer(BaseModel):
    type: Literal["score"] = "score"
    score: float  # probability-weighted position, can fall between levels
    legend: dict[str, str]  # level index -> description
    probabilities: dict[str, float]
    confidence: float


JevAnswer = NoulAnswer | ChoiceAnswer | ScoreAnswer

ANSWER_TYPES: dict[str, type[JevAnswer]] = {
    "noul": NoulAnswer,
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
}
