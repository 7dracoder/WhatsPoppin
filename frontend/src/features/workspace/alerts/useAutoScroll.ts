import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type RefObject,
} from "react";

/** Slow enough to read a card as it passes: about one card every three seconds. */
const SPEED_PX_PER_S = 18;
/** A new move is shown at the top and held there this long before scrolling resumes. */
const NEW_ITEM_HOLD_MS = 3000;
/** Wheel and touch have no "leave" event, so they pause the scroll for this long instead. */
const MANUAL_HOLD_MS = 3000;

/**
 * How many items were added ahead of the old first one (the list is newest
 * first, so new items arrive at the top). 0 when nothing arrived; null when the
 * old first item is gone, so there is nothing left to measure against.
 */
export function prependedCount(
  prev: readonly number[],
  next: readonly number[],
): number | null {
  if (prev.length === 0 || next.length === 0) return 0;
  const at = next.indexOf(prev[0]);
  return at === -1 ? null : at;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!query) return;
    setReduced(query.matches);
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

interface AutoScroll {
  scrollerRef: RefObject<HTMLDivElement>;
  /** The first copy of the list; the caller renders a second copy after it when `loops`. */
  firstCopyRef: RefObject<HTMLUListElement>;
  /** True when the list is taller than its box: render the second copy so the scroll can wrap. */
  loops: boolean;
  scrollerProps: HTMLAttributes<HTMLDivElement>;
}

/**
 * Scrolls a list upward on its own and wraps around, like a departures board.
 *
 * The list is rendered twice, one copy after the other. Once the first copy
 * has scrolled out, the second sits exactly where the first started, so
 * jumping back by one copy's height can't be seen. That jump is the whole loop.
 *
 * It drives the element's real scrollTop from requestAnimationFrame instead of
 * a CSS transform animation, for two reasons: the user can still scroll the
 * list by hand, and the position is a number we own, so it can be corrected
 * when the list changes (a CSS animation's progress is a fraction of the
 * content height, so adding a card would shift everything on screen).
 *
 * Pauses under the pointer or keyboard focus, so a card never moves away from
 * a click. Does nothing when the list fits or the user asked for reduced motion.
 *
 * When new items arrive at the top (`ids` is newest first):
 *  - nobody is interacting: jump to the top and hold, so the new move is seen;
 *  - the user is hovering or focused: keep what they are looking at in place.
 */
export function useAutoScroll(ids: readonly number[]): AutoScroll {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const firstCopyRef = useRef<HTMLUListElement>(null);
  const [loops, setLoops] = useState(false);
  const reduced = usePrefersReducedMotion();
  // In a ref, not state: it changes every frame and nothing renders from it.
  const motion = useRef({ pos: 0, written: 0, paused: false, holdUntil: 0 });
  const prevIds = useRef<readonly number[]>([]);
  const hasItems = ids.length > 0;

  // Loop only when one copy is taller than the box.
  useEffect(() => {
    const scroller = scrollerRef.current;
    const first = firstCopyRef.current;
    if (!scroller || !first || reduced || !("ResizeObserver" in window)) {
      setLoops(false);
      return;
    }
    const measure = () =>
      setLoops(first.offsetHeight > scroller.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    observer.observe(first);
    return () => observer.disconnect();
  }, [reduced, hasItems]);

  /** Distance from the first copy's top to the second's: one full loop. */
  function period(): number {
    const first = firstCopyRef.current;
    const second = first?.nextElementSibling;
    return first && second instanceof HTMLElement
      ? second.offsetTop - first.offsetTop
      : 0;
  }

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!loops || !scroller) return;
    const m = motion.current;
    let last = performance.now();
    let frame = 0;
    const step = (now: number) => {
      // Capped so a tab coming back from the background doesn't leap ahead.
      const dt = Math.min(now - last, 100);
      last = now;
      const active = !m.paused && now >= m.holdUntil;
      // scrollTop differing from what we last wrote means the user moved it: follow them.
      if (!active || Math.abs(scroller.scrollTop - m.written) > 1)
        m.pos = scroller.scrollTop;
      const before = m.pos;
      if (active) m.pos += (SPEED_PX_PER_S * dt) / 1000;
      const loop = period();
      if (loop > 0 && m.pos >= loop) m.pos -= loop;
      if (m.pos !== before) {
        // Browsers round scrollTop to a device pixel, so `pos` keeps the fraction.
        scroller.scrollTop = m.pos;
        m.written = scroller.scrollTop;
      }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [loops]);

  // New items. A layout effect, so the correction lands before the browser paints the new cards.
  useLayoutEffect(() => {
    const added = prependedCount(prevIds.current, ids);
    prevIds.current = ids;
    const scroller = scrollerRef.current;
    const first = firstCopyRef.current;
    if (!scroller || !first || added === 0) return;
    const m = motion.current;
    const oldFirst = added === null ? undefined : first.children[added];
    if (m.paused && oldFirst instanceof HTMLElement) {
      const newest = first.children[0] as HTMLElement;
      const grew = oldFirst.offsetTop - newest.offsetTop;
      // Both copies grew; a reader already in the second copy has two lots of new cards above them.
      const loop = period();
      const inSecondCopy = loop > 0 && scroller.scrollTop >= loop - grew;
      scroller.scrollTop += inSecondCopy ? grew * 2 : grew;
    } else {
      scroller.scrollTop = 0;
      m.holdUntil = performance.now() + NEW_ITEM_HOLD_MS;
    }
    m.pos = m.written = scroller.scrollTop;
  }, [ids]);

  const pause = () => {
    motion.current.paused = true;
  };
  const resume = () => {
    motion.current.paused = false;
  };
  const hold = () => {
    motion.current.holdUntil = performance.now() + MANUAL_HOLD_MS;
  };

  return {
    scrollerRef,
    firstCopyRef,
    loops,
    scrollerProps: {
      onMouseEnter: pause,
      onMouseLeave: resume,
      onFocus: pause,
      onBlur: resume,
      onWheel: hold,
      onTouchStart: hold,
      onTouchMove: hold,
    },
  };
}
