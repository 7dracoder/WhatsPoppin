import { CaretLeft, CaretRight } from "@phosphor-icons/react";
import type { ChartRange } from "../../../types/market";
import { useWorkspace } from "../useWorkspace";

const OPTIONS: Array<{ value: ChartRange; label: string; name: string }> = [
  { value: "1d", label: "1D", name: "1 day" },
  { value: "1w", label: "1W", name: "1 week" },
  { value: "1m", label: "1M", name: "1 month" },
  { value: "6m", label: "6M", name: "6 months" },
  { value: "1y", label: "1Y", name: "1 year" },
  { value: "all", label: "ALL", name: "Since the market opened" },
];

/**
 * Segmented control for the chart's time window. Radio semantics (one of N,
 * arrow keys move selection) via native radio inputs — free keyboard and
 * screen-reader behavior without hand-rolled key handling. `available` greys
 * out ranges a selected market is too young for (null/undefined = all offered).
 */
/** "Fri, Oct 9" for the day the window actually ended on (the backend may have snapped a weekend to Friday). */
function formatDay(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
}

/**
 * Step the chart's window a day at a time. Back always works; forward is
 * disabled at "now". `windowEnd` is the last data point the chart got, so the
 * label shows the session on screen (Friday, when a weekend was asked for).
 */
export function DayStepper({ windowEnd }: { windowEnd: number | null }) {
  const { state, dispatch } = useWorkspace();
  const atNow = state.chartDay === null;
  const label = windowEnd !== null ? formatDay(windowEnd) : atNow ? "Latest" : state.chartDay;
  const button = "flex h-6 w-6 items-center justify-center rounded-[calc(var(--radius-control)-2px)] disabled:cursor-not-allowed disabled:opacity-40";
  return (
    <div
      className="inline-flex items-center gap-0.5 rounded-[var(--radius-control)] p-0.5"
      style={{ background: "var(--surface-elevated)", border: "1px solid var(--border)" }}
      aria-label="Chart day"
    >
      <button
        type="button"
        onClick={() => dispatch({ type: "CHART_DAY_SHIFTED", days: -1 })}
        aria-label="Previous day"
        title="Previous day"
        className={button}
        style={{ color: "var(--text-secondary)" }}
      >
        <CaretLeft size={12} weight="bold" />
      </button>
      <button
        type="button"
        onClick={() => dispatch({ type: "CHART_DAY_RESET" })}
        disabled={atNow}
        aria-label={atNow ? "Showing the latest window" : "Back to the latest window"}
        title={atNow ? "Latest" : "Back to latest"}
        className="min-w-[5.5rem] px-2 py-1 text-xs font-medium tabular-nums disabled:cursor-default"
        style={{ color: atNow ? "var(--text-secondary)" : "var(--text-primary)" }}
      >
        {label}
      </button>
      <button
        type="button"
        onClick={() => dispatch({ type: "CHART_DAY_SHIFTED", days: 1 })}
        disabled={atNow}
        aria-label="Next day"
        title="Next day"
        className={button}
        style={{ color: "var(--text-secondary)" }}
      >
        <CaretRight size={12} weight="bold" />
      </button>
    </div>
  );
}

export function RangeSelector({ available }: { available?: ChartRange[] | null }) {
  const { state, dispatch } = useWorkspace();

  return (
    <div
      role="radiogroup"
      aria-label="Chart time range"
      className="inline-flex rounded-[var(--radius-control)] p-0.5"
      style={{ background: "var(--surface-elevated)", border: "1px solid var(--border)" }}
    >
      {OPTIONS.map((option) => {
        const selected = state.range === option.value;
        // Never disable the active choice: the user must always be able to see what they are on.
        const disabled = !selected && !!available && !available.includes(option.value);
        return (
          <label
            key={option.value}
            title={disabled ? `${option.name} (a selected market is too new)` : option.name}
            className={`${disabled ? "cursor-not-allowed opacity-40" : "cursor-pointer"} rounded-[calc(var(--radius-control)-2px)] px-2.5 py-1 text-xs font-medium transition focus-within:ring-2`}
            style={{
              background: selected ? "var(--accent)" : "transparent",
              color: selected ? "var(--accent-contrast)" : "var(--text-secondary)",
              ["--tw-ring-color" as string]: "var(--accent)",
            }}
          >
            <input
              type="radio"
              name="chart-range"
              value={option.value}
              checked={selected}
              disabled={disabled}
              onChange={() => dispatch({ type: "RANGE_CHANGED", range: option.value })}
              aria-label={option.name}
              className="sr-only"
            />
            {option.label}
          </label>
        );
      })}
    </div>
  );
}
