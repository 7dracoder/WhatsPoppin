import { useRef, useState, type PointerEvent } from "react";
import { TrendDown, TrendUp } from "@phosphor-icons/react";
import { highlightsFor } from "../../company-graph/highlights";
import {
  DIRECTION_COLOR,
  DIRECTION_LABEL,
  EVENT_LABEL,
} from "../../company-graph/labels";
import type {
  CompanyGraph,
  GraphEdge,
  GraphNode,
  RelationshipType,
} from "../../../types/workspaceGraph";
import {
  DIRECTION_LABELS,
  TIER_LABELS,
  directionColor,
  summaryDetail,
  timeAgo,
} from "../alerts/alertFormat";
import type { Flash } from "../alerts/LiveAlertsProvider";
import { useCompanyAlerts } from "../alerts/useAlerts";
import type { Alert, CompanyAlert } from "../../../types/alert";
import { SourceMark } from "../shared/SourceMark";
import { RELATIONSHIP_COLORS, RELATIONSHIP_LABELS } from "./GraphLegend";
import { reversed } from "./relatedCompanies";
import { PairNewsSection } from "./PairNewsSection";

/**
 * A relationship type names the *target's* role for the source ("supplier"
 * means the target supplies the source). Read from the other end, supplier
 * and customer swap; the undirected types stay as they are.
 */
interface Relation {
  edge: GraphEdge;
  /** The company on the other end of the edge. */
  other: string;
  /** This company's role for `other`. */
  role: RelationshipType;
}

