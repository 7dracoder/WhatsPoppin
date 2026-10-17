import {
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
  type ReactNode,
} from "react";

const DEFAULT_WIDTH = 320; // w-80, the width the sidebars were designed at
const MIN_WIDTH = 280;
const MAX_WIDTH = 720;
/** The main pane to the sidebar's left always keeps at least this much. */
const MIN_MAIN_WIDTH = 360;

function storedWidth(key: string): number {
  // localStorage can throw (private windows, blocked site data): fall back to the default.
  try {
    const value = Number(window.localStorage.getItem(key));
    return value >= MIN_WIDTH && value <= MAX_WIDTH ? value : DEFAULT_WIDTH;
  } catch {
    return DEFAULT_WIDTH;
  }
}

/**
 * A right-hand sidebar whose left edge can be dragged to make it wider or
 * narrower. The width is remembered per `storageKey` in localStorage: it's a
 * per-viewer layout preference, not app state, so it doesn't belong in the
 * workspace reducer or a saved project.
 *
 * The handle uses pointer capture, so the drag keeps tracking when the pointer
 * moves faster than the edge or crosses the graph canvas (which would otherwise
 * swallow the move events). Arrow keys resize it too, for keyboard users.
 */
export function ResizableAside({
  storageKey,
  widthVar,
  children,
}: {
  storageKey: string;
  /**
   * A CSS custom property (e.g. "--markets-sidebar-width") kept equal to the
   * current width on the document root, for things outside the sidebar that
   * must stay clear of it, like the alerts button floating beside it.
   */
  widthVar?: string;
  children: ReactNode;
}) {
  const asideRef = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(() => storedWidth(storageKey));
  const drag = useRef<{ x: number; start: number } | null>(null);

  // A layout effect, so whatever is positioned from the variable moves in the same paint as the sidebar.
  useLayoutEffect(() => {
    if (!widthVar) return;
    const root = document.documentElement;
    root.style.setProperty(widthVar, `${width}px`);
    return () => {
      root.style.removeProperty(widthVar);
    };
  }, [widthVar, width]);

  function apply(next: number) {
    const row = asideRef.current?.parentElement;
    const room = row ? row.clientWidth - MIN_MAIN_WIDTH : MAX_WIDTH;
    const clamped = Math.round(
      Math.max(MIN_WIDTH, Math.min(next, MAX_WIDTH, Math.max(MIN_WIDTH, room))),
    );
    setWidth(clamped);
    try {
      window.localStorage.setItem(storageKey, String(clamped));
    } catch {
      // Not remembered this time; the sidebar still resizes.
    }
  }

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault(); // no text selection while dragging
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, start: width };
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    // The sidebar is docked right, so dragging left (a smaller clientX) widens it.
    if (d) apply(d.start + (d.x - e.clientX));
  };
  const endDrag = () => {
    drag.current = null;
  };

  return (
    <aside
      ref={asideRef}
      className="relative h-full shrink-0"
      style={{ width }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        aria-valuenow={width}
        aria-valuemin={MIN_WIDTH}
        aria-valuemax={MAX_WIDTH}
        tabIndex={0}
        title="Drag to resize"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onDoubleClick={() => apply(DEFAULT_WIDTH)}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") apply(width + 24);
          if (e.key === "ArrowRight") apply(width - 24);
        }}
        // Straddles the edge: a 9px target around a line that only shows on hover or focus.
        className="group absolute inset-y-0 -left-1 z-20 flex w-[9px] cursor-col-resize touch-none justify-center outline-none"
      >
        <span
          className="h-full w-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 group-active:opacity-100"
          style={{ background: "var(--accent)" }}
        />
      </div>
      {children}
    </aside>
  );
}
