import { ArrowSquareOut } from "@phosphor-icons/react";
import type { ContentItem } from "../../../types/content";

/**
 * Small domain -> {label, color} lookup for a placeholder source badge.
 * Per the design spec this is explicitly a build task, not a design question —
 * real logo assets aren't worth blocking on, so each known source gets a
 * colored initial badge instead. Falls back to deriving an initial from the
 * item's URL host for anything not in the table.
 */
const SOURCE_BADGES: Record<string, { label: string; color: string }> = {
  "example-news.com": { label: "EN", color: "#3b5bdb" },
  "reuters.com": { label: "R", color: "#ff8000" },
  "bloomberg.com": { label: "B", color: "#000000" },
  "wsj.com": { label: "WSJ", color: "#000000" },
  "cnbc.com": { label: "CN", color: "#005594" },
  "ft.com": { label: "FT", color: "#fff1e5" },
};

function badgeFor(url: string): { label: string; color: string } {
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    if (SOURCE_BADGES[host]) return SOURCE_BADGES[host];
    return { label: host.slice(0, 2).toUpperCase(), color: "var(--accent)" };
  } catch {
    return { label: "?", color: "var(--text-tertiary)" };
  }
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function NewsEvidenceCard({ item }: { item: ContentItem }) {
  const badge = badgeFor(item.url);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2.5">
        <span
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold"
          style={{ background: badge.color, color: "#ffffff" }}
        >
          {badge.label}
        </span>
        <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          {formatTimestamp(item.published_at)}
        </span>
      </div>

      <h3 className="text-base font-semibold leading-snug" style={{ color: "var(--text-primary)" }}>
        {item.title}
      </h3>

      {item.text && (
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
          {item.text}
        </p>
      )}

      {item.entities.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {item.entities.map((entity) => (
            <span
              key={entity}
              className="rounded-[var(--radius-pill)] px-2 py-0.5 text-xs"
              style={{ background: "var(--surface)", color: "var(--text-tertiary)", border: "1px solid var(--border)" }}
            >
              {entity}
            </span>
          ))}
        </div>
      )}

      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-1.5 text-sm font-medium"
        style={{ color: "var(--accent)" }}
      >
        Read full article
        <ArrowSquareOut size={14} weight="bold" />
      </a>
    </div>
  );
}
