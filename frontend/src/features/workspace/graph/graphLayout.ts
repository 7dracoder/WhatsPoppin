import type { GraphEdge, GraphNode } from "../../../types/workspaceGraph";

export interface Point {
  x: number;
  y: number;
}

export interface GraphLayout {
  /** Where each node should settle. */
  targets: Map<string, Point>;
  /** For each person, the company they're drawn around (labels sit on the side facing away from it). */
  personAnchors: Map<string, Point>;
  /** Companies whose name is drawn above the node instead of below it. */
  labelAbove: Set<string>;
}

// Spacing between neighbouring people around a company, in graph units.
const PERSON_SPACING = 8;
// Company names are drawn at a constant on-screen size, so their size in
// graph units depends on zoom. These are the values at zoom 1, used to
// reserve room for them: distance from the node's middle to the name, and
// the name's height.
const COMPANY_LABEL_OFFSET = 11;
const COMPANY_LABEL_FONT = 14;
const COMPANY_LABEL_PAD = 5;
// A board is a full circle around its company, so the ring must clear the
// company's name (drawn just above or below the node) at zoom 1.
const MIN_PERSON_RING = COMPANY_LABEL_OFFSET + COMPANY_LABEL_FONT + COMPANY_LABEL_PAD + 2;
// Room past a ring for its (zoom-gated, horizontal) initials.
const LABEL_ROOM_X = 18;
const LABEL_ROOM_Y = 10;
const GAP = 28;
const FULL_CIRCLE = Math.PI * 2;
// Most companies on one line of a tier before the rest wrap onto another
// line. Without a cap a company with a dozen suppliers, or a large industry,
// makes one row so long that fitting it on screen shrinks every name away.
const ROW_COLUMNS = 5;
// Same cap for the searched company's own line, per side of it.
const SIDE_COLUMNS = 2;

/** Ring radius that keeps `count` evenly spaced people PERSON_SPACING apart. */
function personRingRadius(count: number): number {
  return Math.max(MIN_PERSON_RING, (count * PERSON_SPACING) / FULL_CIRCLE);
}

// Name suffixes that are not a surname, so "Robyn Denholm Jr" reads "RD".
const NAME_SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv"]);

/**
 * "Robyn M Denholm" -> "RD": the first letter of the first and last name.
 * Drawn beside each person instead of the full name, which kept the rings
 * from reading as rings. A one-word name gives one letter; "" gives "".
 */
export function initials(name: string): string {
  const words = name
    .split(/\s+/)
    .map((w) => w.replace(/[.,]/g, ""))
    .filter((w) => w.length > 0);
  while (words.length > 1 && NAME_SUFFIXES.has(words[words.length - 1].toLowerCase())) words.pop();
  if (words.length === 0) return "";
  const first = words[0][0];
  const last = words.length > 1 ? words[words.length - 1][0] : "";
  return (first + last).toUpperCase();
}

/**
 * Tier shift implied by an edge, read in the direction the edge points.
 * An edge `source -> target: supplier` means the target supplies the source,
 * so the target sits one tier *above* (negative y is up on the canvas).
 */
function tierShift(relationship: GraphEdge["relationship"]): number {
  if (relationship === "supplier") return -1;
  if (relationship === "customer") return 1;
  return 0; // partner / competitor / sector_peer: same tier, side by side
}

/**
 * Supply-chain layout: suppliers above the searched company, customers below,
 * competitors and peers beside it. Each tier of the pipeline is a band (a
 * supplier's supplier is two bands up): one line of evenly spaced companies,
 * or several staggered lines when the tier has more than fit on one. Line
 * height and column width grow with board size so neighbouring boards never
 * overlap.
 *
 * `seats` may be empty (boards arrive after companies); the layout is simply
 * recomputed when they land and nodes glide to their adjusted spots.
 */
