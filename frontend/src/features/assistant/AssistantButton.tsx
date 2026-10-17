import { SpeakerHigh, SpeakerSlash } from "@phosphor-icons/react";
import { useAssistant } from "./useAssistant";

const STATUS_LABEL: Record<string, string> = {
  off: "Voice assistant off",
  idle: "Voice assistant on",
  listening: "Listening",
  transcribing: "Heard you",
  thinking: "Thinking",
  speaking: "Speaking",
};

/**
 * The header control next to the theme dots: turns the voice assistant on (and opens its dock),
 * or, once on, shows and hides the dock. The ring shows what it's doing from anywhere on the page.
 */
export function AssistantButton() {
  const { enabled, status, panelOpen, setEnabled, setPanelOpen, level } = useAssistant();
  const busy = status === "listening" || status === "speaking" || status === "thinking" || status === "transcribing";
  const label = enabled ? `${STATUS_LABEL[status]}. ${panelOpen ? "Hide" : "Show"} Poppy` : "Turn on the voice assistant (Alt+V)";

  return (
    <button
      type="button"
      onClick={() => (enabled ? setPanelOpen(!panelOpen) : setEnabled(true))}
      aria-label={label}
      aria-pressed={enabled}
      title={label}
      className="relative flex h-8 w-8 items-center justify-center rounded-full transition hover:opacity-80"
      style={{
        background: enabled ? "var(--accent)" : "var(--surface)",
        border: `1px solid ${enabled ? "var(--accent)" : "var(--border)"}`,
        color: enabled ? "var(--accent-contrast)" : "var(--text-secondary)",
      }}
    >
      {enabled ? <SpeakerHigh size={16} weight="bold" /> : <SpeakerSlash size={16} weight="bold" />}
      {enabled && busy && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 rounded-full"
          style={{
            boxShadow: `0 0 0 ${2 + Math.round(level * 6)}px color-mix(in srgb, var(--accent) 35%, transparent)`,
            animation: status === "listening" ? undefined : "assistant-pulse 1.4s ease-in-out infinite",
            transition: "box-shadow 80ms linear",
          }}
        />
      )}
    </button>
  );
}
