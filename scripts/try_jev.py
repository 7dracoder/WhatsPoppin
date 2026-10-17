"""Manual smoke test for the classification/ library (Jev questions, via the
Nicrron gateway) — run this directly to see real answers printed to the
terminal for one example question of every mode, no backend/frontend needed.

Usage:
    NICRRON_API_KEY=your_key_here python scripts/try_jev.py
    # or add NICRRON_API_KEY=... to the repo's .env file and just run:
    python scripts/try_jev.py

Get a key at https://nicrron.ai.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Running this script directly (not via `python -m`) puts scripts/ on
# sys.path, not the repo root — add it so `classification` is importable.
sys.path.insert(0, str(REPO_ROOT))

from backend.classification import (  # noqa: E402
    BooleanSpec,
    ChoiceSpec,
    ClassificationResult,
    ClassificationSpec,
    JevError,
    MultiSelectSpec,
    ScoreSpec,
    SentimentSpec,
    classify,
)


def _load_dotenv_fallback(key: str) -> str | None:
    """Minimal .env reader — avoids adding python-dotenv as a dependency
    for a one-off script. Only used if the var isn't already in the
    environment."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip()
    return None


def check_api_key() -> None:
    if os.environ.get("NICRRON_API_KEY"):
        return
    fallback = _load_dotenv_fallback("NICRRON_API_KEY")
    if fallback:
        os.environ["NICRRON_API_KEY"] = fallback
        return
    print(
        "No NICRRON_API_KEY found (checked env and .env).\n"
        "Get one at https://nicrron.ai, then either:\n"
        "  export NICRRON_API_KEY=your_key_here\n"
        "or add a line to .env:\n"
        "  NICRRON_API_KEY=your_key_here",
        file=sys.stderr,
    )
    sys.exit(1)


@dataclass
class TestCase:
    mode: str
    title: str
    text: str
    spec: ClassificationSpec


TEST_CASES = [
    # sentiment -> Choice under the hood
    TestCase(
        mode="sentiment (positive)",
        title="Nvidia beats earnings estimates on record AI chip demand",
        text="NVDA reported revenue well above analyst expectations, driven by surging data-center GPU orders.",
        spec=SentimentSpec(),
    ),
    TestCase(
        mode="sentiment (negative)",
        title="US tightens export controls on advanced chips to China",
        text="New restrictions bar sales of Nvidia's top AI chips to Chinese customers, cutting into a major revenue segment.",
        spec=SentimentSpec(),
    ),
    # boolean -> Noul under the hood
    TestCase(
        mode="boolean",
        title="Nvidia beats earnings estimates on record AI chip demand",
        text="NVDA reported revenue well above analyst expectations, driven by surging data-center GPU orders.",
        spec=BooleanSpec(question="Does this suggest NVDA's next earnings report will beat expectations?"),
    ),
    # choice -> Choice under the hood
    TestCase(
        mode="choice",
        title="Fed holds interest rates steady, signals one cut in 2027",
        text="The Federal Reserve kept its benchmark rate unchanged, citing persistent but easing inflation.",
        spec=ChoiceSpec(
            question="What type of event does this article describe?",
            labels={
                "fed-decision": "a Federal Reserve rate/policy decision",
                "earnings": "a company's quarterly earnings report",
                "tariff-sanctions": "a tariff, export control, or sanctions action",
                "other": "none of the above",
            },
        ),
    ),
    # multi_select -> N independent Noul questions in one call (Jev has no native multi-select)
    TestCase(
        mode="multi_select",
        title="US tightens export controls on advanced chips to China",
        text="New restrictions bar sales of Nvidia's top AI chips to Chinese customers, affecting foundry partners like TSMC.",
        spec=MultiSelectSpec(
            labels={
                "affects_downstream_distributors": "companies that sell/resell the end product",
                "affects_upstream_suppliers": "companies that supply components/manufacturing",
                "affects_regulators_policy": "government or regulatory bodies",
            }
        ),
    ),
    # score -> Score under the hood
    TestCase(
        mode="score",
        title="US tightens export controls on advanced chips to China",
        text="New restrictions bar sales of Nvidia's top AI chips to Chinese customers, cutting into a major revenue segment.",
        spec=ScoreSpec(
            question="How urgent is this news for a trader holding NVDA?",
            levels=["low", "medium", "high"],
        ),
    ),
]


def print_result(case: TestCase, result: ClassificationResult) -> None:
    print(f"[{case.mode}] {case.title}")
    print(f"  label:         {result.label}")
    if result.probability is not None:
        print(f"  probability:   {result.probability:.2f}")
    if result.probabilities is not None:
        probs = ", ".join(f"{k}={v:.2f}" for k, v in result.probabilities.items())
        print(f"  probabilities: {probs}")
    if result.score is not None:
        print(f"  score:         {result.score:.2f}")
    if result.confidence is not None:
        print(f"  confidence:    {result.confidence:.2f}")
    print()


def main() -> None:
    check_api_key()

    for case in TEST_CASES:
        try:
            result = classify(case.title, case.text, case.spec)
        except JevError as exc:
            print(f"[{case.mode}] Jev call failed: {exc}\n")
            continue
        print_result(case, result)


if __name__ == "__main__":
    main()
