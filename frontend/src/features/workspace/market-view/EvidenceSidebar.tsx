import { useEffect, useState, type FormEvent } from "react";
import {
  CheckCircle,
  FunnelSimple,
  MagnifyingGlass,
  Newspaper,
  TwitterLogo,
} from "@phosphor-icons/react";
import {
  DEFAULT_NEWS_FILTERS,
  DEFAULT_TWEET_FILTERS,
  fetchCandidateNews,
  fetchCandidateTweets,
  type NewsFilters,
  type NewsSearchField,
  type TweetFilters,
} from "../../../lib/api/evidence";
import { classifySentiments } from "../../../lib/api/sentiment";
import type { ContentItem, Sentiment } from "../../../types/content";
import type { EvidenceAnnotation } from "../../../types/project";
import { useWorkspace } from "../useWorkspace";
import { SENTIMENT_COLOR, SENTIMENT_GLYPH, SENTIMENT_LABEL } from "./sentiment";

type Kind = "tweets" | "news";

const SEARCH_FIELDS: { value: NewsSearchField; label: string }[] = [
  { value: "title", label: "Title" },
  { value: "description", label: "Description" },
  { value: "content", label: "Content" },
];

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function activeCount(kind: Kind, t: TweetFilters, n: NewsFilters): number {
  if (kind === "tweets") {
    return (
      Number(t.account.trim() !== "") +
      Number(t.days !== DEFAULT_TWEET_FILTERS.days)
    );
  }
  return (
    Number(n.searchIn.length > 0) +
    Number(n.domains.trim() !== "") +
    Number(n.sortBy !== DEFAULT_NEWS_FILTERS.sortBy)
  );
}

const inputStyle = {
  background: "var(--bg)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-control)",
  color: "var(--text-primary)",
};

/**
 * Full-height right-hand sidebar for picking evidence, laid out like the
 * prediction-market sidebar: tabs, a search box, a filter drawer, then a
 * scrolling list of thin cards. The filters are the ones the backend libraries
 * accept (see TweetFilters / NewsFilters in lib/api/evidence.ts). Searching
 * runs on submit, not per keystroke, because each search is a call to a
 * rate-limited external API. Clicking a card adds it to state.evidence.
 */
