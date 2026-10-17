import { useEffect, useId, useState, type FormEvent, type KeyboardEvent } from "react";
import type { CompanyRef } from "../../types/graph";
import { searchCompanies } from "./api";

interface Props {
  initialValue?: string;
  onSelect: (symbol: string) => void;
}

/** Ticker or name search with autocomplete from GET /companies/search. */
export default function CompanySearch({ initialValue = "", onSelect }: Props) {
  const [query, setQuery] = useState(initialValue);
  const [matches, setMatches] = useState<CompanyRef[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setMatches([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      searchCompanies(q)
        .then((res) => {
          if (!cancelled) {
            setMatches(res);
            setActive(-1);
          }
        })
        .catch(() => {
          if (!cancelled) setMatches([]);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const choose = (symbol: string) => {
    setQuery(symbol);
    setOpen(false);
    onSelect(symbol);
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (active >= 0 && matches[active]) {
      choose(matches[active].symbol);
      return;
    }
    const symbol = query.trim().toUpperCase();
    if (symbol) choose(symbol);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!open || matches.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % matches.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i <= 0 ? matches.length - 1 : i - 1));
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  const showList = open && matches.length > 0;

  return (
    <form onSubmit={handleSubmit} className="relative flex w-full gap-2" role="search">
      <div className="relative min-w-0 flex-1">
        <label htmlFor={`${listId}-input`} className="sr-only">
          Search for a company
        </label>
        <input
          id={`${listId}-input`}
          type="text"
          value={query}
          autoComplete="off"
          placeholder="Search a company, e.g. TSLA"
          role="combobox"
          aria-expanded={showList}
          aria-controls={`${listId}-list`}
          aria-activedescendant={active >= 0 ? `${listId}-opt-${active}` : undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onKeyDown={handleKeyDown}
          className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900 shadow-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
        />
        {showList && (
          <ul
            id={`${listId}-list`}
            role="listbox"
            className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-md border border-slate-200 bg-white py-1 shadow-lg"
          >
            {matches.map((m, i) => (
              <li
                key={m.symbol}
                id={`${listId}-opt-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(m.symbol);
                }}
                className={`flex cursor-pointer gap-2 px-3 py-2 text-sm ${
                  i === active ? "bg-slate-100" : "hover:bg-slate-50"
                }`}
              >
                <span className="font-semibold text-slate-900">{m.symbol}</span>
                <span className="truncate text-slate-600">{m.name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <button
        type="submit"
        className="shrink-0 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
      >
        Search
      </button>
    </form>
  );
}
