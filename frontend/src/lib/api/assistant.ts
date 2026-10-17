import { ApiError, BASE_URL, post } from "../apiClient";

/** Mirrors backend/api/assistant.py. */
export type AssistantActionType =
  | "search_company"
  | "search_markets"
  | "select_market"
  | "generate_chart"
  | "back_to_graph"
  | "set_range"
  | "set_theme"
  | "set_mode"
  | "go_home"
  | "open_alerts"
  | "stop_listening";

export interface AssistantAction {
  type: AssistantActionType;
  value?: string | null;
}

export interface AssistantReply {
  reply: string;
  actions: AssistantAction[];
}

export interface AssistantTurn {
  role: "user" | "assistant";
  text: string;
}

/** POST /assistant/ask: the user's words plus a snapshot of the screen. */
export function askAssistant(message: string, context: unknown, history: AssistantTurn[]): Promise<AssistantReply> {
  return post<AssistantReply>("/assistant/ask", { message, context, history });
}

/** POST /assistant/transcribe: the raw recording in, the words out (ElevenLabs Scribe). */
export async function transcribe(audio: Blob): Promise<string> {
  const response = await fetch(`${BASE_URL}/assistant/transcribe`, {
    method: "POST",
    headers: { "Content-Type": audio.type || "audio/webm" },
    body: audio,
  });
  if (!response.ok) throw new ApiError(response.status, `Transcription failed: ${response.statusText}`);
  return ((await response.json()) as { text: string }).text;
}

/** POST /assistant/speak: MP3 of `text` in the assistant's ElevenLabs voice. */
export async function speak(text: string, signal?: AbortSignal): Promise<Blob> {
  const response = await fetch(`${BASE_URL}/assistant/speak`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text.slice(0, 1200) }),
    signal,
  });
  if (!response.ok) throw new ApiError(response.status, `Speech failed: ${response.statusText}`);
  return response.blob();
}
