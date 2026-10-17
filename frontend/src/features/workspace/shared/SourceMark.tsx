import type { MarketSource } from "../../../types/predictionMarket";

// Placeholder lettermarks in each venue's brand color. Swap SourceMark's
// contents for the official logo assets from Kalshi's and Polymarket's brand
// kits before shipping.
const SOURCE_MARKS: Record<
  MarketSource,
  { letter: string; bg: string; fg: string; name: string }
> = {
  kalshi: { letter: "K", bg: "#28CC95", fg: "#04261b", name: "Kalshi" },
  polymarket: { letter: "P", bg: "#2E5CFF", fg: "#ffffff", name: "Polymarket" },
};


export function SourceMark({ source }: { source: MarketSource }) {
  const { letter, bg, fg, name } = SOURCE_MARKS[source];
  return (
    <span
      role="img"
      aria-label={name}
      title={name}
      className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-[11px] font-bold"
      style={{ background: bg, color: fg }}
    >
      {letter}
    </span>
  );
}
