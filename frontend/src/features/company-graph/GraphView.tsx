import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d";
import type {
  CompanyGraphResponse,
  HighlightDirection,
  RelationshipType,
} from "../../types/graph";
import {
  CENTER_COLOR,
  DIRECTION_COLOR,
  LINK_COLOR,
  NODE_COLOR,
  RELATIONSHIP_LABEL,
} from "./labels";
import { latestHighlightBySymbol } from "./highlights";

interface GNode {
  id: string;
  name: string;
  role: RelationshipType | "center";
  direction?: HighlightDirection;
}

interface GLink {
  type: RelationshipType;
}

type Node = NodeObject<GNode>;
type Link = LinkObject<GNode, GLink>;

interface Props {
  data: CompanyGraphResponse;
  selected: string | null;
  onSelect: (symbol: string | null) => void;
}

/**
 * Force-directed graph: the searched company is pinned in the middle, linked
 * companies sit around it, and each edge is labeled with the link type.
 * Node objects are reused across polls so existing nodes keep their position
 * and only new ones animate in.
 */
export default function GraphView({ data, selected, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods<Node, Link> | undefined>(undefined);
  const nodeCache = useRef(new Map<string, Node>());
  const cacheOwner = useRef<string | null>(null);
  const needsFit = useRef(true);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      setWidth(Math.floor(entries[0].contentRect.width));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const center = data.company.symbol;

  const graphData = useMemo(() => {
    if (cacheOwner.current !== center) {
      nodeCache.current = new Map();
      cacheOwner.current = center;
    }
    const cache = nodeCache.current;
    const latest = latestHighlightBySymbol(data.highlights);

    const upsert = (id: string, name: string, role: GNode["role"]): Node => {
      const node = cache.get(id) ?? ({ id } as Node);
      node.name = name;
      node.role = role;
      node.direction = latest.get(id)?.direction;
      if (role === "center") {
        node.fx = 0;
        node.fy = 0;
      }
      cache.set(id, node);
      return node;
    };

    const nodes: Node[] = [upsert(center, data.company.name, "center")];
    const seen = new Set([center]);
    for (const n of data.nodes) {
      if (seen.has(n.symbol)) continue;
      seen.add(n.symbol);
      nodes.push(upsert(n.symbol, n.name, n.type));
    }
    const links: Link[] = data.links
      .filter((l) => seen.has(l.source) && seen.has(l.target))
      .map((l) => ({ source: l.source, target: l.target, type: l.type }));
    needsFit.current = true;
    return { nodes, links };
  }, [center, data]);

  const height = width > 0 && width < 640 ? 380 : 540;

  return (
    <div ref={containerRef} className="w-full overflow-hidden rounded-lg border border-slate-200 bg-white">
      {width > 0 && (
        <ForceGraph2D<GNode, GLink>
          ref={graphRef}
          width={width}
          height={height}
          graphData={graphData}
          cooldownTicks={120}
          nodeRelSize={6}
          nodeLabel={(n) => `${n.name} (${n.id})`}
          linkColor={() => LINK_COLOR}
          linkWidth={1.5}
          linkLineDash={(l) => (l.type === "sector_peer" ? [4, 3] : null)}
          onNodeClick={(n) => onSelect(n.id === selected ? null : (n.id as string))}
          onBackgroundClick={() => onSelect(null)}
          onEngineStop={() => {
            if (needsFit.current) {
              needsFit.current = false;
              graphRef.current?.zoomToFit(400, 40);
            }
          }}
          nodeCanvasObject={(n, ctx, scale) => drawNode(n, ctx, scale, n.id === selected)}
          nodePointerAreaPaint={(n, color, ctx) => {
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(n.x ?? 0, n.y ?? 0, 12, 0, 2 * Math.PI);
            ctx.fill();
          }}
          linkCanvasObjectMode={() => "after"}
          linkCanvasObject={drawLinkLabel}
        />
      )}
    </div>
  );
}

function drawNode(n: Node, ctx: CanvasRenderingContext2D, scale: number, isSelected: boolean) {
  const x = n.x ?? 0;
  const y = n.y ?? 0;
  const isCenter = n.role === "center";
  const r = isCenter ? 9 : 6;
  const fill = isCenter ? CENTER_COLOR : n.direction ? DIRECTION_COLOR[n.direction] : NODE_COLOR;

  if (n.direction) {
    ctx.beginPath();
    ctx.arc(x, y, r + 4, 0, 2 * Math.PI);
    ctx.fillStyle = `${DIRECTION_COLOR[n.direction]}33`;
    ctx.fill();
  }
  ctx.beginPath();
  ctx.arc(x, y, r, 0, 2 * Math.PI);
  ctx.fillStyle = fill;
  ctx.fill();
  if (isSelected) {
    ctx.lineWidth = 2.5 / scale;
    ctx.strokeStyle = CENTER_COLOR;
    ctx.stroke();
  }

  const fontSize = Math.max(11 / scale, 3);
  ctx.font = `${isCenter ? "600 " : ""}${fontSize}px system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.fillStyle = "#0f172a";
  ctx.fillText(n.id, x, y + r + 2);
}

function drawLinkLabel(l: Link, ctx: CanvasRenderingContext2D, scale: number) {
  const s = l.source as Node;
  const t = l.target as Node;
  if (typeof s !== "object" || typeof t !== "object") return;
  const x = ((s.x ?? 0) + (t.x ?? 0)) / 2;
  const y = ((s.y ?? 0) + (t.y ?? 0)) / 2;
  const label = RELATIONSHIP_LABEL[l.type];
  const fontSize = Math.max(9 / scale, 2.5);
  ctx.font = `${fontSize}px system-ui, sans-serif`;
  const w = ctx.measureText(label).width;
  ctx.fillStyle = "rgba(255,255,255,0.85)";
  ctx.fillRect(x - w / 2 - 1, y - fontSize / 2 - 1, w + 2, fontSize + 2);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = "#64748b";
  ctx.fillText(label, x, y);
}