export function computeGraphLayout(
  companies: GraphNode[],
  companyEdges: GraphEdge[],
  seats: { companyId: string; personId: string }[],
): GraphLayout {
  const targets = new Map<string, Point>();
  const personAnchors = new Map<string, Point>();
  const labelAbove = new Set<string>();

  const center = companies.find((c) => c.isCenter);
  if (!center) return { targets, personAnchors, labelAbove };

  // Board membership per company, and companies per person.
  const boardOf = new Map<string, string[]>();
  const seatsOf = new Map<string, string[]>();
  for (const { companyId, personId } of seats) {
    boardOf.set(companyId, [...(boardOf.get(companyId) ?? []), personId]);
    seatsOf.set(personId, [...(seatsOf.get(personId) ?? []), companyId]);
  }
  // Directors on several boards are drawn once, between those companies, so
  // only single-board directors take a slot around a company.
  const ringMembers = (companyId: string) =>
    (boardOf.get(companyId) ?? []).filter((p) => seatsOf.get(p)?.length === 1);

  const maxBoard = Math.max(0, ...companies.map((c) => ringMembers(c.id).length));
  const boardReach = maxBoard === 0 ? 0 : personRingRadius(maxBoard);
  // A ring reaches all the way round its company, so neighbours in both
  // directions need room for two rings (the names sit inside the rings).
  const colWidth = Math.max(110, 2 * (boardReach + LABEL_ROOM_X) + GAP);
  const rowHeight = Math.max(110, 2 * (boardReach + LABEL_ROOM_Y) + GAP);

  // Assign tiers (rows) by walking out from the center, BFS so each company
  // gets placed relative to its closest connection. `parent` remembers that
  // connection so rows can be ordered to keep related companies aligned.
  const row = new Map<string, number>([[center.id, 0]]);
  const parent = new Map<string, string>();
  const order: string[] = [center.id];
  for (let i = 0; i < order.length; i++) {
    const id = order[i];
    for (const e of companyEdges) {
      let next: string | undefined;
      let shift = 0;
      if (e.source === id && !row.has(e.target)) {
        next = e.target;
        shift = tierShift(e.relationship);
      } else if (e.target === id && !row.has(e.source)) {
        next = e.source;
        shift = -tierShift(e.relationship); // walking the edge backwards flips it
      }
      if (next === undefined) continue;
      row.set(next, row.get(id)! + shift);
      parent.set(next, id);
      order.push(next);
    }
  }
  // Anything not connected to the center goes on its own row at the bottom.
  const lowest = Math.max(0, ...row.values());
  for (const c of companies) {
    if (!row.has(c.id)) {
      row.set(c.id, lowest + 1);
      order.push(c.id);
    }
  }

  const x = new Map<string, number>([[center.id, 0]]);
  // Each tier is a band of one or more lines. `line` is a company's line
  // within its band: for the center tier 0 is the searched company's own line
  // and +-1, +-2 are the lines below and above it; for the other tiers it
  // counts outward from the center.
  const line = new Map<string, number>([[center.id, 0]]);

  // Center tier: competitors and peers alternate right/left of the center; a
  // peer of a peer stays on its parent's side. Each side holds SIDE_COLUMNS
  // per line, and the rest wrap onto lines below and above (0, +1, -1, ...),
  // so a large industry fans out beside the center instead of running off in
  // one long row. Wrapped lines are shifted half a column, which keeps their
  // edges to the center from running through the companies on the line before.
  const sides: { right: string[]; left: string[] } = { right: [], left: [] };
  let alternate = 0;
  for (const id of order) {
    if (id === center.id || row.get(id) !== 0) continue;
    const parentId = parent.get(id) ?? "";
    const goRight = parentId === center.id || !parentId ? alternate++ % 2 === 0 : sides.right.includes(parentId);
    (goRight ? sides.right : sides.left).push(id);
  }
  for (const [ids, sign] of [
    [sides.right, 1],
    [sides.left, -1],
  ] as const) {
    ids.forEach((id, i) => {
      const wrap = Math.floor(i / SIDE_COLUMNS);
      const column = (i % SIDE_COLUMNS) + 1 + (wrap % 2 === 1 ? 0.5 : 0);
      x.set(id, sign * column * colWidth);
      line.set(id, wrap === 0 ? 0 : (wrap % 2 === 1 ? 1 : -1) * Math.ceil(wrap / 2));
    });
  }
  const centerLines = [...line.values()];
  const linesAbove = Math.max(0, ...centerLines.map((l) => -l));
  const linesBelow = Math.max(0, ...centerLines);

  // Other tiers, nearest the center first: sort by where each company's
  // parent sits, then split the tier into lines of at most ROW_COLUMNS, each
  // spaced evenly around those parents' average x. The lines are balanced
  // (8 companies make 4 + 4, not 5 + 3) and every other one is shifted half a
  // column, so edges to the far line pass between the companies on the near one.
  const linesInTier = new Map<number, number>();
  const otherRows = [...new Set([...row.values()])].filter((r) => r !== 0).sort((a, b) => Math.abs(a) - Math.abs(b));
  for (const r of otherRows) {
    const ids = order.filter((id) => row.get(id) === r);
    const desired = ids.map((id) => x.get(parent.get(id) ?? "") ?? 0);
    const sorted = ids.map((id, i) => ({ id, want: desired[i] })).sort((a, b) => a.want - b.want);
    const middle = desired.reduce((s, v) => s + v, 0) / desired.length;
    const lines = Math.ceil(sorted.length / ROW_COLUMNS);
    const perLine = Math.ceil(sorted.length / lines);
    linesInTier.set(r, lines);
    sorted.forEach(({ id }, i) => {
      const l = i % lines; // dealt across the lines, so each line still spans the tier left to right
      const slot = Math.floor(i / lines);
      const count = Math.min(perLine, Math.ceil((sorted.length - l) / lines));
      const stagger = l % 2 === 1 ? colWidth / 2 : 0;
      x.set(id, middle + (slot - (count - 1) / 2) * colWidth + stagger);
      line.set(id, l);
    });
  }

  // Stack the bands: tiers above the center go up from the center band's top
  // line, tiers below go down from its bottom line.
  const bandStart = new Map<number, number>(); // tier -> the line index (in rows from y = 0) of its first line
  let up = linesAbove;
  let down = linesBelow;
  for (const r of otherRows) {
    const lines = linesInTier.get(r) ?? 1;
    if (r < 0) {
      bandStart.set(r, -(up + 1));
      up += lines;
    } else {
      bandStart.set(r, down + 1);
      down += lines;
    }
  }

  // Lines below the center's are mirrored: name above the node. That keeps
  // every name on the side facing the center, where most of a company's
  // edges come in, so the ring's first person can go on the far side.
  for (const c of companies) {
    const r = row.get(c.id) ?? 0;
    const l = line.get(c.id) ?? 0;
    const rows = r === 0 ? l : (bandStart.get(r) ?? 0) + (r < 0 ? -l : l);
    targets.set(c.id, { x: x.get(c.id) ?? 0, y: rows * rowHeight });
    if (rows > 0) labelAbove.add(c.id);
  }

  // People: an even circle around their company, at a radius that clears
  // the company's name. The first person goes on the side away from the
  // name, so a small board is mostly on that side and off the name.
  for (const c of companies) {
    const members = ringMembers(c.id);
    if (members.length === 0) continue;
    const origin = targets.get(c.id)!;
    const r = personRingRadius(members.length);
    const facing = labelAbove.has(c.id) ? Math.PI / 2 : -Math.PI / 2;
    const step = FULL_CIRCLE / members.length;

    members.forEach((personId, i) => {
      const angle = facing + i * step;
      targets.set(personId, { x: origin.x + Math.cos(angle) * r, y: origin.y + Math.sin(angle) * r });
      personAnchors.set(personId, origin);
    });
  }

  // Directors on two or more boards sit between those companies — but pushed
  // off to one side of the line joining them. The exact midpoint lands on top
  // of the company-to-company edge whenever the two companies are directly
  // connected, which hides both the person and the edge.
  for (const [personId, companyIds] of seatsOf) {
    if (companyIds.length < 2) continue;
    const pts = companyIds.map((id) => targets.get(id)).filter((p): p is Point => !!p);
    if (pts.length === 0) continue;
    const mid = {
      x: pts.reduce((s, p) => s + p.x, 0) / pts.length,
      y: pts.reduce((s, p) => s + p.y, 0) / pts.length,
    };
    const [a, b] = pts;
    let point = mid;
    if (b) {
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      // Perpendicular to a->b; pick whichever side points away from the center.
      let nx = -dy / len;
      let ny = dx / len;
      if (nx * mid.x + ny * mid.y < 0) {
        nx = -nx;
        ny = -ny;
      }
      const offset = Math.max(22, len * 0.18);
      point = { x: mid.x + nx * offset, y: mid.y + ny * offset };
    }
    // These directors aren't confined to a fan, so check them against every
    // company name and push any that landed on one out past its far edge.
    for (const c of companies) {
      const origin = targets.get(c.id)!;
      const side = labelAbove.has(c.id) ? -1 : 1;
      // Rough name width: ~0.6em per character at this weight.
      const halfWidth = (c.label.length * COMPANY_LABEL_FONT * 0.6) / 2 + COMPANY_LABEL_PAD;
      const farEdge = COMPANY_LABEL_OFFSET + COMPANY_LABEL_FONT + COMPANY_LABEL_PAD;
      const along = (point.y - origin.y) * side;
      if (Math.abs(point.x - origin.x) < halfWidth && along > 0 && along < farEdge) {
        point = { x: point.x, y: origin.y + side * farEdge };
      }
    }
    targets.set(personId, point);
    personAnchors.set(personId, mid);
  }

  return { targets, personAnchors, labelAbove };
}

