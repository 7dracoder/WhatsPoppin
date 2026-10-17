import type { Alert, AlertCompany, CompanyAlert, RecentAlert } from "../../types/alert";
import { get } from "../apiClient";

/** Poll this often: the detector writes alerts in bursts, and the impact worker follows within seconds. */
export const ALERTS_POLL_MS = 15_000;

/** The backend speaks snake_case; the app's types are camelCase like the rest of src/types. */
interface AlertWire {
  id: number;
  created_at: string;
  source: string;
  market_id: string;
  title: string | null;
  event_title: string | null;
  url: string | null;
  direction: "yes_up" | "yes_down";
  summary: string;
  score: number;
}

interface RecentAlertWire extends AlertWire {
  companies: AlertCompany[];
}

interface CompanyAlertWire extends AlertWire {
  tier: 1 | 2 | 3;
  matched: string[];
  relevance: number | null;
}

function toAlert(w: AlertWire): Alert {
  return {
    id: w.id,
    createdAt: w.created_at,
    source: w.source,
    marketId: w.market_id,
    title: w.title,
    eventTitle: w.event_title,
    url: w.url,
    direction: w.direction,
    summary: w.summary,
    score: w.score,
  };
}

/** Alerts from the last `minutes`, newest first, each with the companies it may affect. */
export async function fetchRecentAlerts(minutes = 60, limit = 50): Promise<RecentAlert[]> {
  const rows = await get<RecentAlertWire[]>(`/alerts/recent?minutes=${minutes}&limit=${limit}`);
  return rows.map((w) => ({ ...toAlert(w), companies: w.companies }));
}

/** Alerts that affected one company in the last `minutes`, newest first. */
export async function fetchCompanyAlerts(symbol: string, minutes = 24 * 60, limit = 20): Promise<CompanyAlert[]> {
  const path = `/entities/${encodeURIComponent(symbol)}/alerts?minutes=${minutes}&limit=${limit}`;
  const rows = await get<CompanyAlertWire[]>(path);
  return rows.map((w) => ({ ...toAlert(w), tier: w.tier, matched: w.matched, relevance: w.relevance }));
}
