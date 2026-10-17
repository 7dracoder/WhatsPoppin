/**
 * Y-axis domain rules for the overlay chart's panels. Pure so they can be
 * tested without recharts; the panels pass in the values on screen.
 */

/** Fraction of the odds span added on each side so the line doesn't touch the plot edge. */
const ODDS_PAD_FRACTION = 0.1;
/** Smallest padding in odds points: a flat market still gets a readable band, not a 0-wide one. */
const ODDS_MIN_PAD = 1;

/**
 * Odds y-range hugging the data: 10% of the span on each side, at least 1
 * point, so the span is never under ~2 points (a flat market reads as flat,
 * not as noise blown up to the full height). Clamped to the 0-100% a
 * probability can occupy, rounded to whole points so the ticks land on
 * integers. Tick labels stay in real percent, so a zoomed move still reads as
 * the points it actually was. Empty data falls back to the full scale.
 */
export function fitOddsDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 100];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pad = Math.max((hi - lo) * ODDS_PAD_FRACTION, ODDS_MIN_PAD);
  return [Math.max(0, Math.floor(lo - pad)), Math.min(100, Math.ceil(hi + pad))];
}

/** The volume strip takes the bottom quarter of a panel: the tallest bar reaches 1/VOLUME_HEADROOM of the height. */
export const VOLUME_HEADROOM = 4;

/**
 * Domain for the hidden volume axis so bars sit under the line as a strip
 * rather than competing with it. [0, 1] when there is nothing to draw, so
 * recharts never sees a [0, 0] domain.
 */
export function volumeDomain(volumes: Array<number | undefined>): [number, number] {
  let max = 0;
  for (const v of volumes) if (v !== undefined && v > max) max = v;
  return [0, max > 0 ? max * VOLUME_HEADROOM : 1];
}

/** "1.2M", "57.3K", "57": volume for a tooltip or tick, three significant figures at most. */
export function formatVolume(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${trim(v / 1e9)}B`;
  if (abs >= 1e6) return `${trim(v / 1e6)}M`;
  if (abs >= 1e3) return `${trim(v / 1e3)}K`;
  return trim(v);
}

/** Up to one decimal, without a trailing ".0". */
function trim(n: number): string {
  return (Math.round(n * 10) / 10).toString();
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
/** Candidate tick steps, smallest first: the first one that gives at most `target` ticks wins. */
const TIME_STEPS = [
  MINUTE, 5 * MINUTE, 15 * MINUTE, 30 * MINUTE,
  HOUR, 2 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR,
  DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 91 * DAY, 182 * DAY, 365 * DAY,
];

/**
 * Tick times for a time axis: whole minutes, hours, days or weeks, in the
 * viewer's time zone, so a one-day window reads 10:00, 11:00, ... and a week
 * reads one tick per day. recharts' own numeric ticks for an epoch-ms axis
 * land on arbitrary moments (10:17, 13:43). Steps of a day or more are
 * aligned to local midnight, so a daylight-saving change can shift one tick
 * by an hour, which is what a calendar expects.
 */
export function timeTicks(domain: [number, number], target = 6): number[] {
  const [lo, hi] = domain;
  const span = hi - lo;
  if (!(span > 0)) return [];
  const step = TIME_STEPS.find((s) => span / s <= target) ?? TIME_STEPS[TIME_STEPS.length - 1];
  const ticks: number[] = [];
  let t = ceilToStep(lo, step);
  for (let guard = 0; t <= hi && guard < 1000; guard += 1) {
    ticks.push(t);
    t = ceilToStep(t + 1, step);
  }
  return ticks;
}

/** The first multiple of `step` at or after `t`, in local time for day-sized steps. */
function ceilToStep(t: number, step: number): number {
  if (step < DAY) {
    // Sub-day steps align to the local clock: the offset from UTC is a whole number of
    // minutes, so dividing in local time keeps 10:00 at 10:00.
    const offset = new Date(t).getTimezoneOffset() * MINUTE;
    const local = t - offset;
    return Math.ceil(local / step) * step + offset;
  }
  const d = new Date(t);
  const midnight = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = step / DAY;
  let next = midnight;
  if (next < t) next = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime();
  if (days > 1) {
    // Multi-day steps count from the Unix epoch's local midnight, so ticks are stable across windows.
    const dayIndex = Math.round((next - new Date(1970, 0, 1).getTime()) / DAY);
    const skip = (days - (dayIndex % days)) % days;
    const n = new Date(next);
    next = new Date(n.getFullYear(), n.getMonth(), n.getDate() + skip).getTime();
  }
  return next;
}

/**
 * How to label a tick for a window of this span: times inside a day or two,
 * dates inside a year, months beyond. One format per axis, so the labels
 * read as a sequence.
 */
export function tickFormatterFor(domain: [number, number]): (t: number) => string {
  const span = domain[1] - domain[0];
  if (span <= 2 * DAY) return (t) => new Date(t).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (span <= 400 * DAY) return (t) => new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (t) => new Date(t).toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}
