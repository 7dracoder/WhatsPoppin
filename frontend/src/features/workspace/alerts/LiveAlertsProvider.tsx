import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { AlertDirection, RecentAlert } from "../../../types/alert";
import type { PredictionMarket } from "../../../types/predictionMarket";
import { useRecentAlerts, type AlertsStatus } from "./useAlerts";

/** Alerts newer than this feed the sidebar's live list, the bell, and the node flash. */
export const LIVE_WINDOW_MINUTES = 60;

/**
 * The detector's score adds up to ~3 for a price move (|z|/3, capped), ~3 for a
 * volume burst (ratio/5, capped), 1 for a whale order and up to 1 for order-book
 * imbalance (alert_detector/detector.py). 3 means one signal maxed out or two
 * fired together: the "large movement" bar for the live list and the flash.
 */
export const LARGE_MOVE_SCORE = 3;

export interface Flash {
  direction: AlertDirection;
  /** The alert behind the flash, for tooltips and the node card. */
  alert: RecentAlert;
}

interface LiveAlerts {
  alerts: RecentAlert[];
  status: AlertsStatus;
  /** Alerts at or above LARGE_MOVE_SCORE, newest first. */
  large: RecentAlert[];
  /** Companies to flash on the graph, by symbol: the newest large move that touched each. */
  flashBySymbol: Map<string, Flash>;
  /**
   * The recommended-markets list reports when one of its markets has a large
   * move, so the searched company flashes too (the move shows up in both the
   * recommendations and the live list, and the node should agree with both).
   */
  reportCenterMove: (
    symbol: string | null,
    market: PredictionMarket | null,
  ) => void;
}

const LiveAlertsContext = createContext<LiveAlerts | null>(null);

export function isLargeMove(alert: RecentAlert): boolean {
  return alert.score >= LARGE_MOVE_SCORE;
}

/** One poll of /alerts/recent shared by the bell, the sidebar and the graph canvas. */
export function LiveAlertsProvider({ children }: { children: ReactNode }) {
  const { alerts, status } = useRecentAlerts(LIVE_WINDOW_MINUTES);
  const [centerMove, setCenterMove] = useState<{
    symbol: string;
    market: PredictionMarket;
  } | null>(null);

  const reportCenterMove = useCallback(
    (symbol: string | null, market: PredictionMarket | null) => {
      setCenterMove(symbol && market ? { symbol, market } : null);
    },
    [],
  );

  const value = useMemo<LiveAlerts>(() => {
    const large = alerts.filter(isLargeMove); // already newest first from the API
    const flashBySymbol = new Map<string, Flash>();
    // Walk oldest -> newest so the newest alert for a company wins.
    for (const alert of [...large].reverse()) {
      for (const company of alert.companies)
        flashBySymbol.set(company.symbol, {
          direction: alert.direction,
          alert,
        });
    }
    if (centerMove && !flashBySymbol.has(centerMove.symbol)) {
      // Not a detector alert, so it's built from the recommended market itself:
      // the node card names the market that moved rather than a generic notice.
      const { market } = centerMove;
      const change = market.change24h ?? 0;
      const direction: AlertDirection = change > 0 ? "yes_up" : "yes_down";
      const synthetic: RecentAlert = {
        id: -1,
        createdAt: new Date().toISOString(),
        source: market.source,
        marketId: market.id,
        title: market.title,
        eventTitle: null,
        url: market.url ?? null,
        direction,
        summary: `Yes price ${change > 0 ? "up" : "down"} ${Math.abs(change)}¢ over the last 24h`,
        score: LARGE_MOVE_SCORE,
        companies: [],
      };
      flashBySymbol.set(centerMove.symbol, { direction, alert: synthetic });
    }
    return { alerts, status, large, flashBySymbol, reportCenterMove };
  }, [alerts, status, centerMove, reportCenterMove]);

  return (
    <LiveAlertsContext.Provider value={value}>
      {children}
    </LiveAlertsContext.Provider>
  );
}

const EMPTY: LiveAlerts = {
  alerts: [],
  status: "loading",
  large: [],
  flashBySymbol: new Map(),
  reportCenterMove: () => {},
};

/** Falls back to an empty, inert value outside the provider (tests, the project page). */
export function useLiveAlerts(): LiveAlerts {
  return useContext(LiveAlertsContext) ?? EMPTY;
}
