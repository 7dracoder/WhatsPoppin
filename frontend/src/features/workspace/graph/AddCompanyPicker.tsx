import { Plus, X } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import { RELATIONSHIP_COLORS, RELATIONSHIP_LABELS } from "./GraphLegend";
import type { Candidate } from "./relatedCompanies";

/**
 * "Add company": a button at the canvas's bottom-right that opens a list of
 * companies related to the ones on the graph but not on it yet. Each row is
 * the ticker, the company's name and its relationship to the on-graph
 * company it was found through; clicking a row adds that company and that
 * one relationship. It replaced the hop slider, which added every linked
 * company's every link at once and made the graph unreadable.
 *
 * The related companies come from each on-graph company's own graph, which
 * the backend builds from filings; `onOpen` starts those fetches the first
 * time the list is opened, so a graph nobody expands costs nothing.
 */
export function AddCompanyPicker({
  candidates,
  reading,
  onOpen,
  onAdd,
}: {
  candidates: Candidate[];
  /** Companies whose graphs are still being read, so more candidates may come. */
  reading: number;
  onOpen: () => void;
  onAdd: (candidate: Candidate) => void;
}) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Close on a click anywhere else on the page, or on Escape.
  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!panelRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = () => {
    if (!open) onOpen();
    setOpen((v) => !v);
  };

  const surface = {
    background: "var(--surface)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-control)",
  };

  return (
    <div ref={panelRef} className="absolute bottom-6 right-6 z-10 flex flex-col items-end gap-2">
      {open && (
        <div
          role="dialog"
          aria-label="Companies related to this graph"
          className="flex w-80 flex-col"
          style={{ ...surface, boxShadow: "0 8px 24px rgba(0,0,0,0.12)" }}
        >
          <div className="flex items-center justify-between px-3 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
            <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
              Related, not on the graph
            </span>
            <span className="text-[10px] tabular-nums" role="status" style={{ color: "var(--text-tertiary)" }}>
              {reading > 0 ? `reading filings for ${reading}…` : `${candidates.length}`}
            </span>
          </div>
          <ul className="flex max-h-72 flex-col overflow-y-auto py-1">
            {candidates.length === 0 && (
              <li className="px-3 py-3 text-xs" style={{ color: "var(--text-secondary)" }}>
                {reading > 0 ? "Reading the linked companies' filings…" : "Nothing new: every related company is already on the graph."}
              </li>
            )}
            {candidates.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onAdd(c)}
                  className="flex w-full items-baseline gap-2 px-3 py-1.5 text-left hover:bg-[var(--bg)]"
                  title={`Add ${c.id} to the graph`}
                >
                  <span className="w-14 shrink-0 text-xs font-semibold tabular-nums" style={{ color: "var(--text-primary)" }}>
                    {c.id}
                  </span>
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-xs" style={{ color: "var(--text-secondary)" }}>
                      {c.node.name ?? c.node.label}
                    </span>
                    <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                      <span
                        aria-hidden
                        className="inline-block h-1.5 w-1.5 rounded-full"
                        style={{ background: RELATIONSHIP_COLORS[c.role] }}
                      />
                      {RELATIONSHIP_LABELS[c.role]} of {c.via}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex items-center gap-2 px-3 py-2 text-xs font-medium"
        style={{ ...surface, color: "var(--text-primary)" }}
      >
        {open ? <X size={14} weight="bold" /> : <Plus size={14} weight="bold" />}
        Add company
      </button>
    </div>
  );
}
