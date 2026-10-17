import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  askAssistant,
  speak,
  transcribe,
  type AssistantAction,
  type AssistantTurn,
} from "../../lib/api/assistant";
import { THEMES } from "../theme/ThemeProvider";
import { useTheme } from "../theme/useTheme";
import { readScreen, sendCommand } from "./assistantBus";
import { useRecorder } from "./useRecorder";

export type AssistantStatus = "off" | "idle" | "listening" | "transcribing" | "thinking" | "speaking";

export interface AssistantMessage {
  id: number;
  role: "user" | "assistant" | "system";
  text: string;
}

/** What the active page lends the assistant: a snapshot of its state, and the actions it can run. */
export interface WorkspaceBridge {
  getContext: () => Record<string, unknown>;
  run: (action: AssistantAction) => Promise<string | void> | string | void;
}

interface AssistantContextValue {
  enabled: boolean;
  status: AssistantStatus;
  level: number;
  handsFree: boolean;
  narrate: boolean;
  panelOpen: boolean;
  messages: AssistantMessage[];
  error: string | null;
  setEnabled: (on: boolean) => void;
  setHandsFree: (on: boolean) => void;
  setNarrate: (on: boolean) => void;
  setPanelOpen: (open: boolean) => void;
  /** Start listening for one turn, or stop the current recording / speech. */
  toggleListening: () => void;
  /** A typed question, handled exactly like a spoken one. */
  ask: (text: string) => void;
  /** Speak a short line the app wants read out (narration), if narration is on and nothing else is going on. */
  announce: (text: string) => void;
  registerBridge: (bridge: WorkspaceBridge | null) => void;
}

export const AssistantContext = createContext<AssistantContextValue | null>(null);

const STORAGE = { enabled: "whatspoppin.assistant.on", handsFree: "whatspoppin.assistant.handsFree", narrate: "whatspoppin.assistant.narrate" };
const MAX_MESSAGES = 30;

function readFlag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function writeFlag(key: string, on: boolean) {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    // best-effort
  }
}

const GREETING =
  "Hi, I'm Poppy. Ask me about anything on screen, or tell me what to do, like show me Nvidia, or find markets about rate cuts.";

/**
 * The voice assistant, one per app (it lives in the layout, so it stays on across pages).
 * One spoken turn: record until the speaker pauses -> ElevenLabs transcription -> Nicrron answers
 * with a reply and UI actions, given a snapshot of the screen -> the actions run -> ElevenLabs reads
 * the reply. In hands-free mode it listens again as soon as it has finished speaking.
 */
