import { CaretDown, Ear, EarSlash, Microphone, PaperPlaneRight, Power, SpeakerHigh, SpeakerSlash, Waveform } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useAssistant } from "./useAssistant";

const STATUS_TEXT: Record<string, string> = {
  idle: "Tap the mic or press Alt+V",
  listening: "Listening…",
  transcribing: "Got it…",
  thinking: "Thinking…",
  speaking: "Speaking (tap to stop)",
};

const SUGGESTIONS = ["What's on screen?", "Show me Tesla", "Find markets about rate cuts", "Open the first market's chart"];

/**
 * The assistant's floating dock (bottom-left, clear of the projects rail). Always present while
 * the assistant is on: expanded it shows the conversation, the mic and a text box; minimized it is
 * one orb that still shows listening/speaking and still takes a tap to talk.
 */
export function AssistantDock() {
  const a = useAssistant();
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [a.messages, a.panelOpen]);

  if (!a.enabled) return null;

  const busy = a.status !== "idle";
  const orbScale = a.status === "listening" ? 1 + a.level * 0.35 : 1;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    a.ask(draft);
    setDraft("");
  }

  const orb = (size: number) => (
    <button
      type="button"
      onClick={a.toggleListening}
      disabled={a.status === "thinking" || a.status === "transcribing"}
      aria-label={a.status === "listening" ? "Stop listening" : a.status === "speaking" ? "Stop speaking" : "Talk to Poppy"}
      className="relative flex shrink-0 items-center justify-center rounded-full transition-transform disabled:opacity-70"
      style={{
        width: size,
        height: size,
        background: "var(--accent)",
        color: "var(--accent-contrast)",
        transform: `scale(${orbScale})`,
        transition: "transform 80ms linear",
        boxShadow: busy ? "0 0 0 6px color-mix(in srgb, var(--accent) 22%, transparent)" : "0 2px 10px rgba(0,0,0,.12)",
        animation: a.status === "thinking" || a.status === "speaking" ? "assistant-pulse 1.4s ease-in-out infinite" : undefined,
      }}
    >
      {a.status === "speaking" ? <Waveform size={size * 0.45} weight="bold" /> : <Microphone size={size * 0.45} weight="bold" />}
    </button>
  );

  return (
    <div className="pointer-events-none fixed bottom-5 left-20 z-50">
      <AnimatePresence mode="wait" initial={false}>
        {a.panelOpen ? (
          <motion.section
            key="dock"
            role="dialog"
            aria-label="Poppy, the voice assistant"
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 12, scale: 0.98 }}
            transition={{ duration: 0.18 }}
            className="pointer-events-auto flex w-[360px] flex-col overflow-hidden shadow-xl"
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-panel)",
            }}
          >
            <header className="flex items-center gap-2 border-b px-3 py-2" style={{ borderColor: "var(--border)" }}>
              <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Poppy
              </span>
              <span className="truncate text-[11px]" style={{ color: "var(--text-tertiary)" }} aria-live="polite">
                {STATUS_TEXT[a.status] ?? ""}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <IconToggle
                  on={a.handsFree}
                  onClick={() => a.setHandsFree(!a.handsFree)}
                  label={a.handsFree ? "Hands-free on: listens after every reply" : "Hands-free off: tap to talk"}
                  icon={a.handsFree ? <Ear size={14} weight="bold" /> : <EarSlash size={14} weight="bold" />}
                />
                <IconToggle
                  on={a.narrate}
                  onClick={() => a.setNarrate(!a.narrate)}
                  label={a.narrate ? "Narration on: reads out what loads" : "Narration off"}
                  icon={a.narrate ? <SpeakerHigh size={14} weight="bold" /> : <SpeakerSlash size={14} weight="bold" />}
                />
                <IconToggle on={false} onClick={() => a.setPanelOpen(false)} label="Minimize" icon={<CaretDown size={14} weight="bold" />} />
                <IconToggle on={false} onClick={() => a.setEnabled(false)} label="Turn off the voice assistant" icon={<Power size={14} weight="bold" />} />
              </div>
            </header>

            <div ref={listRef} className="flex max-h-72 min-h-[7rem] flex-col gap-2 overflow-y-auto px-3 py-3" aria-live="polite">
              {a.messages.map((m) => (
                <p
                  key={m.id}
                  className={`max-w-[85%] rounded-2xl px-3 py-2 text-xs leading-relaxed ${m.role === "user" ? "self-end" : "self-start"}`}
                  style={
                    m.role === "user"
                      ? { background: "var(--accent)", color: "var(--accent-contrast)" }
                      : m.role === "system"
                        ? { color: "var(--text-tertiary)", fontStyle: "italic", padding: "0 0.25rem" }
                        : { background: "var(--surface-elevated)", color: "var(--text-primary)" }
                  }
                >
                  {m.text}
                </p>
              ))}
              {a.messages.length <= 1 && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => a.ask(s)}
                      className="rounded-full px-2.5 py-1 text-[11px] transition hover:opacity-80"
                      style={{ border: "1px solid var(--border)", color: "var(--text-secondary)" }}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              )}
              {a.error && (
                <p className="text-[11px]" role="alert" style={{ color: "var(--status-negative)" }}>
                  {a.error}
                </p>
              )}
            </div>

            <form onSubmit={submit} className="flex items-center gap-2 border-t px-3 py-2.5" style={{ borderColor: "var(--border)" }}>
              {orb(40)}
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Ask anything, or tell me what to do…"
                aria-label="Ask Poppy"
                className="min-w-0 flex-1 bg-transparent text-xs outline-none"
                style={{ color: "var(--text-primary)" }}
              />
              <button
                type="submit"
                disabled={!draft.trim()}
                aria-label="Send"
                className="flex h-7 w-7 items-center justify-center rounded-full disabled:opacity-30"
                style={{ color: "var(--accent)" }}
              >
                <PaperPlaneRight size={16} weight="bold" />
              </button>
            </form>
          </motion.section>
        ) : (
          <motion.div
            key="orb"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            className="pointer-events-auto flex items-center gap-2"
          >
            {orb(48)}
            <button
              type="button"
              onClick={() => a.setPanelOpen(true)}
              className="rounded-full px-3 py-1.5 text-[11px] font-medium shadow-sm"
              style={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}
            >
              {a.status === "idle" ? "Poppy" : STATUS_TEXT[a.status]}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function IconToggle({ on, onClick, label, icon }: { on: boolean; onClick: () => void; label: string; icon: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={on}
      title={label}
      className="flex h-7 w-7 items-center justify-center rounded-full transition hover:opacity-80"
      style={{
        color: on ? "var(--accent)" : "var(--text-tertiary)",
        background: on ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "transparent",
      }}
    >
      {icon}
    </button>
  );
}
