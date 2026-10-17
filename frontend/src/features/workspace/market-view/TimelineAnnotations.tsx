import { useState } from "react";
import { ReferenceLine, usePlotArea } from "recharts";
import type { EvidenceAnnotation } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";
import { SENTIMENT_COLOR, SENTIMENT_GLYPH, SENTIMENT_LABEL } from "./sentiment";

/**
 * What recharts hands a vertical ReferenceLine's `shape`. (x1, y1) is the
 * BOTTOM of the plot and (x2, y2) the top, the opposite of how it reads, so
 * markers must derive top/bottom with min/max rather than trust the order.
 */
interface MarkerShapeProps {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const TAG_W = 32;
const TAG_H = 20;
const TIP_CHAR_W = 7; // rough glyph width at 12px, for sizing the tooltip box
const TIP_MAX_CHARS = 60;
const TIP_H = 50;
const TIP_GAP = 6;
/**
 * Room a chart must always leave above its plot for the hover box. Constant on purpose: reserving it
 * only once evidence exists made the whole plot jump down the moment the first item was added.
 */
export const TIP_HEADROOM = TIP_H + TIP_GAP + 4;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * A tag hanging off the top of a dotted line: filled with the item's Jev
 * sentiment color (red/green/gray), lettered T (tweet) or N (news). Hovering
 * or focusing it shows the item's name in a small box underneath.
 */
function EvidenceMarker({
  annotation,
  isPinned,
  isHovered,
  onPin,
  onHover,
  line,
  variant,
}: {
  variant: "tags" | "lines";
  annotation: EvidenceAnnotation;
  isPinned: boolean;
  isHovered: boolean;
  onPin: () => void;
  onHover: (hovered: boolean) => void;
  line: MarkerShapeProps;
}) {
  const plot = usePlotArea();
  const { item, sentiment } = annotation;
  const kind = item.source === "twitter" ? "tweet" : "news";
  const tone = sentiment ? SENTIMENT_COLOR[sentiment] : "var(--text-tertiary)";
  const fill = isPinned ? "var(--accent)" : tone;
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onPin();
    }
  };

  const name = truncate(item.title, TIP_MAX_CHARS);
  const detail = `${item.author ?? (item.source === "twitter" ? "Tweet" : "News")}${
    sentiment ? ` · ${SENTIMENT_GLYPH[sentiment]} ${SENTIMENT_LABEL[sentiment]}` : ""
  }`;
  const tipW = Math.min(420, Math.max(name.length, detail.length) * TIP_CHAR_W + 20);
  // Centered on the tag, but kept inside the plot so it never runs off the chart's edge.
  const minX = plot?.x ?? line.x1 - 1000;
  const maxX = (plot ? plot.x + plot.width : line.x1 + 1000) - tipW;
  const tipX = Math.min(Math.max(line.x1 - tipW / 2, minX), maxX);
  const top = Math.min(line.y1, line.y2);
  const bottom = Math.max(line.y1, line.y2);
  // Above the tag so it never covers the data; flips below only if there is no room above.
  const tipY = top - TIP_GAP - TIP_H >= 0 ? top - TIP_GAP - TIP_H : top + TAG_H + TIP_GAP;

  // In the panel below the tags the line is only a continuation of the one
  // above: same color, top to bottom of the plot, no tag or tooltip.
  if (variant === "lines") {
    return (
      <line
        x1={line.x1}
        y1={top}
        x2={line.x1}
        y2={bottom}
        stroke={fill}
        strokeWidth={2}
        strokeDasharray="2 4"
        strokeLinecap="round"
        opacity={isPinned ? 1 : 0.55}
      />
    );
  }

  return (
    <g>
      <line
        x1={line.x1}
        y1={top + TAG_H}
        x2={line.x1}
        y2={bottom}
        stroke={fill}
        strokeWidth={2}
        strokeDasharray="2 4"
        strokeLinecap="round"
        opacity={isPinned ? 1 : 0.55}
      />
      <g
        role="button"
        tabIndex={0}
        aria-label={`View ${kind} evidence: ${item.title}${sentiment ? ` (${SENTIMENT_LABEL[sentiment]} sentiment)` : ""}`}
        onClick={onPin}
        onKeyDown={onKeyDown}
        onMouseEnter={() => onHover(true)}
        onMouseLeave={() => onHover(false)}
        onFocus={() => onHover(true)}
        onBlur={() => onHover(false)}
        style={{ cursor: "pointer", outline: "none" }}
      >
        <rect x={line.x1 - TAG_W / 2} y={top} width={TAG_W} height={TAG_H} rx={4} fill={fill} />
        {/* a small notch joining the tag to its line */}
        <path
          d={`M${line.x1 - 4} ${top + TAG_H} L${line.x1 + 4} ${top + TAG_H} L${line.x1} ${top + TAG_H + 4} Z`}
          fill={fill}
        />
        <text
          x={line.x1}
          y={top + TAG_H / 2}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={11}
          fontWeight={700}
          fill="var(--accent-contrast)"
          style={{ pointerEvents: "none" }}
        >
          {item.source === "twitter" ? "T" : "N"}
        </text>
      </g>
      {isHovered && (
        <g style={{ pointerEvents: "none" }}>
          <rect
            x={tipX}
            y={tipY}
            width={tipW}
            height={TIP_H}
            rx={6}
            fill="var(--surface-elevated)"
            stroke="var(--border)"
          />
          <text x={tipX + 10} y={tipY + 20} fontSize={12} fontWeight={600} fill="var(--text-primary)">
            {name}
          </text>
          <text x={tipX + 10} y={tipY + 38} fontSize={12} fill={tone}>
            {detail}
          </text>
        </g>
      )}
    </g>
  );
}

/**
 * Evidence markers as recharts <ReferenceLine>s, so they share the chart's own
 * x scale (no hand-measured overlay insets). Must render inside the chart.
 * Requires a numeric time XAxis: `x` is the evidence's epoch ms, no snapping
 * to the data grid needed. Items outside `domain` are dropped.
 */
export function TimelineAnnotations({
  domain,
  variant = "tags",
}: {
  domain: [number, number];
  /** "tags" draws the tag, line and hover box; "lines" only the line, to continue it through another panel. */
  variant?: "tags" | "lines";
}) {
  const { state, dispatch } = useWorkspace();
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [start, end] = domain;

  // Pinned and hovered paint last so they (and the hover box) sit on top of their neighbours.
  const lift = (id: string) => Number(id === state.pinnedAnnotationId) + Number(id === hoveredId) * 2;
  const visible = state.evidence
    .map((annotation) => ({ annotation, t: Date.parse(annotation.timestamp) }))
    .filter(({ t }) => Number.isFinite(t) && t >= start && t <= end)
    .sort((a, b) => lift(a.annotation.id) - lift(b.annotation.id));

  return (
    <>
      {visible.map(({ annotation, t }) => {
        const isPinned = state.pinnedAnnotationId === annotation.id;
        return (
          <ReferenceLine
            key={annotation.id}
            x={t}
            ifOverflow="visible"
            shape={(line: MarkerShapeProps) => (
              <EvidenceMarker
                annotation={annotation}
                isPinned={isPinned}
                isHovered={hoveredId === annotation.id}
                line={line}
                variant={variant}
                onPin={() => dispatch({ type: "EVIDENCE_PINNED", annotationId: annotation.id })}
                onHover={(h) => setHoveredId((cur) => (h ? annotation.id : cur === annotation.id ? null : cur))}
              />
            )}
          />
        );
      })}
    </>
  );
}