/** Every company-to-company edge touching `id`, phrased from `id`'s side. */
function relationsOf(graph: CompanyGraph, id: string): Relation[] {
  return graph.edges.flatMap((edge) => {
    if (edge.target === id)
      return [{ edge, other: edge.source, role: edge.relationship }];
    if (edge.source === id)
      return [{ edge, other: edge.target, role: reversed(edge.relationship) }];
    return [];
  });
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** The most market moves a card lists; the sidebar's alert list has the rest. */
const MAX_MOVES = 3;

/**
 * One prediction-market move tied to a company: which market moved, which way
 * and by how much, and why that market was matched to the company.
 */
function MarketMove({
  alert,
  why,
  when,
}: {
  alert: Alert;
  why: string;
  when: string;
}) {
  const Trend = alert.direction === "yes_up" ? TrendUp : TrendDown;
  const title = alert.eventTitle ?? alert.title ?? alert.marketId;
  return (
    <li
      className="flex flex-col gap-1 px-2.5 py-2"
      style={{
        background: "var(--bg)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-control)",
      }}
    >
      <div className="flex items-start gap-2">
        <SourceMark
          source={alert.source === "kalshi" ? "kalshi" : "polymarket"}
        />
        {alert.url ? (
          <a
            href={alert.url}
            target="_blank"
            rel="noreferrer"
            className="text-xs font-semibold leading-snug hover:underline"
            style={{ color: "var(--text-primary)" }}
          >
            {title}
          </a>
        ) : (
          <p
            className="text-xs font-semibold leading-snug"
            style={{ color: "var(--text-primary)" }}
          >
            {title}
          </p>
        )}
      </div>
      {/* The detector titles a market by its event when it has one; the outcome that moved is the market's own title. */}
      {alert.eventTitle && alert.title && alert.title !== alert.eventTitle && (
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Outcome: {alert.title}
        </p>
      )}
      <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
        <span
          className="mr-1.5 inline-flex items-center gap-0.5 font-semibold"
          style={{ color: directionColor(alert.direction) }}
        >
          <Trend size={10} weight="bold" aria-hidden="true" />
          {DIRECTION_LABELS[alert.direction]}
        </span>
        {summaryDetail(alert.summary)}
      </p>
      <p
        className="flex gap-2 text-[10px]"
        style={{ color: "var(--text-tertiary)" }}
      >
        <span className="min-w-0 truncate">{why}</span>
        <span className="ml-auto shrink-0 tabular-nums">{when}</span>
      </p>
    </li>
  );
}

function whyMatched(alert: CompanyAlert): string {
  return alert.tier === 2 && alert.matched.length > 0
    ? `${TIER_LABELS[2]} via ${alert.matched.join(", ")}`
    : TIER_LABELS[alert.tier];
}

/**
 * The prediction-market moves tied to a company: the last day's detector
 * alerts matched to it, newest first. The node only pulses for a move in the
 * last hour, but the card keeps the day's moves so the event behind a pulse is
 * still here after the ring stops. A pulse that came from the recommendations
 * list (a large 24h change, not a detector alert) is listed when there's
 * nothing else to show.
 */
function MarketMoves({ symbol, flash }: { symbol: string; flash?: Flash }) {
  const { alerts, status } = useCompanyAlerts(symbol);
  const moves = status === "ready" ? alerts.slice(0, MAX_MOVES) : [];
  if (moves.length === 0 && !flash) return null;
  return (
    <section
      aria-label="Prediction market moves"
      className="flex flex-col gap-1.5"
    >
      <p
        className="text-[10px] font-semibold uppercase tracking-wider"
        style={{ color: "var(--text-tertiary)" }}
      >
        Market moves · 24h
        {alerts.length > moves.length && moves.length > 0 && (
          <span className="font-normal normal-case tracking-normal">
            {" "}
            · latest {moves.length} of {alerts.length}
          </span>
        )}
      </p>
      <ul className="flex flex-col gap-1.5">
        {moves.length > 0
          ? moves.map((a) => (
              <MarketMove
                key={a.id}
                alert={a}
                why={whyMatched(a)}
                when={timeAgo(a.createdAt)}
              />
            ))
          : flash && (
              <MarketMove
                alert={flash.alert}
                why="recommended market"
                when="last 24h"
              />
            )}
      </ul>
    </section>
  );
}

interface Size {
  width: number;
  height: number;
}

const MIN_SIZE: Size = { width: 240, height: 140 };
/** Room kept between the card and the pane's right and bottom edges. */
const EDGE_MARGIN = 24;
/** The size the card was last dragged to, kept for the next card opened this session. */
let rememberedSize: Size | null = null;

/**
 * Drag-to-resize from the bottom-right corner. Until the first drag the card
 * keeps its CSS size (a fixed width, as tall as its content up to a cap); from
 * then on it has the explicit size the user gave it.
 *
 * Pointer capture sends every move to the handle even when the pointer runs
 * ahead of it or leaves the card, so the drag can't be dropped halfway, and it
 * covers mouse, touch and pen with one set of handlers.
 */
function useCornerResize() {
  const cardRef = useRef<HTMLElement>(null);
  const [size, setSize] = useState<Size | null>(rememberedSize);
  const drag = useRef<{ x: number; y: number; start: Size } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const card = cardRef.current;
    if (!card) return;
    e.preventDefault(); // no text selection while dragging
    e.currentTarget.setPointerCapture(e.pointerId);
    const rect = card.getBoundingClientRect();
    drag.current = {
      x: e.clientX,
      y: e.clientY,
      start: { width: rect.width, height: rect.height },
    };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const card = cardRef.current;
    const pane = card?.offsetParent;
    if (!d || !card || !pane) return;
    // Never past the pane the card sits in, or the handle would be dragged out of reach.
    const maxWidth = pane.clientWidth - card.offsetLeft - EDGE_MARGIN;
    const maxHeight = pane.clientHeight - card.offsetTop - EDGE_MARGIN;
    const clamp = (value: number, min: number, max: number) =>
      Math.max(min, Math.min(Math.max(min, max), value));
    const next = {
      width: clamp(d.start.width + e.clientX - d.x, MIN_SIZE.width, maxWidth),
      height: clamp(
        d.start.height + e.clientY - d.y,
        MIN_SIZE.height,
        maxHeight,
      ),
    };
    rememberedSize = next;
    setSize(next);
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  return {
    cardRef,
    size,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
    },
  };
}

type Position = { left: number; top: number };
// Like the size: where the user dragged the card to is kept for the session, so
// opening another node's card doesn't snap it back to the corner.
let rememberedPosition: Position | null = null;

