import { Trash } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import type { Project } from "../../../types/project";

/** Rough "2h ago" formatting — this is a quick-recall list, not a detail view, so precision doesn't matter. */
function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

interface ProjectTabProps {
  project: Project;
  active: boolean;
  expanded: boolean;
  onSelect: () => void;
  /** Resolves once the project is gone; rejects to keep the row and show an error. */
  onDelete: () => Promise<void>;
}

/**
 * One saved-project row. `expanded` controls whether the name/ticker/timestamp
 * are visible or the tab is collapsed to just a glyph — driven by the rail's
 * hover-preview/pin state in ProjectSidebar, not anything local to this component.
 *
 * Deleting takes two clicks on the trash icon: the first turns it into a
 * "Delete?" confirm that times out, the second deletes. That keeps a
 * mis-click harmless without a modal, which would be heavy for a rail. The
 * row is a div with two buttons (select and delete) rather than one button:
 * a button inside a button is invalid HTML and breaks keyboard focus.
 */
export function ProjectTab({ project, active, expanded, onSelect, onDelete }: ProjectTabProps) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [failed, setFailed] = useState(false);
  const glyph = (project.ticker ?? project.name).slice(0, 2);
  const subtitle = failed
    ? "Couldn't delete — try again"
    : project.ticker
      ? `${project.ticker} · saved ${relativeTime(project.updatedAt)}`
      : `No ticker yet · added ${relativeTime(project.createdAt)}`;

  // An unconfirmed delete forgets itself after a moment, and when the rail collapses.
  useEffect(() => {
    if (!confirming || !expanded) {
      setConfirming(false);
      return;
    }
    const t = window.setTimeout(() => setConfirming(false), 4000);
    return () => window.clearTimeout(t);
  }, [confirming, expanded]);

  async function handleDelete() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setDeleting(true);
    setFailed(false);
    try {
      await onDelete();
    } catch {
      setFailed(true);
      setDeleting(false);
      setConfirming(false);
    }
  }

  const fg = active ? "var(--accent-contrast)" : "var(--text-primary)";

  return (
    <div
      className="group flex w-full items-center rounded-[var(--radius-control)] transition"
      style={{ background: active ? "var(--accent)" : "transparent", color: fg, opacity: deleting ? 0.5 : 1 }}
    >
      <button
        type="button"
        onClick={onSelect}
        disabled={deleting}
        title={project.ticker ? `${project.name} (${project.ticker})` : project.name}
        className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-2 text-left"
      >
        <span
          className="flex h-6 w-6 flex-none items-center justify-center rounded-full text-[10px] font-semibold uppercase"
          style={{
            background: active ? "var(--accent-contrast)" : "var(--surface-elevated)",
            color: active ? "var(--accent)" : "var(--text-secondary)",
            border: active ? "none" : "1px solid var(--border)",
          }}
        >
          {glyph}
        </span>
        {expanded && (
          <span className="flex min-w-0 flex-1 flex-col overflow-hidden">
            <span className="truncate text-sm font-medium leading-tight">{project.name}</span>
            <span
              className="truncate text-xs leading-tight"
              style={{
                color: failed ? "var(--status-negative)" : active ? "var(--accent-contrast)" : "var(--text-tertiary)",
                opacity: active && !failed ? 0.85 : 1,
              }}
            >
              {subtitle}
            </span>
          </span>
        )}
      </button>
      {expanded && (
        <button
          type="button"
          onClick={handleDelete}
          disabled={deleting}
          aria-label={confirming ? `Confirm deleting ${project.name}` : `Delete ${project.name}`}
          title={confirming ? "Click again to delete" : "Delete project"}
          className="mr-1.5 flex h-7 flex-none items-center gap-1 rounded-[var(--radius-control)] px-1.5 text-[11px] font-medium opacity-0 transition focus:opacity-100 group-hover:opacity-100"
          style={{
            // Always visible while confirming, so the second click has a target even if the pointer drifted.
            opacity: confirming ? 1 : undefined,
            background: confirming ? "var(--status-negative)" : "transparent",
            color: confirming ? "#fff" : active ? "var(--accent-contrast)" : "var(--text-tertiary)",
          }}
        >
          <Trash size={13} weight="bold" />
          {confirming && "Delete?"}
        </button>
      )}
    </div>
  );
}