/**
 * Re-home a company the user dragged: its layout slot becomes `to`, and its
 * board shifts by the same amount so the people follow it. Mutates the layout
 * in place — the target force reads it on every tick.
 *
 * A single-board director's anchor is the very same Point object as their
 * company's target (see above), which is how a company's board is found here
 * and why moving `origin` also moves their anchor.
 */
export function moveCompany(layout: GraphLayout, companyId: string, to: Point): void {
  const origin = layout.targets.get(companyId);
  if (!origin) return;
  const dx = to.x - origin.x;
  const dy = to.y - origin.y;
  for (const [personId, anchor] of layout.personAnchors) {
    if (anchor !== origin) continue;
    const target = layout.targets.get(personId);
    if (target) {
      target.x += dx;
      target.y += dy;
    }
  }
  origin.x = to.x;
  origin.y = to.y;
}

/**
 * A d3-compatible force that pulls each node toward its layout target.
 * Reads targets through a getter on every tick, so recomputing the layout
 * moves nodes without re-registering the force.
 */
export function targetForce(getTarget: (id: string) => Point | undefined, strength: number) {
  let nodes: { id?: string | number; x?: number; y?: number; vx?: number; vy?: number }[] = [];
  function force(alpha: number) {
    for (const n of nodes) {
      const t = getTarget(String(n.id));
      if (!t || n.x === undefined || n.y === undefined) continue;
      n.vx = (n.vx ?? 0) + (t.x - n.x) * strength * alpha;
      n.vy = (n.vy ?? 0) + (t.y - n.y) * strength * alpha;
    }
  }
  force.initialize = (ns: typeof nodes) => {
    nodes = ns;
  };
  return force;
}