/**
 * Drag the card by its header. The card is absolutely positioned in the canvas
 * pane, so the position is a left/top offset in that pane, clamped so at least
 * a strip of the card stays inside it (a card dragged fully off screen could
 * never be dragged back). The header's buttons and links don't start a drag,
 * so Close still closes and a name still opens its link.
 */
function useDragPosition(cardRef: { current: HTMLElement | null }) {
  const [position, setPosition] = useState<Position | null>(rememberedPosition);
  const drag = useRef<{ x: number; y: number; start: Position } | null>(null);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const card = cardRef.current;
    if (!card || e.button !== 0) return;
    if ((e.target as HTMLElement).closest("button, a, input, select")) return;
    e.preventDefault(); // no text selection while dragging
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, start: { left: card.offsetLeft, top: card.offsetTop } };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    const card = cardRef.current;
    const pane = card?.offsetParent;
    if (!d || !card || !pane) return;
    const keep = 48; // px of the card that must stay inside the pane
    const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(Math.max(min, max), value));
    const next = {
      left: clamp(d.start.left + e.clientX - d.x, keep - card.offsetWidth, pane.clientWidth - keep),
      top: clamp(d.start.top + e.clientY - d.y, 0, pane.clientHeight - keep),
    };
    rememberedPosition = next;
    setPosition(next);
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  return {
    position,
    dragProps: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel: onPointerUp },
  };
}

/**
 * Details for the clicked node. A company shows its full name, how it relates
 * to the companies it's linked to (with the sentence and filing the link was
 * read from) and any recent events it's exposed to; a person shows their role
 * and the boards they sit on.
 */
