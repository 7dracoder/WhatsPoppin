import type { AlertDirection, ImpactTier } from "../../../types/alert";

export const TIER_LABELS: Record<ImpactTier, string> = {
  1: "about the company",
  2: "linked company",
  3: "related topic",
};

/** Direction is a fact about the market's Yes price, never a call on the stock (spec: direction stays with F8). */
export const DIRECTION_LABELS: Record<AlertDirection, string> = {
  yes_up: "Yes up",
  yes_down: "Yes down",
};

export function directionColor(direction: AlertDirection): string {
  return direction === "yes_up"
    ? "var(--status-positive)"
    : "var(--status-negative)";
}

/** "just now", "4m ago", "3h ago", then a date once it's older than a day. */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const seconds = Math.max(0, Math.round((now - t) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(t).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** The detector's summary opens with "[Venue] title (Yes): ..."; the card already shows venue and title, so keep the rest. */
export function summaryDetail(summary: string): string {
  // Strip exactly the detector's prefix, not the first colon: titles can contain colons too.
  const stripped = summary.replace(/^\[[^\]]+\] .*? \((?:Yes|No)\): /, "");
  return stripped !== summary ? stripped : summary;
}
