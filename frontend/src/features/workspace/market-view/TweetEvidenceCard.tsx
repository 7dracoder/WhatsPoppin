import { ArrowSquareOut, ChatCircle, Heart, Repeat } from "@phosphor-icons/react";
import type { ContentItem } from "../../../types/content";

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Stage 8's tweet template. Per the design spec, this should read as an actual
 * tweet (avatar, @handle, text, timestamp) rather than a generic social card —
 * no live oEmbed in the time available, so this is a static facsimile that
 * leans into the recognizable tweet UI shape (avatar circle, handle line,
 * body text, muted static action row).
 */
export function TweetEvidenceCard({ item }: { item: ContentItem }) {
  const handle = item.author ?? "@unknown";
  const displayName = handle.replace(/^@/, "");

  return (
    <div
      className="rounded-[var(--radius-panel)] border p-4"
      style={{ borderColor: "var(--border)", background: "var(--surface)" }}
    >
      <div className="flex items-start gap-3">
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-bold"
          style={{ background: "var(--accent)", color: "var(--accent-contrast)" }}
        >
          {displayName.slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {displayName}
            </span>
            <span className="truncate text-sm" style={{ color: "var(--text-tertiary)" }}>
              {handle.startsWith("@") ? handle : `@${handle}`}
            </span>
            <span style={{ color: "var(--text-tertiary)" }}>·</span>
            <span className="shrink-0 text-sm" style={{ color: "var(--text-tertiary)" }}>
              {formatTimestamp(item.published_at)}
            </span>
          </div>

          <p className="mt-1 text-sm leading-relaxed" style={{ color: "var(--text-primary)" }}>
            {item.text ?? item.title}
          </p>

          <div className="mt-3 flex items-center gap-6" style={{ color: "var(--text-tertiary)" }}>
            <span className="flex items-center gap-1.5 text-xs">
              <ChatCircle size={15} />
            </span>
            <span className="flex items-center gap-1.5 text-xs">
              <Repeat size={15} />
            </span>
            <span className="flex items-center gap-1.5 text-xs">
              <Heart size={15} />
            </span>
          </div>
        </div>
      </div>

      <a
        href={item.url}
        target="_blank"
        rel="noreferrer"
        className="mt-3 flex items-center gap-1.5 text-sm font-medium"
        style={{ color: "var(--accent)" }}
      >
        View on X
        <ArrowSquareOut size={14} weight="bold" />
      </a>
    </div>
  );
}
