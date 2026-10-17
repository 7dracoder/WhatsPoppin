"""Tesla Filing Analyst: answers from the "Tesla Research Corpus" in PRISM's Knowledge Base.

Each question is one traced run, sent to PRISM under agent `tesla-filing-analyst`:

    knowledge_base.search  (retrieval)  the corpus chunks PRISM's Knowledge Base returned
    llm.answer             (llm)        nicrron/auto with the v1 or v2 instruction
    check.numbers_supported (tool)      every number in the answer must appear in the retrieved
                                        chunks and sit next to a source ID; if not, the step and
                                        the run fail with UnsupportedFinancialClaim

    uv run python -m backend.research.tesla_analyst --version v2 "How did deliveries change?"
    uv run python -m backend.research.tesla_analyst --version v1 --suite   # the three test prompts
"""

import argparse
import os
import re
import threading

import httpx
from dotenv import load_dotenv

from backend.llm.client import post_with_retry, resolve_endpoint
from backend.observability import trace_run

AGENT_ID = "tesla-filing-analyst"
KNOWLEDGE_BASE = "Tesla Research Corpus"
MODEL = "nicrron/auto"

INSTRUCTIONS = {
    "v1": "Write a polished Tesla investment summary. Answer confidently and concisely. Citations are optional.",
    "v2": """You are a source-grounded financial research analyst.

Use only retrieved Knowledge Base content. Before answering, retrieve the passages needed to support every factual claim.

For every numeric claim:
1. State the metric, value, unit, and fiscal period.
2. Attach the exact source ID from the retrieved material.
3. Do not use a number if its source, metric, or period is missing.
4. If evidence is insufficient, say "Insufficient retrieved evidence."
5. Report only values that appear verbatim in the retrieved content. Never compute new numbers (differences, percentages, ratios, margins); describe the direction of change in words instead.

Return:
- a short answer;
- a "Claims and evidence" list;
- one citation for every numeric claim.

Never use outside knowledge or estimates.""",
}

SUITE = [
    "Compare Tesla's 2023 and 2024 revenue, GAAP net income, and deliveries.",
    "What was Tesla's GAAP net income in 2020 and 2024? Explain the difference.",
    "How did Tesla's vehicle deliveries change between 2023 and 2024?",
]

NUMBER = re.compile(r"(?<![\w.#])\$?\d[\d,]*(?:\.\d+)?%?(?!-[KQ])")  # not form names (10-K) or chunk refs (#0)
SOURCE_ID = re.compile(r"\bS\d\b")
CLAIM_START = re.compile(r"\s*(?:[-*|]|\d+\.)\s|\s*#")  # a list item, table row or heading starts a new claim


def search_knowledge_base(query: str, limit: int = 4) -> list[dict]:
    resp = httpx.post(
        f"{os.environ.get('PRISM_HOST', 'https://prism.blockconvey.com').rstrip('/')}/api/knowledge-base/search",
        headers={"X-PRISMtrace-Key": os.environ["PRISM_API_KEY"]},
        json={"project_id": os.environ["PRISM_PROJECT_ID"], "agent_id": AGENT_ID, "query": query, "limit": limit},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["results"]


def answer(instruction: str, question: str, chunks: list[dict]) -> str:
    url, key, model = resolve_endpoint(MODEL)
    context = "\n\n".join(f"[{c['doc_name']} #{c['chunk_index']}]\n{c['content']}" for c in chunks)
    with httpx.Client() as client:
        resp = post_with_retry(client, url, headers={"Authorization": f"Bearer {key}"}, timeout=60, json={
            "model": model,
            "messages": [{"role": "system", "content": instruction},
                         {"role": "user", "content": f"RETRIEVED KNOWLEDGE BASE CONTENT:\n{context}\n\nQUESTION: {question}"}],
        })
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _digits(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3})", "", text)


def claims(text: str) -> list[str]:
    """The answer split into claims: a list item or table row with its continuation lines, or a paragraph."""
    units: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            units.append("")
        elif CLAIM_START.match(line) or not units or not units[-1] or units[-1].lstrip().startswith("|"):
            units.append(line)
        else:
            units[-1] += "\n" + line
    return [u for u in units if u]


def unsupported_numbers(text: str, sources: str) -> list[dict]:
    """Numbers in `text` that are not in `sources` verbatim, or have no source ID in the same claim.
    Years (1990-2039) are periods, not claims, so they are not checked."""
    corpus = _digits(sources)
    problems = []
    for claim in claims(text):
        cited = bool(SOURCE_ID.search(claim))
        for raw in NUMBER.findall(re.sub(r"(?m)^\s*\d+\.\s", "", claim)):  # not list numbering
            value = _digits(raw.strip("$%,"))
            if re.fullmatch(r"(199|20[0-3])\d", value) or value in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                continue
            in_source = re.search(rf"(?<![\d.]){re.escape(value)}(?![\d])", corpus) is not None
            if not (in_source and cited):
                problems.append({"number": raw, "in_source": in_source, "cited": cited})
    return problems


def run(version: str, question: str) -> dict:
    with trace_run("tesla_analyst.answer", inputs={"message": question}, agent_id=AGENT_ID,
                   agent_name="Tesla Filing Analyst", prompt_version=version, knowledge_base=KNOWLEDGE_BASE) as r:
        with r.step("knowledge_base.search", kind="retrieval", knowledge_base=KNOWLEDGE_BASE) as s:
            s.input = {"knowledge_base": KNOWLEDGE_BASE, "agent_id": AGENT_ID, "query": question}
            chunks = search_knowledge_base(question)
            s.output = [{"knowledge_base": KNOWLEDGE_BASE, "document": c["doc_name"], "chunk": c["chunk_index"],
                         "score": c["relevance_score"], "content": c["content"]} for c in chunks]
        with r.step("llm.answer", kind="llm", model=MODEL, prompt_version=version) as s:
            s.input = {"instruction": INSTRUCTIONS[version], "question": question}
            reply = answer(INSTRUCTIONS[version], question, chunks)
            s.output = reply
        r.output = {"reply": reply}
        with r.step("check.numbers_supported", kind="tool") as s:
            problems = unsupported_numbers(reply, "\n".join(c["content"] for c in chunks))
            s.output = {"unsupported": problems, "passed": not problems}
            if problems:
                s.status = r.status = "error"
                s.error = {"type": "UnsupportedFinancialClaim",
                           "message": f"{len(problems)} number(s) not traceable to a cited retrieved source: "
                                      + ", ".join(p["number"] for p in problems[:8])}
                r.error = {**s.error, "step": "check.numbers_supported"}
    return {"run_id": r.id, "version": version, "question": question, "status": r.status,
            "unsupported": problems, "reply": reply}


def main() -> None:
    load_dotenv(".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", choices=INSTRUCTIONS, default="v2")
    parser.add_argument("--suite", action="store_true", help="run the three test prompts")
    parser.add_argument("question", nargs="?")
    args = parser.parse_args()
    for question in SUITE if args.suite else [args.question]:
        result = run(args.version, question)
        print(f"\n=== {result['version']} | {result['status']} | run {result['run_id']}\nQ: {question}\n{result['reply']}")
        if result["unsupported"]:
            print("UNSUPPORTED:", result["unsupported"])
    for t in threading.enumerate():  # PRISM exports run on daemon threads; let them finish
        if t is not threading.main_thread():
            t.join(timeout=30)


if __name__ == "__main__":
    main()
