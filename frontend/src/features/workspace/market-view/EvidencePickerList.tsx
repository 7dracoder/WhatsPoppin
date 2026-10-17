import { CheckCircle, Circle, Newspaper, TwitterLogo } from "@phosphor-icons/react";
import type { ContentItem } from "../../../types/content";

/**
 * Stage 7's "pickable list" of evidence candidates (tweets or news, depending on
 * which "Add" button opened it). This is deliberately a click-to-add list, not a
 * checkbox+submit form — the design spec calls out that curation should be an
 * explicit per-item action, and each click immediately dispatches EVIDENCE_ADDED
 * upstream (see AddEvidenceButtons), so there's nothing to "submit."
 */
export function EvidencePickerList({
  items,
  loading,
  error = false,
  kind,
  addedIds,
  onSelect,
}: {
  items: ContentItem[];
  loading: boolean;
  error?: boolean;
  kind: "tweets" | "news";
  /** ids already added to state.evidence, so already-added candidates render as checked/disabled. */
  addedIds: Set<string>;
  onSelect: (item: ContentItem) => void;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2 p-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-14 animate-pulse rounded-[var(--radius-control)]"
            style={{ background: "var(--border)" }}
          />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <p className="p-4 text-sm" style={{ color: "var(--text-tertiary)" }}>
        Couldn't load {kind}. Close and reopen to try again.
      </p>
    );
  }

  if (items.length === 0) {
    return (
      <p className="p-4 text-sm" style={{ color: "var(--text-tertiary)" }}>
        {kind === "news" ? "No news found for this ticker older than 30 days." : `No ${kind} found for this ticker in the last 30 days.`}
      </p>
    );
  }

  return (
    <ul className="flex max-h-80 flex-col gap-1 overflow-y-auto p-2">
      {items.map((item) => {
        const added = addedIds.has(item.id);
        return (
          <li key={item.id}>
            <button
              type="button"
              disabled={added}
              onClick={() => onSelect(item)}
              className="flex w-full items-start gap-2.5 rounded-[var(--radius-control)] p-2.5 text-left transition disabled:cursor-default"
              style={{
                background: "transparent",
                opacity: added ? 0.55 : 1,
              }}
              onMouseEnter={(e) => {
                if (!added) e.currentTarget.style.background = "var(--surface-elevated)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "transparent";
              }}
            >
              <span className="mt-0.5 shrink-0" style={{ color: "var(--text-tertiary)" }}>
                {item.source === "twitter" ? <TwitterLogo size={16} /> : <Newspaper size={16} />}
              </span>
              <span className="flex-1 min-w-0">
                <span className="block truncate text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                  {item.title}
                </span>
                {item.text && (
                  <span className="mt-0.5 block truncate text-xs" style={{ color: "var(--text-secondary)" }}>
                    {item.text}
                  </span>
                )}
              </span>
              <span className="mt-0.5 shrink-0" style={{ color: added ? "var(--accent)" : "var(--text-tertiary)" }}>
                {added ? <CheckCircle size={18} weight="fill" /> : <Circle size={18} />}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
