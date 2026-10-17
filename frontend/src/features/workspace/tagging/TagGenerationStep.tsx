import { useEffect } from "react";
import { motion } from "motion/react";
import { Sparkle } from "@phosphor-icons/react";
import { useWorkspace } from "../useWorkspace";
import { generateTags } from "../../../lib/api/tags";

/**
 * Stage 3 — the Gemini tag-generation step.
 *
 * This is the one place in Stages 3-4 that puts a live LLM call in the
 * critical path (see frontend-design-spec.md Stage 3 / Open Questions, and
 * frontend-architecture-spec.md Section 6). The skeleton below is shaped
 * like the chip row it resolves into, not a generic spinner, so the layout
 * doesn't jump and the user can see "this becomes tags" while it loads.
 *
 * Fires once per graph: when `state.graph` is complete (the backend builds
 * it over several polled responses — tags for half a graph would be wrong)
 * and `tagsStatus` is still "idle", it kicks off the mocked Gemini call.
 */
export function TagGenerationStep() {
  const { state, dispatch } = useWorkspace();

  useEffect(() => {
    if (!state.graph || state.graphStatus === "loading" || state.tagsStatus !== "idle") return;

    let cancelled = false;
    dispatch({ type: "TAGS_STATUS", status: "loading" });

    generateTags(state.graph)
      .then((tags) => {
        if (cancelled) return;
        dispatch({ type: "TAGS_LOADED", tags });
      })
      .catch(() => {
        if (cancelled) return;
        dispatch({ type: "TAGS_STATUS", status: "error" });
      });

    return () => {
      cancelled = true;
    };
  }, [state.graph, state.graphStatus, state.tagsStatus, dispatch]);

  if (!state.graph || state.graphStatus === "loading") return null;

  return (
    <section aria-label="Generated tags" className="flex flex-col gap-3">
      {/* Intro caption only earns its place while there's nothing else to
          look at yet — once the chips are actually showing, the label is
          redundant with the content it was explaining. */}
      {state.tagsStatus !== "done" && (
        <div className="flex items-center gap-2">
          <Sparkle size={16} weight="fill" style={{ color: "var(--accent)" }} />
          <p className="text-sm font-medium" style={{ color: "var(--text-secondary)" }}>
            {state.tagsStatus === "error"
              ? "Couldn't generate tags — showing the graph only."
              : "Based on this graph, here's what we're watching:"}
          </p>
        </div>
      )}

      {state.tagsStatus === "loading" && <TagSkeletonRow />}

      {state.tagsStatus === "done" && state.tags.length > 0 && (
        <motion.ul
          className="flex flex-wrap gap-2"
          initial="hidden"
          animate="visible"
          variants={{
            hidden: {},
            visible: { transition: { staggerChildren: 0.06 } },
          }}
        >
          {state.tags.map((tag) => (
            <motion.li
              key={tag}
              variants={{
                hidden: { opacity: 0, y: 6 },
                visible: { opacity: 1, y: 0 },
              }}
              className="rounded-[var(--radius-pill)] px-3 py-1.5 text-sm font-medium"
              style={{
                backgroundColor: "var(--surface-elevated)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
            >
              {tag}
            </motion.li>
          ))}
        </motion.ul>
      )}
    </section>
  );
}

/**
 * Chip-shaped placeholder bars (not a spinner) — same width distribution as a
 * typical tag row, so the skeleton reads as "tags are coming," per the
 * dashboard skill's "skeleton shaped like the final thing" principle.
 */
function TagSkeletonRow() {
  const widths = [96, 136, 112, 150];
  return (
    <div className="flex flex-wrap gap-2" aria-hidden="true">
      {widths.map((w, i) => (
        <motion.span
          key={i}
          className="h-[30px] rounded-[var(--radius-pill)]"
          style={{ width: w, backgroundColor: "var(--border)" }}
          animate={{ opacity: [0.5, 0.9, 0.5] }}
          transition={{ duration: 1.3, repeat: Infinity, ease: "easeInOut", delay: i * 0.12 }}
        />
      ))}
    </div>
  );
}
