/**
 * How the voice assistant sees and drives components it doesn't own.
 *
 * - Screen facts: a component publishes what it shows under a key ("markets", "chart", "alerts"),
 *   and the assistant reads them all into the context it sends with each question.
 * - Commands: a component registers a handler for an action it owns (the markets panel owns
 *   "search_markets" and "select_market", the alerts bell owns "open_alerts"); the assistant
 *   sends the command and learns whether anyone on screen handled it.
 *
 * Module-level on purpose: the assistant lives in the app layout and outlives every page, while
 * these components mount and unmount with the workspace stages.
 */

const screen = new Map<string, unknown>();

/** Publish (or with `undefined`, withdraw) one piece of what's on screen. */
export function publishScreen(key: string, value: unknown): void {
  if (value === undefined) screen.delete(key);
  else screen.set(key, value);
}

export function readScreen(): Record<string, unknown> {
  return Object.fromEntries(screen);
}

type Handler = (value: string | null) => string | void | Promise<string | void>;
const handlers = new Map<string, Set<Handler>>();

/** Handle one command type; returns the unsubscribe. A handler may return a note for the assistant. */
export function onCommand(type: string, handler: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type)!.add(handler);
  return () => {
    handlers.get(type)?.delete(handler);
  };
}

/** Run a command. `handled` is false when nothing on screen owns it right now. */
export async function sendCommand(type: string, value: string | null): Promise<{ handled: boolean; note?: string }> {
  const set = handlers.get(type);
  if (!set || set.size === 0) return { handled: false };
  let note: string | undefined;
  for (const handler of set) {
    const result = await handler(value);
    if (typeof result === "string") note = result;
  }
  return { handled: true, note };
}
