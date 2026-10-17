import { X } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useWorkspace } from "../useWorkspace";
import { NewsEvidenceCard } from "./NewsEvidenceCard";
import { TweetEvidenceCard } from "./TweetEvidenceCard";

/**
 * Stage 8's evidence drawer. Deliberately a right-side slide-in panel, not a
 * modal dialog — a centered modal would cover the chart the user is trying to
 * correlate the evidence against (per the design spec). Built as a custom
 * focus-trapped panel rather than a Radix Dialog: the app doesn't have
 * @radix-ui/themes' <Theme> provider/CSS wired up anywhere yet, and pulling
 * that in just for this panel would fight the existing CSS-variable token
 * system instead of using it. Dismiss is wired for Escape and click-outside,
 * matching what Radix's Dialog would have given for free.
 */
export function EvidenceDrawer() {
  const { state, dispatch } = useWorkspace();
  const panelRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  const annotation = state.evidence.find((e) => e.id === state.pinnedAnnotationId) ?? null;
  const isOpen = annotation !== null;

  function close() {
    dispatch({ type: "EVIDENCE_PINNED", annotationId: null });
  }

  useEffect(() => {
    if (!isOpen) return;

    previouslyFocused.current = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    function handleKeydown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        close();
        return;
      }
      if (e.key === "Tab" && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(
          'button, a[href], input, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    document.addEventListener("keydown", handleKeydown);
    return () => {
      document.removeEventListener("keydown", handleKeydown);
      previouslyFocused.current?.focus();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  return createPortal(
    <AnimatePresence>
      {isOpen && annotation && (
        <>
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="fixed inset-0 z-40"
            style={{ background: "rgba(0,0,0,0.25)" }}
            onClick={close}
            aria-hidden="true"
          />
          <motion.div
            key="panel"
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={annotation.item.source === "twitter" ? "Tweet evidence" : "News evidence"}
            tabIndex={-1}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
            className="fixed right-0 top-0 z-50 h-full w-full max-w-md overflow-y-auto border-l outline-none"
            style={{ background: "var(--surface-elevated)", borderColor: "var(--border)" }}
          >
            <div
              className="flex items-center justify-between border-b px-4 py-3"
              style={{ borderColor: "var(--border)" }}
            >
              <span className="text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-tertiary)" }}>
                {annotation.item.source === "twitter" ? "Tweet" : "News"} evidence
              </span>
              <button
                type="button"
                onClick={close}
                aria-label="Close evidence panel"
                className="rounded-full p-1.5 transition hover:opacity-70"
                style={{ color: "var(--text-secondary)" }}
              >
                <X size={16} weight="bold" />
              </button>
            </div>

            <div className="p-4">
              {annotation.item.source === "twitter" ? (
                <TweetEvidenceCard item={annotation.item} />
              ) : (
                <NewsEvidenceCard item={annotation.item} />
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  );
}
