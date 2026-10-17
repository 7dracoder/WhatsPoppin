import type { MarketSource } from "../../../types/predictionMarket";
import kalshiLogo from "../../../assets/logos/kalshi.png";
import polymarketLogo from "../../../assets/logos/polymarket.png";

// The venues' own app icons (Kalshi's green "K", Polymarket's blue mark), both square and
// full-bleed at 128x128, so one frame renders them at exactly the same size everywhere.
const SOURCE_MARKS: Record<MarketSource, { src: string; name: string }> = {
  kalshi: { src: kalshiLogo, name: "Kalshi" },
  polymarket: { src: polymarketLogo, name: "Polymarket" },
};

export function SourceMark({ source }: { source: MarketSource }) {
  const { src, name } = SOURCE_MARKS[source];
  return (
    <img
      src={src}
      alt={name}
      title={name}
      width={20}
      height={20}
      draggable={false}
      className="block h-5 w-5 shrink-0 rounded-md object-cover"
    />
  );
}
