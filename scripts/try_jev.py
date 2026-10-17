"""Manual smoke test for the classification/ library (Jev, via OpenRouter)
— run this directly to see real Jev answers printed to the terminal for
one example question of every mode, no backend/frontend needed.

Usage:
    OPENROUTER=your_key_here python scripts/try_jev.py
    # or add OPENROUTER=... to the repo's .env file and just run:
    python scripts/try_jev.py

Get a key at https://openrouter.ai (Account -> API keys). classify()
reads the env var OPENROUTER specifically (not OPENROUTER_API_KEY, which
is what .env.example currently names it — rename/add the line if you hit
"OPENROUTER API key not set").
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
    if os.environ.get("OPENROUTER"):
        return
    fallback = _load_dotenv_fallback("OPENROUTER")
    if fallback:
        os.environ["OPENROUTER"] = fallback
        return
    hint = ""
    if _load_dotenv_fallback("OPENROUTER_API_KEY"):
        hint = (
            "\nFound OPENROUTER_API_KEY in .env instead — classify() reads OPENROUTER, "
            "so add/rename a line to:\n  OPENROUTER=<same key>"
        )
    print(
        "No OPENROUTER key found (checked env and .env)." + hint + "\n"
        "Get one at https://openrouter.ai -> Account -> API keys, then either:\n"
        "  export OPENROUTER=your_key_here\n"
        "or add a line to .env:\n"
        "  OPENROUTER=your_key_here",
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
    # multi_select -> N independent Noul calls under the hood (Jev has no native multi-select)
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
