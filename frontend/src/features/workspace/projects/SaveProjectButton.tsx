import { Check, FloppyDisk, X } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { createProject, updateProject } from "../../../lib/api/projects";
import type { ProjectInput } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";

type SaveStatus = "idle" | "saving" | "done" | "error";

/**
 * "Save as project" action: a small square button that opens a centered name form.
 * Assembles the current WorkspaceState into a ProjectInput (Section 4 of the
 * architecture spec). PUTs over activeProjectId when present — including a
 * project just added empty from the sidebar — so saving updates it in place;
 * otherwise POSTs a new one and lets the backend assign the id.
 */
export function SaveProjectButton() {
  const { state, dispatch } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(state.activeProjectName ?? "");
  const [status, setStatus] = useState<SaveStatus>("idle");

  // The button stays mounted while the user opens other projects from the rail, so the
  // field is re-seeded from the active project each time the form opens: otherwise
  // "Update project" would PUT the previous project's name onto this one.
  useEffect(() => {
    if (open) setName(state.activeProjectName ?? "");
  }, [open, state.activeProjectName]);

  const canSave =
    Boolean(state.ticker && state.graph) && name.trim().length > 0;

  async function handleSave() {
    if (!state.ticker || !state.graph || !name.trim()) return;
    setStatus("saving");
    try {
      const input: ProjectInput = {
        name: name.trim(),
        ticker: state.ticker,
        graphSnapshot: state.graph,
        selectedMarketIds: state.selectedMarketIds,
        suggestedMarkets: state.suggestedMarkets,
        evidence: state.evidence,
        range: state.range,
      };
      const project = state.activeProjectId
        ? await updateProject(state.activeProjectId, input)
        : await createProject(input);
      dispatch({ type: "PROJECT_SAVED", id: project.id, name: project.name });
      setStatus("done");
      setTimeout(() => {
        setStatus("idle");
        setOpen(false);
      }, 900);
    } catch {
      setStatus("error");
    }
  }

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const label = state.activeProjectId ? "Update project" : "Save as project";

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={label}
        title={label}
        className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-control)] shadow-md transition hover:opacity-90"
        style={{ background: "var(--accent)", color: "var(--accent-contrast)" }}
      >
        <FloppyDisk size={18} weight="bold" />
      </button>

      {/* Portaled to <body>: the button sits inside a scroll/positioned column,
          and a modal centered on the screen shouldn't be clipped or offset by it. */}
      {createPortal(
        <AnimatePresence>
          {open && (
            <motion.div
              key="save-backdrop"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-4"
              style={{ background: "rgba(0,0,0,0.3)" }}
              onClick={() => setOpen(false)}
            >
              <motion.div
                role="dialog"
                aria-modal="true"
                aria-labelledby="save-project-title"
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 8, scale: 0.98 }}
                transition={{ duration: 0.16 }}
                onClick={(e) => e.stopPropagation()}
                className="w-full max-w-sm rounded-[var(--radius-panel)] p-5 shadow-xl"
                style={{
                  background: "var(--surface-elevated)",
                  border: "1px solid var(--border)",
                }}
              >
                <div className="mb-3 flex items-center justify-between">
                  <h2
                    id="save-project-title"
                    className="text-sm font-semibold"
                    style={{ color: "var(--text-primary)" }}
                  >
                    {label}
                  </h2>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    aria-label="Close"
                    className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-control)]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    <X size={14} />
                  </button>
                </div>

                <label
                  htmlFor="save-project-name"
                  className="mb-1.5 block text-xs font-semibold uppercase tracking-wide"
                  style={{ color: "var(--text-tertiary)" }}
                >
                  Project name
                </label>
                <input
                  id="save-project-name"
                  type="text"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && canSave && status !== "saving")
                      handleSave();
                  }}
                  placeholder={
                    state.ticker
                      ? `${state.ticker} thesis`
                      : "Name this project"
                  }
                  className="w-full rounded-[var(--radius-control)] px-3 py-2 text-sm outline-none"
                  style={{
                    background: "var(--surface)",
                    border: "1px solid var(--border)",
                    color: "var(--text-primary)",
                  }}
                />

                {status === "error" && (
                  <p
                    className="mt-2 text-xs"
                    style={{ color: "var(--status-negative)" }}
                    role="alert"
                  >
                    Couldn't save — try again.
                  </p>
                )}

                <div className="mt-4 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium"
                    style={{
                      color: "var(--text-secondary)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={!canSave || status === "saving"}
                    className="flex items-center gap-1.5 rounded-[var(--radius-control)] px-3.5 py-2 text-sm font-medium transition disabled:opacity-50"
                    style={{
                      background: "var(--accent)",
                      color: "var(--accent-contrast)",
                    }}
                  >
                    {status === "done" ? (
                      <>
                        <Check size={14} weight="bold" /> Saved
                      </>
                    ) : status === "saving" ? (
                      "Saving…"
                    ) : (
                      "Save"
                    )}
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
