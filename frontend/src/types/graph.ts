/**
 * Types for the Company Graph API (GET /graph/{ticker}, GET /companies/search).
 * Shapes follow the shared contract in new_specs/company-graph-tasks.md.
 */

export type GraphStatus = "running" | "done" | "error";

/** The related company's role relative to the searched company. */
export type RelationshipType =
  | "supplier"
  | "customer"
  | "partner"
  | "competitor"
  | "sector_peer";

export type HighlightDirection = "may_benefit" | "may_face_pressure";

export type EventType =
  | "product_launch"
  | "contract"
  | "earnings_surprise"
  | "recall"
  | "acquisition"
  | "odds_move";

export interface CompanyRef {
  symbol: string;
  name: string;
}

/** The searched company in a graph response. */
export interface GraphCompany extends CompanyRef {
  /** SEC's industry (SIC) description; null when SEC lists none for the company. */
  industry?: string | null;
}

export interface GraphNode {
  symbol: string;
  name: string;
  type: RelationshipType;
  industry?: string | null;
}

export interface GraphLink {
  source: string;
  target: string;
  type: RelationshipType;
  summary: string;
  evidence_url: string;
}

export interface GraphHighlight {
  target: string;
  direction: HighlightDirection;
  event_type: EventType;
  reason: string;
  source_url: string;
  event_time: string;
  price_change_pct: number | null;
}

export interface CompanyGraphResponse {
  company: GraphCompany;
  status: GraphStatus;
  /** The link run alone. "done" here while `status` is "running" means only the news run is left. */
  links_status?: GraphStatus | null;
  nodes: GraphNode[];
  links: GraphLink[];
  highlights: GraphHighlight[];
}

/** One director from GET /graph/{ticker}/board (mirrors schemas.BoardMemberOut). */
export interface BoardMember {
  /** "cik-" + the person's SEC id — the same on every board they sit on. */
  id: string;
  name: string;
  role: string;
  evidence_url: string;
  filed_at: string;
}

export interface BoardResponse {
  company: CompanyRef;
  status: GraphStatus;
  members: BoardMember[];
}

export type CompanySearchResponse = CompanyRef[];

/** One news article or X post about two linked companies together. */
export interface PairNewsItem {
  source: "news" | "x";
  title: string;
  url: string;
  published_at: string;
  /** The outlet for news, the @handle for X. */
  by: string | null;
}

/** GET /graph/{ticker}/news/{other}. */
export interface PairNewsResponse {
  company: CompanyRef;
  other: CompanyRef;
  items: PairNewsItem[];
  /** Sources that could not be searched this time: "news", "x". */
  failed: string[];
}
