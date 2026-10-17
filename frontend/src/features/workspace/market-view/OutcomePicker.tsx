import { useEffect, useState } from "react";
import { fetchMarketOutcomes, toMarketCard } from "../../../lib/api/predictionMarkets";
import type { MarketCard } from "../../../types/market";
import type { PredictionMarket } from "../../../types/predictionMarket";
import { useWorkspace } from "../useWorkspace";

/**
 * "Which answer?" for a plotted market: a select listing every outcome of the
 * market's event, so "What will Jensen Huang say?" can switch from the AI
 * answer to the TSM one. Choosing swaps the market in place (MARKET_SWAPPED),
 * so the line keeps its color and slot and the chart refetches. Renders
 * nothing while the outcomes load or when the event has only this one.
 */
export function OutcomePicker({ card }: { card: MarketCard }) {
  const { dispatch } = useWorkspace();
  const [outcomes, setOutcomes] = useState<PredictionMarket[]>([]);

  useEffect(() => {
    let cancelled = false;
    const asMarket: PredictionMarket = {
      id: card.marketId,
      source: "kalshi", // only the id matters for the lookup; the venue comes back with the outcomes
      title: card.title,
      category: card.category ?? "",
      closeTime: card.closeTime,
      outcome: card.outcome,
      url: card.url,
    };
    fetchMarketOutcomes(asMarket).then((found) => {
      if (!cancelled) setOutcomes(found);
    });
    return () => {
      cancelled = true;
    };
  }, [card]);

  if (outcomes.length < 2) return null;

  return (
    <select
      value={card.marketId}
      onChange={(e) => {
        const next = outcomes.find((o) => o.id === e.target.value);
        if (next) dispatch({ type: "MARKET_SWAPPED", marketId: card.marketId, market: toMarketCard(next) });
      }}
      aria-label={`Outcome of ${card.title} to plot`}
      title="Plot another outcome of this event"
      className="max-w-[12rem] truncate rounded-[calc(var(--radius-control)-2px)] px-1.5 py-0.5 text-xs"
      style={{ background: "var(--surface-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
    >
      {outcomes.map((o) => (
        <option key={o.id} value={o.id}>
          {o.outcome ?? o.question ?? o.id}
        </option>
      ))}
    </select>
  );
}
