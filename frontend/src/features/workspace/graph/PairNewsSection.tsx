import { formatPairDate, PAIR_SOURCE_LABEL, usePairNews } from "../../company-graph/usePairNews";

/**
 * Recent news and X posts about two linked companies together (GET
 * /graph/{ticker}/news/{other}), in the node card's styling. The backend
 * answers only for companies linked in the stored graph.
 */
export function PairNewsSection({ ticker, other }: { ticker: string; other: string }) {
  const state = usePairNews(ticker, other);

  return (
    <div className="flex flex-col gap-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
      <p className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-tertiary)" }}>
        News about {ticker} + {other}
      </p>

      {state.kind === "loading" && (
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          Looking for recent news and posts that cover both…
        </p>
      )}

      {state.kind === "error" && (
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Could not load news: {state.message}
        </p>
      )}

      {state.kind === "done" && state.data.items.length === 0 && (
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          No recent news or posts about these two companies together.
        </p>
      )}

      {state.kind === "done" &&
        state.data.items.map((item) => (
          <article key={item.url} className="flex flex-col gap-0.5">
            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              title={item.title}
              className="line-clamp-3 text-xs hover:underline"
              style={{ color: "var(--text-primary)" }}
            >
              {item.title}
            </a>
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              {PAIR_SOURCE_LABEL[item.source] ?? item.source}
              {item.by && <> · {item.by}</>} · {formatPairDate(item.published_at)}
            </p>
          </article>
        ))}

      {state.kind === "done" && state.data.failed.length > 0 && (
        <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
          Not searched this time: {state.data.failed.map((s) => PAIR_SOURCE_LABEL[s] ?? s).join(", ")}.
        </p>
      )}
    </div>
  );
}
