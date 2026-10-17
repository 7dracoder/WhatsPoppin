"""`/assistant` router: the voice assistant ("Poppy") that can see and drive the whole app.

Three steps per spoken turn, each its own route so the browser can show progress:

    POST /assistant/transcribe   audio (webm/ogg/mp4/wav body)  -> {"text"}     ElevenLabs Scribe
    POST /assistant/ask          {message, context, history}    -> {reply, actions}   Nicrron
    POST /assistant/speak        {text}                          -> audio/mpeg   ElevenLabs TTS

`context` is a JSON snapshot of what is on screen (the stage, the company and its graph, the
visible markets, the chart, the evidence) built by the frontend, so the model answers about
exactly what the user sees. `actions` are UI commands from a fixed list that the frontend runs
(search a company, find markets, pick one, open the chart, change range or theme…). The
ElevenLabs key never leaves the server.
"""

import json
import logging
import os
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.llm.client import LlmError, complete_structured
from backend.observability import trace_run

log = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

ELEVEN_URL = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE_ID = "XrExE9yKIg1WjnnlVkGX"  # "Matilda": knowledgeable, professional
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"     # lowest latency
DEFAULT_STT_MODEL = "scribe_v2"
MAX_AUDIO_BYTES = 10 * 1024 * 1024          # ~10 min of opus; a voice command is a few seconds
MAX_SPEAK_CHARS = 1200
MAX_CONTEXT_CHARS = 12000
MAX_HISTORY = 8

ActionType = Literal[
    "search_company",   # value: ticker or company name
    "search_markets",   # value: free-text risk/event to find prediction markets for
    "select_market",    # value: the market's number in the visible list (1-based) or its title
    "generate_chart",   # open the price-vs-odds chart for the selected markets
    "back_to_graph",
    "set_range",        # value: 1d | 1w | 1m | 6m | 1y | all
    "set_theme",        # value: one of the theme names in context
    "set_mode",         # value: light | dark
    "go_home",          # back to the empty company prompt
    "open_alerts",
    "stop_listening",
]


class AssistantAction(BaseModel):
    type: ActionType
    value: str | None = None


class AssistantReply(BaseModel):
    reply: str = Field(description="What to say back, spoken aloud: 1-3 short sentences, no markdown, no lists")
    actions: list[AssistantAction] = Field(default_factory=list, description="UI commands to run, in order")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class AskIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    context: dict = Field(default_factory=dict)
    history: list[Turn] = Field(default_factory=list)


SYSTEM_PROMPT = """You are Poppy, the voice assistant built into What'sPoppin, a research app that maps
prediction markets (Kalshi, Polymarket), news and social posts to the public companies they affect.

You can SEE the app through CONTEXT (a JSON snapshot of the screen) and CONTROL it with ACTIONS.

How the app flows:
1. "empty": the user types a company -> search_company builds its relationship graph (suppliers,
   customers, partners, competitors, board interlocks from SEC filings).
2. "graph-ready" / "picking-markets": a right-hand panel lists prediction markets for the company
   (visible_markets, numbered). search_markets finds markets for a plain-English risk. select_market
   adds one by its number or title. generate_chart opens the chart once at least one is selected.
3. "market-view": the stock price vs. the market's odds over time (set_range), with news/X evidence
   pinned on the timeline, sentiment and a correlation readout. back_to_graph returns.

Actions (only these; value is a string or null):
- search_company(value=ticker or name)   - search_markets(value=query)   - select_market(value=number or title)
- generate_chart   - back_to_graph   - set_range(value=1d|1w|1m|6m|1y|all)   - go_home   - open_alerts
- set_theme(value=theme name from context.themes)   - set_mode(value=light|dark)   - stop_listening

Rules:
- Answer from CONTEXT when the question is about the screen. Name real numbers and names you see
  there; never invent companies, markets, prices or odds that are not in CONTEXT.
- General finance or "how does this app work" questions: answer briefly from general knowledge,
  and say when something is not on screen.
- When the user asks to do something, emit the action(s) AND say what you are doing. Chain actions
  when needed (e.g. "show me Tesla's EV tariff market" -> search_company TSLA; the markets load
  after the graph, so tell them to pick one next rather than selecting blindly).
- select_market only refers to markets in visible_markets. generate_chart needs a selected market.
- You give information, not financial advice: never recommend buying or selling, never predict a
  price, never promise returns. If asked, say you can't advise and offer what the data shows.
- Speak naturally and briefly: 1 to 3 short sentences, no markdown, no bullet lists, no URLs.
  Say tickers as companies when natural ("Nvidia" rather than "N V D A")."""


def _api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise HTTPException(503, "Voice is not configured: set ELEVENLABS_API_KEY in .env")
    return key


@router.post("/ask", response_model=AssistantReply)
async def ask(body: AskIn) -> AssistantReply:
    """One assistant turn: the user's words plus the screen -> what to say and what to do."""
    with trace_run("assistant.turn", inputs={"message": body.message}, stage=body.context.get("stage")) as run:
        with run.step("context.snapshot", kind="retrieval") as step:
            context = json.dumps(body.context, ensure_ascii=False, default=str)[:MAX_CONTEXT_CHARS]
            history = "\n".join(f"{t.role.upper()}: {t.text}" for t in body.history[-MAX_HISTORY:])
            step.output = {"context_chars": len(context), "history_turns": len(body.history[-MAX_HISTORY:])}
        user = f"CONTEXT:\n{context}\n\nCONVERSATION SO FAR:\n{history or '(none)'}\n\nUSER: {body.message}"
        try:
            with run.step("llm.complete_structured", kind="llm", model="nicrron/auto") as step:
                answer = await run_in_threadpool(complete_structured, SYSTEM_PROMPT, user, AssistantReply)
                step.output = answer.model_dump()
        except LlmError as exc:
            log.warning("assistant turn failed: %s", exc)
            raise HTTPException(502, f"Assistant unavailable: {exc}") from exc
        for action in answer.actions:  # the UI runs these; recorded so a run shows every tool call it asked for
            with run.step(f"action.{action.type}", kind="tool") as step:
                step.input = {"value": action.value}
        run.output = answer.model_dump()
        return answer


@router.post("/transcribe")
async def transcribe(request: Request) -> dict:
    """Speech to text. The body is the raw recording (MediaRecorder's webm/opus in Chrome)."""
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "Empty audio")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Recording too long")
    content_type = (request.headers.get("content-type") or "audio/webm").split(";")[0]
    extension = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4", "audio/wav": "wav",
                 "audio/mpeg": "mp3"}.get(content_type, "webm")
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.post(
                f"{ELEVEN_URL}/speech-to-text",
                headers={"xi-api-key": _api_key()},
                data={"model_id": os.environ.get("ELEVENLABS_STT_MODEL") or DEFAULT_STT_MODEL,
                      "tag_audio_events": "false"},
                files={"file": (f"speech.{extension}", audio, content_type)},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("transcription failed: %s", exc)
            raise HTTPException(502, "Transcription failed") from exc
    return {"text": (response.json().get("text") or "").strip()}


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEAK_CHARS)


@router.post("/speak")
async def speak(body: SpeakIn) -> Response:
    """Text to speech, as MP3."""
    voice = os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE_ID
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.post(
                f"{ELEVEN_URL}/text-to-speech/{voice}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": _api_key()},
                json={"text": body.text,
                      "model_id": os.environ.get("ELEVENLABS_TTS_MODEL") or DEFAULT_TTS_MODEL,
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("speech failed: %s", exc)
            raise HTTPException(502, "Speech failed") from exc
    return Response(content=response.content, media_type="audio/mpeg")
