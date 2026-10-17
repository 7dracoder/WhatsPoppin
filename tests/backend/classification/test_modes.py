from backend.classification.modes import (
    MODE_HANDLERS,
    BooleanSpec,
    ChoiceSpec,
    MultiSelectSpec,
    ScoreSpec,
    SentimentSpec,
)
from backend.classification.primitives import ChoiceAnswer, ChoiceQuestion, NoulAnswer, NoulQuestion, ScoreAnswer


def test_sentiment_builds_choice_question_with_three_labels():
    build, _ = MODE_HANDLERS["sentiment"]
    questions = build(SentimentSpec(), "some article text")

    assert list(questions.keys()) == ["main"]
    question = questions["main"]
    assert isinstance(question, ChoiceQuestion)
    assert set(question.criteria) == {"positive", "negative", "neutral"}


def test_sentiment_interprets_choice_answer():
    _, interpret = MODE_HANDLERS["sentiment"]
    answers = {
        "main": ChoiceAnswer(
            choice="negative",
            probabilities={"positive": 0.1, "negative": 0.8, "neutral": 0.1},
            confidence=0.9,
        )
    }

    result = interpret(SentimentSpec(), answers)

    assert result.mode == "sentiment"
    assert result.label == "negative"
    assert result.probability == 0.8
    assert result.confidence == 0.9


def test_boolean_builds_noul_question_from_spec_question():
    build, _ = MODE_HANDLERS["boolean"]
    spec = BooleanSpec(question="does this suggest NVDA earnings will beat expectations?")
    questions = build(spec, "state text")

    question = questions["main"]
    assert isinstance(question, NoulQuestion)
    assert question.instructions == spec.question


def test_boolean_interprets_noul_answer_with_yes_no_label():
    _, interpret = MODE_HANDLERS["boolean"]

    high = interpret(BooleanSpec(question="q"), {"main": NoulAnswer(noul=0.9)})
    low = interpret(BooleanSpec(question="q"), {"main": NoulAnswer(noul=0.1)})

    assert high.label == "yes"
    assert high.probability == 0.9
    assert low.label == "no"
    assert low.probability == 0.1


def test_choice_builds_choice_question_with_spec_labels():
    build, _ = MODE_HANDLERS["choice"]
    spec = ChoiceSpec(question="event type?", labels={"earnings": None, "fed-decision": None, "other": None})
    questions = build(spec, "state text")

    question = questions["main"]
    assert isinstance(question, ChoiceQuestion)
    assert question.instructions == spec.question
    assert question.criteria == spec.labels


def test_choice_interprets_choice_answer():
    _, interpret = MODE_HANDLERS["choice"]
    spec = ChoiceSpec(question="event type?", labels={"earnings": None, "other": None})
    answers = {"main": ChoiceAnswer(choice="earnings", probabilities={"earnings": 0.6, "other": 0.4}, confidence=0.7)}

    result = interpret(spec, answers)

    assert result.label == "earnings"
    assert result.probability == 0.6


def test_multi_select_builds_one_noul_question_per_label():
    build, _ = MODE_HANDLERS["multi_select"]
    spec = MultiSelectSpec(
        labels={"affects_downstream_distributors": "...", "affects_upstream_suppliers": None}
    )
    questions = build(spec, "state text")

    assert set(questions.keys()) == set(spec.labels)
    assert all(isinstance(q, NoulQuestion) for q in questions.values())


def test_multi_select_selects_labels_at_or_above_threshold():
    _, interpret = MODE_HANDLERS["multi_select"]
    spec = MultiSelectSpec(
        labels={"distributors": None, "suppliers": None, "unrelated": None}, threshold=0.5
    )
    answers = {
        "distributors": NoulAnswer(noul=0.9),
        "suppliers": NoulAnswer(noul=0.5),  # exactly at threshold -> selected
        "unrelated": NoulAnswer(noul=0.2),
    }

    result = interpret(spec, answers)

    assert sorted(result.label) == ["distributors", "suppliers"]
    assert result.probabilities == {"distributors": 0.9, "suppliers": 0.5, "unrelated": 0.2}


def test_score_builds_score_question_with_spec_levels():
    build, _ = MODE_HANDLERS["score"]
    spec = ScoreSpec(question="how urgent is this?", levels=["low", "medium", "high"])
    questions = build(spec, "state text")

    question = questions["main"]
    assert question.criteria == spec.levels
    assert question.instructions == spec.question


def test_score_interprets_score_answer():
    _, interpret = MODE_HANDLERS["score"]
    spec = ScoreSpec(question="q", levels=["low", "high"])
    answers = {
        "main": ScoreAnswer(
            score=1.2,
            legend={"0": "low", "1": "high"},
            probabilities={"0": 0.2, "1": 0.8},
            confidence=0.85,
        )
    }

    result = interpret(spec, answers)

    assert result.label is None
    assert result.score == 1.2
    assert result.confidence == 0.85


def test_multi_select_uses_custom_question_wording_when_given():
    build, _ = MODE_HANDLERS["multi_select"]
    spec = MultiSelectSpec(labels={"Tesla, Inc.": "also known as Tesla", "Gold": None},
                           question="Is this market about {label}?")

    questions = build(spec, "state")

    assert questions["Tesla, Inc."].instructions == "Is this market about Tesla, Inc.? (also known as Tesla)"
    assert questions["Gold"].instructions == "Is this market about Gold?"