export function AssistantProvider({ children }: { children: ReactNode }) {
  const { theme, mode, setTheme, setMode } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();

  const [enabled, setEnabledState] = useState(() => readFlag(STORAGE.enabled, false));
  const [handsFree, setHandsFreeState] = useState(() => readFlag(STORAGE.handsFree, true));
  const [narrate, setNarrateState] = useState(() => readFlag(STORAGE.narrate, true));
  const [panelOpen, setPanelOpen] = useState(false);
  const [status, setStatus] = useState<AssistantStatus>(enabled ? "idle" : "off");
  const [level, setLevel] = useState(0);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const recorder = useRecorder(setLevel);
  const bridgeRef = useRef<WorkspaceBridge | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speechAbort = useRef<AbortController | null>(null);
  const session = useRef(0); // bumped on every on/off: stale async steps check it and bail out
  const statusRef = useRef(status);
  statusRef.current = status;
  const handsFreeRef = useRef(handsFree);
  handsFreeRef.current = handsFree;
  const narrateRef = useRef(narrate);
  narrateRef.current = narrate;
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const historyRef = useRef<AssistantTurn[]>([]);
  const nextId = useRef(1);
  const listenRef = useRef<() => void>(() => {});
  /** A narration line that arrived mid-turn (e.g. the graph finished while Poppy was talking). */
  const pendingAnnouncement = useRef<string | null>(null);
  const announceRef = useRef<(text: string) => void>(() => {});
  /** After a turn: say a queued narration line if there is one, else (hands-free) listen again. */
  const afterTurn = useCallback(() => {
    const pending = pendingAnnouncement.current;
    if (pending) {
      pendingAnnouncement.current = null;
      announceRef.current(pending);
      return;
    }
    if (handsFreeRef.current && enabledRef.current) listenRef.current();
  }, []);

  const pushMessage = useCallback((role: AssistantMessage["role"], text: string) => {
    setMessages((prev) => [...prev.slice(-(MAX_MESSAGES - 1)), { id: nextId.current++, role, text }]);
  }, []);

  const stopSpeaking = useCallback(() => {
    speechAbort.current?.abort();
    speechAbort.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = "";
    }
  }, []);

  /** Read `text` aloud; resolves when playback ends (or is interrupted). */
  const speakText = useCallback(
    async (text: string, mySession: number) => {
      stopSpeaking();
      const controller = new AbortController();
      speechAbort.current = controller;
      setStatus("speaking");
      try {
        const blob = await speak(text, controller.signal);
        if (session.current !== mySession || controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        const audio = audioRef.current ?? new Audio();
        audioRef.current = audio;
        audio.src = url;
        await new Promise<void>((resolve) => {
          const done = () => {
            URL.revokeObjectURL(url);
            resolve();
          };
          audio.onended = done;
          audio.onerror = done;
          controller.signal.addEventListener("abort", done, { once: true });
          audio.play().catch(done);
        });
      } catch {
        if (!controller.signal.aborted) setError("Couldn't play the voice reply.");
      } finally {
        if (speechAbort.current === controller) speechAbort.current = null;
      }
    },
    [stopSpeaking],
  );

  const buildContext = useCallback(
    () => ({
      page: location.pathname,
      theme: THEMES.find((t) => t.id === theme)?.name ?? theme,
      mode,
      themes: THEMES.map((t) => t.name),
      ...(bridgeRef.current?.getContext() ?? { stage: "no workspace on this page" }),
      screen: readScreen(),
    }),
    [location.pathname, theme, mode],
  );

  const runAction = useCallback(
    async (action: AssistantAction): Promise<string | void> => {
      const value = action.value?.trim() || null;
      switch (action.type) {
        case "set_theme": {
          const wanted = (value ?? "").toLowerCase();
          const match = THEMES.find((t) => t.name.toLowerCase().includes(wanted) || t.id.includes(wanted));
          if (match) setTheme(match.id);
          return match ? undefined : `No theme called ${value}.`;
        }
        case "set_mode":
          if (value === "light" || value === "dark") setMode(value);
          return;
        case "go_home":
          navigate("/");
          return;
        case "stop_listening":
          setHandsFreeState(false);
          writeFlag(STORAGE.handsFree, false);
          return;
        case "open_alerts":
        case "search_markets":
        case "select_market": {
          // A chained command ("show me Nvidia and find chip markets") can arrive before the panel
          // that owns it has mounted, so give it up to ~2 s to appear.
          for (let attempt = 0; attempt < 14; attempt += 1) {
            const { handled, note } = await sendCommand(action.type, value);
            if (handled) return note;
            await new Promise((r) => setTimeout(r, 150));
          }
          return "That panel isn't on screen right now.";
        }
        default:
          if (bridgeRef.current) return bridgeRef.current.run({ ...action, value });
          return "Open the workspace first.";
      }
    },
    [navigate, setMode, setTheme],
  );

  const handleUtterance = useCallback(
    async (text: string, mySession: number) => {
      pushMessage("user", text);
      setStatus("thinking");
      setError(null);
      try {
        const answer = await askAssistant(text, buildContext(), historyRef.current);
        if (session.current !== mySession) return;
        historyRef.current = [...historyRef.current, { role: "user" as const, text }, { role: "assistant" as const, text: answer.reply }].slice(-8);
        pushMessage("assistant", answer.reply);
        const notes: string[] = [];
        for (const action of answer.actions) {
          const note = await runAction(action);
          if (note) notes.push(note);
          await new Promise((r) => setTimeout(r, 120)); // let React apply one action before the next reads state
        }
        notes.forEach((n) => pushMessage("system", n));
        if (session.current !== mySession) return;
        await speakText(notes.length ? `${answer.reply} ${notes.join(" ")}` : answer.reply, mySession);
      } catch {
        if (session.current !== mySession) return;
        setError("I couldn't reach the assistant. Is the backend running?");
      }
      if (session.current !== mySession) return;
      setStatus("idle");
      statusRef.current = "idle";
      afterTurn();
    },
    [afterTurn, buildContext, pushMessage, runAction, speakText],
  );

  const listen = useCallback(async () => {
    const mySession = session.current;
    stopSpeaking();
    setError(null);
    setStatus("listening");
    let take;
    try {
      take = await recorder.record();
    } catch {
      setError("Microphone access was blocked. Allow it in the browser to talk to Poppy.");
      setStatus("idle");
      return;
    }
    if (session.current !== mySession) return;
    if (!take.audio) {
      setStatus("idle");
      if (handsFreeRef.current && enabledRef.current) listenRef.current(); // keep listening
      return;
    }
    setStatus("transcribing");
    try {
      const text = (await transcribe(take.audio)).trim();
      if (session.current !== mySession) return;
      if (!text) {
        setStatus("idle");
        if (handsFreeRef.current && enabledRef.current) listenRef.current();
        return;
      }
      await handleUtterance(text, mySession);
    } catch {
      if (session.current !== mySession) return;
      setError("Couldn't transcribe that. Try again.");
      setStatus("idle");
    }
  }, [handleUtterance, recorder, stopSpeaking]);
  listenRef.current = () => void listen();

  const setEnabled = useCallback(
    (on: boolean) => {
      session.current += 1;
      stopSpeaking();
      recorder.stop();
      setEnabledState(on);
      writeFlag(STORAGE.enabled, on);
      setLevel(0);
      if (!on) {
        recorder.release();
        setStatus("off");
        setPanelOpen(false);
        return;
      }
      setStatus("idle");
      setPanelOpen(true);
      if (messages.length === 0) pushMessage("assistant", GREETING);
      const mySession = session.current;
      // Greet, then (hands-free) start listening. Turning it on is a click, so playback is allowed.
      void speakText(GREETING, mySession).then(() => {
        if (session.current !== mySession) return;
        setStatus("idle");
        if (handsFreeRef.current) listenRef.current();
      });
    },
    [messages.length, pushMessage, recorder, speakText, stopSpeaking],
  );

  const toggleListening = useCallback(() => {
    if (!enabledRef.current) return;
    const s = statusRef.current;
    if (s === "listening") recorder.stop();
    else if (s === "speaking") {
      stopSpeaking();
      setStatus("idle");
    } else if (s === "idle") void listen();
  }, [listen, recorder, stopSpeaking]);

  const ask = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || !enabledRef.current) return;
      recorder.stop();
      stopSpeaking();
      session.current += 1;
      void handleUtterance(trimmed, session.current);
    },
    [handleUtterance, recorder, stopSpeaking],
  );

  const announce = useCallback(
    (text: string) => {
      if (!enabledRef.current || !narrateRef.current) return;
      if (statusRef.current !== "idle" && statusRef.current !== "listening") {
        pendingAnnouncement.current = text; // said as soon as the current turn finishes
        return;
      }
      pendingAnnouncement.current = null;
      recorder.stop();
      session.current += 1;
      const mySession = session.current;
      pushMessage("assistant", text);
      void speakText(text, mySession).then(() => {
        if (session.current !== mySession) return;
        setStatus("idle");
        statusRef.current = "idle";
        afterTurn();
      });
    },
    [afterTurn, pushMessage, recorder, speakText],
  );
  announceRef.current = announce;

  const setHandsFree = useCallback(
    (on: boolean) => {
      setHandsFreeState(on);
      writeFlag(STORAGE.handsFree, on);
      if (on && enabledRef.current && statusRef.current === "idle") listenRef.current();
      if (!on && statusRef.current === "listening") recorder.stop();
    },
    [recorder],
  );

  const setNarrate = useCallback((on: boolean) => {
    setNarrateState(on);
    writeFlag(STORAGE.narrate, on);
  }, []);

  const registerBridge = useCallback((bridge: WorkspaceBridge | null) => {
    bridgeRef.current = bridge;
  }, []);

  // Alt+V: talk / stop, from anywhere.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.altKey && e.code === "KeyV") {
        e.preventDefault();
        if (!enabledRef.current) setEnabled(true);
        else toggleListening();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setEnabled, toggleListening]);

  // Turned on in an earlier visit: come back on quietly (no greeting, no mic until asked; the
  // browser blocks audio and mic prompts before the first click anyway).
  useEffect(() => {
    if (enabled) setStatus("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo<AssistantContextValue>(
    () => ({
      enabled,
      status,
      level,
      handsFree,
      narrate,
      panelOpen,
      messages,
      error,
      setEnabled,
      setHandsFree,
      setNarrate,
      setPanelOpen,
      toggleListening,
      ask,
      announce,
      registerBridge,
    }),
    [enabled, status, level, handsFree, narrate, panelOpen, messages, error, setEnabled, setHandsFree, setNarrate, toggleListening, ask, announce, registerBridge],
  );

  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}
