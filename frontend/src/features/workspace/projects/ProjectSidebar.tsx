import { CaretRight, Plus, PushPin, PushPinSlash } from "@phosphor-icons/react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { createProject, deleteProject, listProjects } from "../../../lib/api/projects";
import type { Project } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";
import { ProjectTab } from "./ProjectTab";

const COLLAPSED_WIDTH = 56;
const EXPANDED_WIDTH = 240;

/**
 * Stage 10 — left-edge projects rail.
 *
 * Accessibility correction from the design spec: hover-only reveal is
 * explicitly flagged as insufficient (no touch support, nothing for keyboard
 * focus to land on), so this rail has THREE independent ways to open:
 *   1. Hover-to-preview — mouseover widens the rail (sighted mouse users).
 *   2. Click-to-pin — clicking the pin icon (or the rail's own collapsed
 *      button when nothing is pinned yet) keeps it open regardless of
 *      hover state, and persists across mouse-leave.
 *   3. Keyboard focus — focusing any tab inside the rail (Tab key) expands
 *      it exactly like hover does, via onFocus/onBlur on the container, so
 *      a keyboard-only user can reach the list without ever touching a mouse.
 * `expanded` (what actually renders names) is `pinned || hovered || focused`.
 */
export function ProjectSidebar() {
  const { state, dispatch } = useWorkspace();
  const [projects, setProjects] = useState<Project[]>([]);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [loadError, setLoadError] = useState(false);
  // Inline "new project" name entry — counts as a reason to stay expanded so
  // the input doesn't collapse out from under the user mid-typing.
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createStatus, setCreateStatus] = useState<"idle" | "saving" | "error">("idle");
  // Gates text rendering separately from the rail's own open/closed state:
  // without this, "expanded && <text>" renders the instant hover/focus/pin
  // fires, so labels pop in at the *collapsed* width and visibly reflow wider
  // as the spring-driven width animation plays out underneath them. Only
  // show text once that width animation has actually finished.
  const [widthSettled, setWidthSettled] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    listProjects()
      .then((loaded) => {
        if (cancelled) return;
        setProjects(loaded);
        setLoadError(false);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [state.activeProjectId, state.projectsRevision]); // refetch after a create/save so the list stays current

  const expanded = pinned || hovered || focused || creating;

  useEffect(() => {
    if (!expanded) setWidthSettled(false);
  }, [expanded]);

  const showText = expanded && widthSettled;

  function handleSelect(project: Project) {
    // GET /projects returns full Project objects (graphSnapshot, evidence,
    // etc. included), so no extra getProject() round-trip is needed before
    // restoring the workspace.
    dispatch({ type: "PROJECT_LOADED", project });
  }

  async function handleDelete(project: Project) {
    await deleteProject(project.id); // a failure propagates to the row, which shows it
    setProjects((prev) => prev.filter((p) => p.id !== project.id));
    // The open workspace was this project's: back to the ticker prompt, so a
    // later save can't try to update a row that no longer exists.
    if (project.id === state.activeProjectId) dispatch({ type: "RESET" });
  }

  function cancelCreate() {
    setCreating(false);
    setNewName("");
    setCreateStatus("idle");
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = newName.trim();
    if (!name || createStatus === "saving") return;
    setCreateStatus("saving");
    try {
      const project = await createProject({
        name,
        ticker: null,
        graphSnapshot: null,
        selectedMarketIds: [],
        suggestedMarkets: [],
        evidence: [],
      });
      setProjects((prev) => [project, ...prev]);
      dispatch({ type: "PROJECT_LOADED", project }); // lands on the ticker prompt, attached to this project
      cancelCreate();
    } catch {
      setCreateStatus("error");
    }
  }

  function handleBlurCapture(e: React.FocusEvent<HTMLDivElement>) {
    if (!containerRef.current?.contains(e.relatedTarget as Node | null)) {
      setFocused(false);
    }
  }

  return (
    <motion.div
      ref={containerRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={handleBlurCapture}
      animate={{ width: expanded ? EXPANDED_WIDTH : COLLAPSED_WIDTH }}
      initial={false}
      transition={{ type: "spring", stiffness: 320, damping: 32 }}
      onAnimationComplete={() => {
        if (expanded) setWidthSettled(true);
      }}
      className="absolute left-0 top-0 z-30 flex h-full flex-col overflow-hidden border-r"
      style={{ background: "var(--surface)", borderColor: "var(--border)" }}
      aria-label="Saved projects"
    >
      <div className="flex items-center gap-2 px-2.5 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <button
          type="button"
          onClick={() => setPinned((p) => !p)}
          aria-pressed={pinned}
          aria-label={pinned ? "Unpin projects rail" : "Pin projects rail open"}
          title={pinned ? "Unpin" : "Pin open"}
          className="flex h-7 w-7 flex-none items-center justify-center rounded-[var(--radius-control)] transition"
          style={{
            background: pinned ? "var(--accent)" : "transparent",
            color: pinned ? "var(--accent-contrast)" : "var(--text-secondary)",
          }}
        >
          {pinned ? <PushPin size={15} weight="fill" /> : <PushPinSlash size={15} weight="bold" />}
        </button>
        <AnimatePresence>
          {showText && (
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 truncate text-xs font-semibold uppercase tracking-wide"
              style={{ color: "var(--text-tertiary)" }}
            >
              Projects
            </motion.span>
          )}
        </AnimatePresence>
        {!expanded && <CaretRight size={13} style={{ color: "var(--text-tertiary)" }} />}
      </div>

      <div className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        {creating && showText ? (
          <form onSubmit={handleCreate} className="flex flex-col gap-1 px-0.5 pb-1">
            <input
              type="text"
              autoFocus
              value={newName}
              maxLength={200}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") cancelCreate();
              }}
              onBlur={() => {
                if (!newName.trim()) cancelCreate();
              }}
              disabled={createStatus === "saving"}
              placeholder="Project name, then Enter"
              aria-label="New project name"
              className="w-full rounded-[var(--radius-control)] px-2.5 py-1.5 text-sm outline-none"
              style={{
                background: "var(--surface-elevated)",
                border: "1px solid var(--accent)",
                color: "var(--text-primary)",
              }}
            />
            {createStatus === "error" && (
              <p className="px-1 text-xs" style={{ color: "var(--status-negative)" }}>
                Couldn't add — is the API running?
              </p>
            )}
          </form>
        ) : (
          <button
            type="button"
            onClick={() => setCreating(true)}
            aria-label="Add project"
            title="New project"
            className="flex w-full items-center gap-2.5 rounded-[var(--radius-control)] px-2.5 py-2 text-left transition"
            style={{ color: "var(--text-secondary)" }}
          >
            <span
              className="flex h-6 w-6 flex-none items-center justify-center rounded-full"
              style={{ border: "1px dashed var(--border)" }}
            >
              <Plus size={12} weight="bold" />
            </span>
            {showText && <span className="truncate text-sm font-medium">New project</span>}
          </button>
        )}

        {loadError && showText && (
          <p className="px-1 py-2 text-xs" style={{ color: "var(--status-negative)" }}>
            Couldn't load projects — is the API running?
          </p>
        )}
        {!loadError && projects.length === 0 && showText && (
          <p className="px-1 py-2 text-xs" style={{ color: "var(--text-tertiary)" }}>
            No saved projects yet.
          </p>
        )}
        {projects.map((project) => (
          <ProjectTab
            key={project.id}
            project={project}
            active={project.id === state.activeProjectId}
            expanded={showText}
            onSelect={() => handleSelect(project)}
            onDelete={() => handleDelete(project)}
          />
        ))}
      </div>
    </motion.div>
  );
}
