import { useEffect, useState } from "react";
import {
  ALERTS_POLL_MS,
  fetchCompanyAlerts,
  fetchRecentAlerts,
} from "../../../lib/api/alerts";
import type { CompanyAlert, RecentAlert } from "../../../types/alert";

export type AlertsStatus = "loading" | "ready" | "unavailable";

/**
 * Live alerts, polled. Polling (not a WebSocket) because the backend exposes a
 * plain GET and alerts arrive at most a few a minute; a 15s poll is one cheap
 * indexed query, and it keeps working through proxies and the dev server as is.
 * Pauses while the tab is hidden so a forgotten window doesn't hammer Neon.
 */
export function useRecentAlerts(minutes = 60, enabled = true) {
  const [alerts, setAlerts] = useState<RecentAlert[]>([]);
  const [status, setStatus] = useState<AlertsStatus>("loading");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function tick() {
      if (document.visibilityState !== "hidden") {
        try {
          const found = await fetchRecentAlerts(minutes);
          if (cancelled) return;
          setAlerts(found);
          setStatus("ready");
        } catch {
          if (cancelled) return;
          setStatus("unavailable");
        }
      }
      timer = setTimeout(tick, ALERTS_POLL_MS);
    }

    // Coming back to the tab refreshes at once, replacing the pending timer so
    // there is only ever one poll chain.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      if (timer) clearTimeout(timer);
      void tick();
    };
    void tick();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [minutes, enabled]);

  return { alerts, status };
}

/** Alerts that affected one company; refetches when the ticker changes, polls at the same rate. */
export function useCompanyAlerts(symbol: string | null, minutes = 24 * 60) {
  const [alerts, setAlerts] = useState<CompanyAlert[]>([]);
  const [status, setStatus] = useState<AlertsStatus>("loading");

  useEffect(() => {
    if (!symbol) {
      setAlerts([]);
      return;
    }
    let cancelled = false;
    setStatus("loading");
    const load = () =>
      fetchCompanyAlerts(symbol, minutes).then(
        (found) => {
          if (cancelled) return;
          setAlerts(found);
          setStatus("ready");
        },
        () => {
          if (!cancelled) setStatus("unavailable");
        },
      );
    load();
    const interval = setInterval(load, ALERTS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol, minutes]);

  return { alerts, status };
}
