import type { CompanyGraphResponse } from "../../types/graph";
import { highlightsFor } from "./highlights";
import { DIRECTION_COLOR, DIRECTION_LABEL, EVENT_LABEL, EXPOSED_LABEL, RELATIONSHIP_LABEL } from "./labels";
import PairNews from "./PairNews";

interface Props {
  data: CompanyGraphResponse;
  symbol: string;
  onClose: () => void;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatPct(pct: number): string {
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/** Details for a clicked node: how it is linked, and the events it is exposed to. */
export default function NodeCard({ data, symbol, onClose }: Props) {
  const isCenter = symbol === data.company.symbol;
  const node = data.nodes.find((n) => n.symbol === symbol);
  const name = isCenter ? data.company.name : node?.name ?? symbol;
  const links = data.links.filter((l) => l.target === symbol || l.source === symbol);
  const events = highlightsFor(data.highlights, symbol);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm" aria-label={`${name} details`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold text-slate-900">{name}</h2>
          <p className="text-sm text-slate-500">
            {symbol}
            {node && !isCenter && <> · {RELATIONSHIP_LABEL[node.type]} of {data.company.symbol}</>}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="shrink-0 rounded px-2 py-1 text-sm text-slate-500 hover:bg-slate-100"
          aria-label="Close details"
        >
          Close
        </button>
      </div>

      {!isCenter && links.length > 0 && (
        <div className="mt-3 space-y-2">
          {links.map((l) => (
            <p key={`${l.source}-${l.target}-${l.type}`} className="text-sm text-slate-700">
              {l.summary}{" "}
              <a href={l.evidence_url} target="_blank" rel="noreferrer" className="text-slate-900 underline">
                Filing
              </a>
            </p>
          ))}
        </div>
      )}

      {events.length > 0 ? (
        <div className="mt-4 space-y-3">
          {events.map((h, i) => (
            <article
              key={`${h.source_url}-${i}`}
              className="rounded-md border-l-4 bg-slate-50 p-3"
              style={{ borderColor: DIRECTION_COLOR[h.direction] }}
            >
              <p className="text-xs font-semibold uppercase tracking-wide" style={{ color: DIRECTION_COLOR[h.direction] }}>
                {EXPOSED_LABEL} · {DIRECTION_LABEL[h.direction]}
              </p>
              <p className="mt-1 text-sm font-medium text-slate-900">
                {EVENT_LABEL[h.event_type] ?? h.event_type}
                <span className="font-normal text-slate-500"> · {formatTime(h.event_time)}</span>
              </p>
              <p className="mt-1 text-sm text-slate-700">{h.reason}</p>
              {h.price_change_pct !== null && (
                <p className="mt-1 text-sm text-slate-700">
                  Price change since the event: <span className="font-medium">{formatPct(h.price_change_pct)}</span>
                </p>
              )}
              <a href={h.source_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-sm text-slate-900 underline">
                Source
              </a>
            </article>
          ))}
        </div>
      ) : (
        !isCenter && <p className="mt-4 text-sm text-slate-500">No recent events for this company.</p>
      )}

      {!isCenter && node && (
        <PairNews ticker={data.company.symbol} tickerName={data.company.name} other={symbol} otherName={name} />
      )}
    </section>
  );
}
