import { Newspaper, Plus, TwitterLogo } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { fetchCandidateNews, fetchCandidateTweets } from "../../../lib/api/evidence";
import type { ContentItem } from "../../../types/content";
import type { EvidenceAnnotation } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";
import { EvidencePickerList } from "./EvidencePickerList";

type PickerKind = "tweets" | "news";

/**
 * Stage 7 — the two explicit "Add" actions. Each button opens a small popover
 * with a pick-to-add list sourced from the evidence API (mock fallback), keyed off the
 * current workspace ticker. Nothing is auto-populated into state.evidence —
 * only an explicit click on a candidate (inside EvidencePickerList) adds it.
 */
export function AddEvidenceButtons() {
  const { state, dispatch } = useWorkspace();
  const [openPicker, setOpenPicker] = useState<PickerKind | null>(null);
  const [itemsByKind, setItemsByKind] = useState<Record<PickerKind, ContentItem[]>>({
    tweets: [],
    news: [],
  });
  const [loadingKind, setLoadingKind] = useState<PickerKind | null>(null);
  const [errorKind, setErrorKind] = useState<PickerKind | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!openPicker) return;
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpenPicker(null);
      }
    }
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenPicker(null);
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [openPicker]);

  async function openAndLoad(kind: PickerKind) {
    if (openPicker === kind) {
      setOpenPicker(null);
      return;
    }
    setOpenPicker(kind);
    if (itemsByKind[kind].length > 0) return; // already loaded this session
    setLoadingKind(kind);
    setErrorKind(null);
    const ticker = state.ticker ?? "";
    try {
      const items = kind === "tweets" ? await fetchCandidateTweets(ticker) : await fetchCandidateNews(ticker);
      setItemsByKind((prev) => ({ ...prev, [kind]: items }));
    } catch {
      // the fetchers already fall back to mocks; this is a last-resort guard
      setErrorKind(kind);
    } finally {
      setLoadingKind(null);
    }
  }

  function handleSelect(item: ContentItem) {
    const annotation: EvidenceAnnotation = {
      id: `evidence-${item.id}-${Date.now()}`,
      item,
      timestamp: item.published_at,
    };
    dispatch({ type: "EVIDENCE_ADDED", annotation });
  }

  const addedIds = new Set(state.evidence.map((e) => e.item.id));

  return (
    <div ref={containerRef} className="relative flex items-center gap-2">
      <AddButton
        label="Add tweets"
        icon={<TwitterLogo size={15} weight="bold" />}
        active={openPicker === "tweets"}
        onClick={() => openAndLoad("tweets")}
      />
      <AddButton
        label="Add news"
        icon={<Newspaper size={15} weight="bold" />}
        active={openPicker === "news"}
        onClick={() => openAndLoad("news")}
      />

      <AnimatePresence>
        {openPicker && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.14 }}
            className="absolute left-0 top-full z-20 mt-2 w-80 overflow-hidden rounded-[var(--radius-panel)] border shadow-lg"
            style={{ background: "var(--surface-elevated)", borderColor: "var(--border)" }}
          >
            <div className="border-b px-3 py-2" style={{ borderColor: "var(--border)" }}>
              <p className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
                {openPicker === "tweets" ? "Candidate tweets" : "Candidate news"}
              </p>
            </div>
            <EvidencePickerList
              items={itemsByKind[openPicker]}
              loading={loadingKind === openPicker}
              error={errorKind === openPicker}
              kind={openPicker}
              addedIds={addedIds}
              onSelect={handleSelect}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function AddButton({
  label,
  icon,
  active,
  onClick,
}: {
  label: string;
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={active}
      className="flex items-center gap-1.5 rounded-[var(--radius-control)] border px-3 py-1.5 text-sm font-medium transition"
      style={{
        borderColor: active ? "var(--accent)" : "var(--border)",
        background: active ? "var(--surface-elevated)" : "var(--surface)",
        color: "var(--text-primary)",
      }}
    >
      <Plus size={14} weight="bold" style={{ color: "var(--text-tertiary)" }} />
      {icon}
      {label}
    </button>
  );
}
