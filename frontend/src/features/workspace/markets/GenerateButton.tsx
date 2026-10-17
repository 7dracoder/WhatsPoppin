import { motion } from "motion/react";
import { ArrowRight } from "@phosphor-icons/react";
import { useWorkspace } from "../useWorkspace";

/**
 * Stage 5 trigger. Disabled until at least one market is selected. Dispatches
 * `GENERATE_PRESSED`, which flips `stage` to "market-view" — the slide
 * transition and everything after it belongs to the market-view feature,
 * not this component.
 */
export function GenerateButton() {
  const { state, dispatch } = useWorkspace();
  const count = state.selectedMarketIds.length;
  const disabled = count === 0;

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-sm" style={{ color: "var(--text-tertiary)" }}>
        {count === 0 ? "Select at least one market to continue." : `${count} market${count === 1 ? "" : "s"} selected`}
      </span>
      <motion.button
        type="button"
        disabled={disabled}
        onClick={() => dispatch({ type: "GENERATE_PRESSED" })}
        whileHover={disabled ? undefined : { scale: 1.02 }}
        whileTap={disabled ? undefined : { scale: 0.98 }}
        className="flex items-center gap-2 rounded-[var(--radius-pill)] px-5 py-2.5 text-sm font-semibold"
        style={{
          backgroundColor: disabled ? "var(--border)" : "var(--accent)",
          color: disabled ? "var(--text-tertiary)" : "var(--accent-contrast)",
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        Generate
        <ArrowRight size={16} weight="bold" />
      </motion.button>
    </div>
  );
}
