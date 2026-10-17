import { formatPairDate, PAIR_SOURCE_LABEL, usePairNews } from "./usePairNews";

interface Props {
  /** The searched company. */
  ticker: string;
  tickerName: string;
  /** The linked company that was clicked. */
  other: string;
  otherName: string;
}

/** Recent news and X posts about the searched company and one linked company together. */
export default function PairNews({ ticker, tickerName, other, otherName }: Props) {
  const state = usePairNews(ticker, other);

  return (
    <div className="mt-4 border-t border-slate-200 pt-3">
      <h3 className="text-sm font-semibold text-slate-900">
        News about {tickerName} + {otherName}
      </h3>

      {state.kind === "loading" && (
        <p className="mt-2 text-sm text-slate-500">Looking for recent news and posts that cover both…</p>
      )}

      {state.kind === "error" && <p className="mt-2 text-sm text-red-700">Could not load news: {state.message}</p>}

      {state.kind === "done" && (
        <>
          {state.data.items.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">No recent news or posts about these two companies together.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {state.data.items.map((item) => (
                <li key={item.url} className="text-sm">
                  <a href={item.url} target="_blank" rel="noreferrer" className="text-slate-900 underline">
                    {item.title}
                  </a>
                  <p className="text-xs text-slate-500">
                    {PAIR_SOURCE_LABEL[item.source] ?? item.source}
                    {item.by && <> · {item.by}</>} · {formatPairDate(item.published_at)}
                  </p>
                </li>
              ))}
            </ul>
          )}
          {state.data.failed.length > 0 && (
            <p className="mt-2 text-xs text-slate-500">
              Not searched this time: {state.data.failed.map((s) => PAIR_SOURCE_LABEL[s] ?? s).join(", ")}.
            </p>
          )}
        </>
      )}
    </div>
  );
}
