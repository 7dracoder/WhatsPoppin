import type { RelationshipType } from "../../../types/workspaceGraph";
import { DIRECTION_COLOR, DIRECTION_LABEL } from "../../company-graph/labels";

/**
 * Fixed categorical palette for edge/relationship coloring.
 *
 * Deliberately NOT the locked --status-positive/--status-negative/--status-warning
 * tokens from index.css — those are reserved app-wide for alert/market-direction
 * semantics (bullish/bearish), and the design spec explicitly calls out that the
 * relationship palette and the status palette must never collide on the same hue,
 * or a user could misread "this edge is a competitor" as "this is bearish."
 *
 * Five of these hues are spaced through the blue -> violet -> magenta -> pink range,
 * away from the status tokens' red/green/amber territory, so the two systems stay
 * visually unambiguous at a glance. "partner" is a desaturated slate instead: that
 * hue range is full, and a muted color suits the one relationship that states
 * neither a direction nor a rivalry. Values are fixed (not re-themed per [data-theme])
 * because a stable relationship-to-color mapping across themes is more useful here
 * than theme-matching — same reasoning dataviz categorical palettes usually follow.
 */
export const RELATIONSHIP_COLORS: Record<RelationshipType, string> = {
  supplier: "#4C7BD9",
  customer: "#2BA8B8",
  partner: "#7C8AA5",
  competitor: "#8B5CF6",
  sector_peer: "#C34FC7",
  "board-interlock": "#E0529E",
};

export const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  supplier: "Supplier",
  customer: "Customer",
  partner: "Partner",
  competitor: "Competitor",
  sector_peer: "Industry peer",
  "board-interlock": "Board interlock",
};

const ORDER: RelationshipType[] = [
  "supplier",
  "customer",
  "partner",
  "competitor",
  "sector_peer",
  "board-interlock",
];

const DIRECTED: RelationshipType[] = ["supplier", "customer"];

/** What a directed edge means, phrased around the searched company when we know it. */
function describe(rel: RelationshipType, ticker: string | null): string | null {
  if (rel === "supplier")
    return ticker ? `sells to ${ticker}` : "sells to the company it points at";
  if (rel === "customer")
    return ticker
      ? `buys from ${ticker}`
      : "buys from the company pointing at it";
  return null;
}

/**
 * A short sample of the edge as it's drawn on the canvas: same color, and an
 * arrowhead only on the relationships that have a direction.
 */
function EdgeSwatch({ rel }: { rel: RelationshipType }) {
  const color = RELATIONSHIP_COLORS[rel];
  const directed = DIRECTED.includes(rel);
  return (
    <svg
      width="26"
      height="10"
      viewBox="0 0 26 10"
      className="shrink-0"
      aria-hidden="true"
    >
      <line
        x1="1"
        y1="5"
        x2={directed ? 18 : 25}
        y2="5"
        stroke={color}
        strokeWidth={rel === "board-interlock" ? 1 : 2.5}
        strokeLinecap="round"
      />
      {directed && <path d="M17 1 L25 5 L17 9 Z" fill={color} />}
    </svg>
  );
}

/** Fixed legend near the graph — color-coding here is never left for the user to infer. */
export function GraphLegend({
  showBoardInterlock = false,
  showHighlights = false,
  ticker = null,
}: {
  showBoardInterlock?: boolean;
  /** Whether any drawn company has a recent highlight (a colored node). */
  showHighlights?: boolean;
  ticker?: string | null;
}) {
  const items = showBoardInterlock
    ? ORDER
    : ORDER.filter((r) => r !== "board-interlock");

  return (
    <div
      className="flex flex-col gap-2 px-4 py-3"
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-control)",
      }}
    >
      <span
        className="text-[10px] font-semibold uppercase tracking-wider"
        style={{ color: "var(--text-tertiary)" }}
      >
        Relationship
      </span>
      {items.map((rel) => {
        const description = describe(rel, ticker);
        return (
          <div key={rel} className="flex items-center gap-2">
            <EdgeSwatch rel={rel} />
            <span
              className="text-xs"
              style={{ color: "var(--text-secondary)" }}
            >
              {RELATIONSHIP_LABELS[rel]}
              {description && (
                <span style={{ color: "var(--text-tertiary)" }}>
                  {" "}
                  — {description}
                </span>
              )}
            </span>
          </div>
        );
      })}
      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
        Arrows point from seller to buyer
      </span>
      {/* Always listed, even with nothing pulsing: a live move can start at any moment, and the
          key should already say what the ring means when it does. */}
      <span
        className="mt-1 text-[10px] font-semibold uppercase tracking-wider"
        style={{ color: "var(--text-tertiary)" }}
      >
        Live move · last hour
      </span>
      <div className="flex items-center gap-2">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full"
          style={{
            background: "var(--surface-elevated)",
            border: "1.5px solid var(--text-primary)",
            boxShadow:
              "0 0 0 4px color-mix(in srgb, var(--text-primary) 15%, transparent)",
          }}
        />
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Pulsing ring = a market it's exposed to moved sharply
        </span>
      </div>
      <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
        Direction is in the alerts list, not on the node
      </span>
      {showHighlights && (
        <>
          <span
            className="mt-1 text-[10px] font-semibold uppercase tracking-wider"
            style={{ color: "var(--text-tertiary)" }}
          >
            Recent event
          </span>
          {(["may_benefit", "may_face_pressure"] as const).map((direction) => (
            <div key={direction} className="flex items-center gap-2">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{
                  background: DIRECTION_COLOR[direction],
                  boxShadow: `0 0 0 3px color-mix(in srgb, ${DIRECTION_COLOR[direction]} 25%, transparent)`,
                }}
              />
              <span
                className="text-xs"
                style={{ color: "var(--text-secondary)" }}
              >
                {DIRECTION_LABEL[direction].replace(/^./, (c) =>
                  c.toUpperCase(),
                )}
              </span>
            </div>
          ))}
          <span
            className="text-[10px]"
            style={{ color: "var(--text-tertiary)" }}
          >
            Colored company = a recent news event; click it for details
          </span>
        </>
      )}
    </div>
  );
}