export function NodeInfoCard({
  node,
  graph,
  boards,
  flash,
  onClose,
}: {
  node: GraphNode;
  graph: CompanyGraph;
  /** For a person: the companies whose board they sit on. */
  boards: GraphNode[];
  /** The live market move this node is pulsing for, if any. */
  flash?: Flash;
  onClose: () => void;
}) {
  const isPerson = node.kind === "person";
  const center = graph.nodes.find((n) => n.isCenter);
  // Relationship to the searched ticker first — that's the one being asked
  // about, and it's the one named beside the ticker in the header.
  const relations = isPerson
    ? []
    : relationsOf(graph, node.id).sort(
        (a, b) =>
          Number(b.other === center?.id) - Number(a.other === center?.id),
      );
  const toCenter = node.isCenter
    ? undefined
    : relations.find((r) => r.other === center?.id);
  const subtitle = isPerson
    ? (node.role ?? "Director")
    : node.isCenter
      ? `${node.label} · searched company`
      : toCenter
        ? `${node.label} · ${RELATIONSHIP_LABELS[toCenter.role]} of ${toCenter.other}`
        : node.label;
  const events = isPerson ? [] : highlightsFor(graph.highlights ?? [], node.id);
  const title = isPerson ? node.label : (node.name ?? node.label);
  // News about this company together with the one it's linked to: the searched
  // ticker when there's a direct link, otherwise its first linked company.
  const newsPartner =
    isPerson || node.isCenter ? undefined : (toCenter ?? relations[0])?.other;
  const { cardRef, size, handleProps } = useCornerResize();
  const { position, dragProps } = useDragPosition(cardRef);

  return (
    // Starts top-left of the canvas pane (which already begins past the projects
    // rail); after a drag it sits wherever it was dropped.
    <section
      ref={cardRef}
      aria-label={`${title} details`}
      className={`absolute z-10 flex flex-col overflow-hidden shadow-lg ${position ? "" : "left-6 top-24"} ${size ? "" : "max-h-[45%] w-80"}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-panel)",
        ...(position ?? {}),
        ...(size ?? {}),
      }}
    >
      {/* The scrolling part is a child, so the resize handle stays in the corner while the content scrolls. */}
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
        {/* The header is the drag handle: grab the name to move the card. */}
        <div
          {...dragProps}
          className="flex items-start justify-between gap-2"
          style={{ cursor: "grab", touchAction: "none" }}
          title="Drag to move"
        >
          <div className="min-w-0">
            <h3
              className="text-sm font-semibold"
              style={{ color: "var(--text-primary)" }}
            >
              {title}
            </h3>
            <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              {subtitle}
            </p>
            {node.industry && (
              <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                Industry: {node.industry}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="shrink-0 rounded-md px-2 py-1 text-xs"
            style={{ color: "var(--text-secondary)" }}
          >
            Close
          </button>
        </div>

        {!isPerson && (
          <MarketMoves key={node.id} symbol={node.id} flash={flash} />
        )}

        {isPerson && boards.length > 0 && (
          <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
            Sits on the board of {boards.map((b) => b.label).join(", ")}
            {boards.length > 1 && " — a board interlock between them"}.
          </p>
        )}

        {relations.map((relation) => {
          const { edge, other, role } = relation;
          return (
            <div
              key={`${edge.source}-${edge.target}-${edge.relationship}`}
              className="flex flex-col gap-1"
            >
              {/* The header already names the relationship to the searched ticker. */}
              {relation !== toCenter && (
                <p
                  className="flex items-center gap-2 text-xs font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{
                      background: RELATIONSHIP_COLORS[edge.relationship],
                    }}
                  />
                  {/* From the searched company's own card, name the other company's role instead. */}
                  {node.isCenter
                    ? `${other} is its ${RELATIONSHIP_LABELS[reversed(role)].toLowerCase()}`
                    : `${RELATIONSHIP_LABELS[role]} of ${other}`}
                </p>
              )}
              {edge.summary && (
                <p
                  className="text-xs"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {edge.summary}
                </p>
              )}
              {edge.evidenceUrl && (
                <a
                  href={edge.evidenceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="self-start text-xs underline"
                  style={{ color: "var(--accent)" }}
                >
                  {edge.evidenceUrl.includes("browse-edgar?SIC=")
                    ? "SEC industry list"
                    : "Source filing (SEC)"}
                </a>
              )}
            </div>
          );
        })}

        {!isPerson && relations.length === 0 && (
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            No stated relationships.
          </p>
        )}

        {events.map((h, i) => (
          <article
            key={`${h.source_url}-${i}`}
            className="flex flex-col gap-1 border-l-2 pl-3"
            style={{ borderColor: DIRECTION_COLOR[h.direction] }}
          >
            <p
              className="text-[10px] font-semibold uppercase tracking-wider"
              style={{ color: DIRECTION_COLOR[h.direction] }}
            >
              {DIRECTION_LABEL[h.direction]}
            </p>
            <p
              className="text-xs font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              {EVENT_LABEL[h.event_type] ?? h.event_type}
              <span
                className="font-normal"
                style={{ color: "var(--text-tertiary)" }}
              >
                {" "}
                · {formatTime(h.event_time)}
              </span>
            </p>
            <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
              {h.reason}
            </p>
            {h.price_change_pct !== null && (
              <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Price change since the event:{" "}
                {h.price_change_pct > 0 ? "+" : ""}
                {h.price_change_pct.toFixed(1)}%
              </p>
            )}
            <a
              href={h.source_url}
              target="_blank"
              rel="noreferrer"
              className="self-start text-xs underline"
              style={{ color: "var(--accent)" }}
            >
              Source
            </a>
          </article>
        ))}

        {newsPartner && (
          <PairNewsSection
            key={`${newsPartner}-${node.id}`}
            ticker={newsPartner}
            other={node.id}
          />
        )}
      </div>
      <div
        {...handleProps}
        role="separator"
        aria-label="Drag to resize"
        title="Drag to resize"
        className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize touch-none"
        style={{
          // Two diagonal strokes, the usual resize grip.
          background:
            "linear-gradient(135deg, transparent 50%, var(--text-tertiary) 50%, var(--text-tertiary) 60%, transparent 60%, transparent 75%, var(--text-tertiary) 75%, var(--text-tertiary) 85%, transparent 85%)",
        }}
      />
    </section>
  );
}