export function EvidenceSidebar() {
  const { state, dispatch } = useWorkspace();
  const ticker = state.ticker ?? "";

  const [kind, setKind] = useState<Kind>("tweets");
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Drafts are what the inputs show; applied is what the last submit searched
  // with, so typing never triggers a fetch.
  const [tweetDraft, setTweetDraft] = useState(DEFAULT_TWEET_FILTERS);
  const [newsDraft, setNewsDraft] = useState(DEFAULT_NEWS_FILTERS);
  const [tweetApplied, setTweetApplied] = useState(DEFAULT_TWEET_FILTERS);
  const [newsApplied, setNewsApplied] = useState(DEFAULT_NEWS_FILTERS);

  const [items, setItems] = useState<Record<Kind, ContentItem[]>>({
    tweets: [],
    news: [],
  });
  const [status, setStatus] = useState<
    Record<Kind, "loading" | "done" | "error">
  >({
    tweets: "loading",
    news: "loading",
  });

  // Jev sentiment per item id, filled in after each list loads. Cards render
  // right away and pick up their color when this arrives.
  const [sentiments, setSentiments] = useState<Map<string, Sentiment>>(
    new Map(),
  );
  useEffect(() => {
    const all = [...items.tweets, ...items.news];
    if (all.length === 0) return;
    let cancelled = false;
    classifySentiments(all).then((found) => {
      if (!cancelled) setSentiments(found);
    });
    return () => {
      cancelled = true;
    };
  }, [items]);

  // One effect per tab so switching tabs never refetches the other one, and a
  // superseded request can't overwrite a newer one.
  // Both lists wait for market view: the sidebar is mounted (hidden) during the graph stages,
  // and each fetch is a metered X / NewsAPI call followed by a Jev sentiment pass.
  const inMarketView = state.stage === "market-view";
  useEffect(() => {
    if (!inMarketView) return;
    let cancelled = false;
    setStatus((s) => ({ ...s, tweets: "loading" }));
    fetchCandidateTweets(ticker, undefined, tweetApplied)
      .then((found) => {
        if (cancelled) return;
        setItems((p) => ({ ...p, tweets: found }));
        setStatus((s) => ({ ...s, tweets: "done" }));
      })
      .catch(() => !cancelled && setStatus((s) => ({ ...s, tweets: "error" })));
    return () => {
      cancelled = true;
    };
  }, [ticker, tweetApplied, inMarketView]);

  useEffect(() => {
    if (!inMarketView) return;
    let cancelled = false;
    setStatus((s) => ({ ...s, news: "loading" }));
    fetchCandidateNews(ticker, undefined, newsApplied)
      .then((found) => {
        if (cancelled) return;
        setItems((p) => ({ ...p, news: found }));
        setStatus((s) => ({ ...s, news: "done" }));
      })
      .catch(() => !cancelled && setStatus((s) => ({ ...s, news: "error" })));
    return () => {
      cancelled = true;
    };
  }, [ticker, newsApplied, inMarketView]);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (kind === "tweets") setTweetApplied(tweetDraft);
    else setNewsApplied(newsDraft);
  }

  function reset() {
    if (kind === "tweets") {
      setTweetDraft(DEFAULT_TWEET_FILTERS);
      setTweetApplied(DEFAULT_TWEET_FILTERS);
    } else {
      setNewsDraft(DEFAULT_NEWS_FILTERS);
      setNewsApplied(DEFAULT_NEWS_FILTERS);
    }
  }

  function toggle(item: ContentItem) {
    if (addedIds.has(item.id)) {
      dispatch({ type: "EVIDENCE_REMOVED", itemId: item.id });
      return;
    }
    const annotation: EvidenceAnnotation = {
      id: `evidence-${item.id}-${Date.now()}`,
      item,
      timestamp: item.published_at,
      sentiment: sentiments.get(item.id),
    };
    dispatch({ type: "EVIDENCE_ADDED", annotation });
  }

  const addedIds = new Set(state.evidence.map((e) => e.item.id));
  const list = items[kind];
  const loading = status[kind] === "loading";
  const filterCount = activeCount(kind, tweetApplied, newsApplied);
  const isTweets = kind === "tweets";
  const query = isTweets ? tweetDraft.query : newsDraft.query;

  return (
    <section
      aria-label="Add evidence"
      className="flex h-full min-h-0 flex-col gap-2 px-4 py-4"
      style={{
        background: "var(--surface)",
        borderLeft: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-2">
        <div
          role="tablist"
          aria-label="Evidence type"
          className="flex gap-1 rounded-[var(--radius-control)] p-0.5"
          style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
        >
          {(["tweets", "news"] as const).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={kind === k}
              onClick={() => setKind(k)}
              className="flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 text-xs font-medium"
              style={{
                background: kind === k ? "var(--surface)" : "transparent",
                color:
                  kind === k ? "var(--text-primary)" : "var(--text-tertiary)",
                boxShadow: kind === k ? "0 0 0 1px var(--border)" : "none",
              }}
            >
              {k === "tweets" ? (
                <TwitterLogo size={13} weight="bold" />
              ) : (
                <Newspaper size={13} weight="bold" />
              )}
              {k === "tweets" ? "Tweets" : "News"}
            </button>
          ))}
        </div>
        {state.evidence.length > 0 && (
          <span
            className="ml-auto rounded-full px-2 py-0.5 text-[10px] font-semibold"
            style={{
              background: "var(--accent)",
              color: "var(--accent-contrast)",
            }}
          >
            {state.evidence.length} added
          </span>
        )}
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-2">
        <div
          className="flex items-center gap-2 px-2.5 py-1.5"
          style={inputStyle}
        >
          <MagnifyingGlass
            size={14}
            style={{ color: "var(--text-tertiary)" }}
            aria-hidden="true"
          />
          <input
            type="text"
            value={query}
            onChange={(e) =>
              isTweets
                ? setTweetDraft({ ...tweetDraft, query: e.target.value })
                : setNewsDraft({ ...newsDraft, query: e.target.value })
            }
            placeholder={
              isTweets
                ? `"phrase", #hashtag, $${ticker || "TICKER"}…`
                : "Keywords or exact phrase…"
            }
            aria-label={isTweets ? "Search tweets" : "Search news"}
            className="w-full bg-transparent text-xs outline-none"
          />
          <button
            type="button"
            onClick={() => setFiltersOpen((o) => !o)}
            aria-expanded={filtersOpen}
            aria-label="Filters"
            className="flex shrink-0 items-center gap-1 text-[10px] font-medium"
            style={{
              color: filterCount > 0 ? "var(--accent)" : "var(--text-tertiary)",
            }}
          >
            <FunnelSimple size={14} />
            {filterCount > 0 && filterCount}
          </button>
        </div>

        {filtersOpen && (
          <div
            className="flex flex-col gap-2 p-2.5 text-xs"
            style={{ ...inputStyle, background: "var(--bg)" }}
          >
            {isTweets ? (
              <>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--text-tertiary)" }}>
                    From account
                  </span>
                  <input
                    type="text"
                    value={tweetDraft.account}
                    onChange={(e) =>
                      setTweetDraft({ ...tweetDraft, account: e.target.value })
                    }
                    placeholder="@handle"
                    className="px-2 py-1 outline-none"
                    style={inputStyle}
                  />
                  {tweetDraft.account.trim() && (
                    <span
                      className="text-[10px]"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      Looks up this account's posts; the search above is
                      ignored.
                    </span>
                  )}
                </label>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--text-tertiary)" }}>
                    Time window
                  </span>
                  <select
                    value={tweetDraft.days}
                    onChange={(e) =>
                      setTweetDraft({
                        ...tweetDraft,
                        days: Number(e.target.value),
                      })
                    }
                    className="px-2 py-1 outline-none"
                    style={inputStyle}
                  >
                    {[7, 14, 30].map((d) => (
                      <option key={d} value={d}>
                        Last {d} days
                      </option>
                    ))}
                  </select>
                </label>
              </>
            ) : (
              <>
                <fieldset className="flex flex-col gap-1">
                  <legend style={{ color: "var(--text-tertiary)" }}>
                    Search in
                  </legend>
                  <div className="flex flex-wrap gap-1.5">
                    {SEARCH_FIELDS.map(({ value, label }) => {
                      const on = newsDraft.searchIn.includes(value);
                      return (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={on}
                          onClick={() =>
                            setNewsDraft({
                              ...newsDraft,
                              searchIn: on
                                ? newsDraft.searchIn.filter((f) => f !== value)
                                : [...newsDraft.searchIn, value],
                            })
                          }
                          className="rounded-full px-2 py-0.5"
                          style={{
                            border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                            color: on
                              ? "var(--accent)"
                              : "var(--text-secondary)",
                          }}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--text-tertiary)" }}>Domains</span>
                  <input
                    type="text"
                    value={newsDraft.domains}
                    onChange={(e) =>
                      setNewsDraft({ ...newsDraft, domains: e.target.value })
                    }
                    placeholder="example.org, -blog.example.com"
                    className="px-2 py-1 outline-none"
                    style={inputStyle}
                  />
                  <span
                    className="text-[10px]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    Comma-separated. Prefix with - to exclude.
                  </span>
                </label>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--text-tertiary)" }}>Sort by</span>
                  <select
                    value={newsDraft.sortBy}
                    onChange={(e) =>
                      setNewsDraft({
                        ...newsDraft,
                        sortBy: e.target.value as NewsFilters["sortBy"],
                      })
                    }
                    className="px-2 py-1 outline-none"
                    style={inputStyle}
                  >
                    <option value="publishedAt">Newest</option>
                    <option value="relevancy">Relevancy</option>
                  </select>
                </label>
              </>
            )}
            <div className="flex items-center justify-between pt-1">
              <button
                type="button"
                onClick={reset}
                style={{ color: "var(--text-tertiary)" }}
              >
                Reset
              </button>
              <button
                type="submit"
                className="rounded-[var(--radius-pill)] px-3 py-1 font-semibold"
                style={{
                  background: "var(--accent)",
                  color: "var(--accent-contrast)",
                }}
              >
                Apply
              </button>
            </div>
          </div>
        )}
      </form>

      <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
        {loading
          ? "Searching…"
          : `${list.length} ${isTweets ? "tweet" : "article"}${list.length === 1 ? "" : "s"}${ticker ? ` for ${ticker}` : ""}`}
        {!isTweets && " · older than 30 days"}
      </p>

      {loading ? (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] content-start gap-1.5" aria-label="Loading">
          {[0, 1, 2, 3].map((i) => (
            <li
              key={i}
              className="h-16 animate-pulse"
              style={{
                background: "var(--border)",
                borderRadius: "var(--radius-control)",
              }}
            />
          ))}
        </ul>
      ) : status[kind] === "error" ? (
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          Couldn't load {kind}. Change the search to try again.
        </p>
      ) : list.length === 0 ? (
        <p
          className="px-3 py-3 text-xs"
          style={{
            border: "1px dashed var(--border)",
            borderRadius: "var(--radius-control)",
            color: "var(--text-tertiary)",
          }}
        >
          Nothing matched. Loosen the filters or change the search.
        </p>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] content-start min-h-0 flex-1 gap-1.5 overflow-y-auto pr-1">
          {list.map((item) => {
            const added = addedIds.has(item.id);
            const sentiment = sentiments.get(item.id);
            const edge = added ? "var(--accent)" : "var(--border)";
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => toggle(item)}
                  title={
                    added
                      ? "Click to remove from evidence"
                      : "Click to add as evidence"
                  }
                  aria-pressed={added}
                  className="flex w-full flex-col gap-1 px-2.5 py-2 text-left"
                  style={{
                    background: "var(--bg)",
                    // Longhands only (no `border` shorthand): React warns when a
                    // shorthand and a side-specific property change independently.
                    // The 3px left edge carries sentiment; the accent ring marks "added".
                    borderStyle: "solid",
                    borderWidth: "1px 1px 1px 3px",
                    borderColor: `${edge} ${edge} ${edge} ${sentiment ? SENTIMENT_COLOR[sentiment] : "var(--border)"}`,
                    boxShadow: added ? "0 0 0 1px var(--accent)" : "none",
                    borderRadius: "var(--radius-control)",
                  }}
                >
                  <div
                    className="flex items-center gap-1.5 text-[10px]"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    {item.source === "twitter" ? (
                      <TwitterLogo size={12} />
                    ) : (
                      <Newspaper size={12} />
                    )}
                    <span
                      className="truncate font-medium"
                      style={{ color: "var(--text-secondary)" }}
                    >
                      {item.author ?? new URL(item.url, "http://x").hostname}
                    </span>
                    {sentiment && (
                      <span
                        className="shrink-0 font-semibold"
                        style={{ color: SENTIMENT_COLOR[sentiment] }}
                        title={`${SENTIMENT_LABEL[sentiment]} sentiment (Jev)`}
                      >
                        {SENTIMENT_GLYPH[sentiment]}{" "}
                        {SENTIMENT_LABEL[sentiment]}
                      </span>
                    )}
                    <span className="ml-auto shrink-0">
                      {formatDate(item.published_at)}
                    </span>
                    {added && (
                      <CheckCircle
                        size={14}
                        weight="fill"
                        style={{ color: "var(--accent)" }}
                      />
                    )}
                  </div>
                  <p
                    className="line-clamp-2 text-xs font-semibold leading-snug"
                    style={{ color: "var(--text-primary)" }}
                  >
                    {item.title}
                  </p>
                  {item.engagement && (
                    <p
                      className="text-[10px] tabular-nums"
                      style={{ color: "var(--text-tertiary)" }}
                    >
                      {item.engagement.likes} likes · {item.engagement.reposts}{" "}
                      reposts
                    </p>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
