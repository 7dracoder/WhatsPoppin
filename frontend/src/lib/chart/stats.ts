/**
 * Small statistics toolkit for comparing a stock with a prediction market.
 * Pure functions: no React, no fetching, no dependencies.
 *
 * WHY RETURNS / CHANGES, NOT LEVELS
 * Prices and odds are usually non-stationary (they wander, they trend). Two
 * independent random walks will often look strongly correlated just because
 * both drift: this is the "spurious regression" problem (Granger & Newbold,
 * 1974). Differencing (log returns for prices, change in logit for odds)
 * makes the series roughly stationary, so a correlation between them
 * measures co-movement of surprises rather than a shared trend.
 *
 * TODO (asynchronous data): stocks and prediction markets don't tick at the
 * same instants. Forcing them onto one grid and correlating biases the
 * correlation toward zero at fine sampling (the Epps effect). A
 * Hayashi-Yoshida estimator uses overlapping intervals of the raw,
 * unsynchronized ticks and avoids that bias.
 */

/**
 * Log return ln(P_t / P_{t-1}). Logs are used because they add across time
 * (a 2-day return is the sum of two 1-day returns) and are symmetric-ish
 * around zero, which suits correlation and regression.
 * Output has length n-1. A step where either price is non-positive or not
 * finite yields 0 (no information) instead of NaN/Infinity, which also keeps
 * indices aligned with other series.
 */
export function logReturns(values: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < values.length; i += 1) {
    const prev = values[i - 1];
    const curr = values[i];
    const valid = Number.isFinite(prev) && Number.isFinite(curr) && prev > 0 && curr > 0;
    out.push(valid ? Math.log(curr / prev) : 0);
  }
  return out;
}

/**
 * Log-odds of a probability given in percentage POINTS (0-100).
 * Why logit: a probability is bounded to [0, 1], so a move from 94% to 97%
 * is far more "informative" than 50% to 53%. The logit stretches the ends to
 * the whole real line, so equal changes mean roughly equal evidence.
 * Why clamp: logit(0) = -Infinity and logit(1) = +Infinity. Thin markets
 * print 0% / 100% often, so we clamp to [eps, 1 - eps] (default 0.5%) to keep
 * every value finite.
 */
export function logit(p: number, eps = 0.005): number {
  const prob = Math.min(1 - eps, Math.max(eps, p / 100));
  return Math.log(prob / (1 - prob));
}

/**
 * First differences of the logit: the odds analogue of a log return.
 * Output has length n-1.
 */
export function deltaLogit(odds: number[]): number[] {
  const l = odds.map((p) => logit(p));
  return l.slice(1).map((v, i) => v - l[i]);
}

function mean(xs: number[]): number {
  return xs.length === 0 ? 0 : xs.reduce((s, x) => s + x, 0) / xs.length;
}

/** Pearson correlation, or null when undefined (fewer than 2 points or zero variance). */
export function pearson(a: number[], b: number[]): number | null {
  const n = Math.min(a.length, b.length);
  if (n < 2) return null;
  const x = a.slice(0, n);
  const y = b.slice(0, n);
  const mx = mean(x);
  const my = mean(y);
  let sxy = 0;
  let sxx = 0;
  let syy = 0;
  for (let i = 0; i < n; i += 1) {
    const dx = x[i] - mx;
    const dy = y[i] - my;
    sxy += dx * dy;
    sxx += dx * dx;
    syy += dy * dy;
  }
  // A constant series has no spread, so "correlation" is 0/0: report null, not NaN.
  if (sxx === 0 || syy === 0) return null;
  // Clamp: floating-point rounding can push |r| a hair past 1.
  return Math.max(-1, Math.min(1, sxy / Math.sqrt(sxx * syy)));
}

/**
 * OLS of y (stock returns) on x (benchmark returns, e.g. SPY): y = alpha + beta*x + e.
 * Why: most of a stock's daily move is just the market. Subtracting beta*x
 * leaves the stock-specific part, so a later correlation with prediction-market
 * odds isn't just "both correlate with the market".
 * beta = cov(x, y) / var(x). If x has zero variance, beta is undefined, so we
 * use beta = 0 and the residuals are just y minus its mean.
 */
export function residualizeOnBenchmark(
  y: number[],
  x: number[],
): { residuals: number[]; beta: number; alpha: number } {
  const n = Math.min(y.length, x.length);
  const ys = y.slice(0, n);
  const xs = x.slice(0, n);
  const mx = mean(xs);
  const my = mean(ys);
  let sxy = 0;
  let sxx = 0;
  for (let i = 0; i < n; i += 1) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) ** 2;
  }
  const beta = sxx === 0 ? 0 : sxy / sxx;
  const alpha = my - beta * mx; // the OLS line always passes through (mean x, mean y)
  const residuals = ys.map((yi, i) => yi - alpha - beta * xs[i]);
  return { residuals, beta, alpha };
}

/**
 * Pearson correlation over a trailing window ending at each index.
 * Why rolling: a relationship that exists on average may come and go; a single
 * full-sample number hides that. Entries are null until `window` points exist,
 * or when the window is constant (undefined correlation).
 */
export function rollingCorrelation(a: number[], b: number[], window: number): (number | null)[] {
  const n = Math.min(a.length, b.length);
  const out: (number | null)[] = [];
  for (let i = 0; i < n; i += 1) {
    if (window < 2 || i + 1 < window) {
      out.push(null);
    } else {
      out.push(pearson(a.slice(i + 1 - window, i + 1), b.slice(i + 1 - window, i + 1)));
    }
  }
  return out;
}

/**
 * 95% confidence interval for a correlation via the Fisher z-transform.
 * Why: r is bounded to [-1, 1] and its sampling distribution is skewed near
 * the edges, so r +/- 1.96*se is wrong. z = atanh(r) is approximately normal
 * with standard error 1/sqrt(n-3); build the interval there, then map back
 * with tanh. Needs n > 3; otherwise we return the uninformative [-1, 1].
 */
export function correlationCI(r: number, n: number): [number, number] {
  if (n <= 3 || !Number.isFinite(r)) return [-1, 1];
  const rc = Math.max(-0.999999, Math.min(0.999999, r)); // atanh(+/-1) is infinite
  const z = Math.atanh(rc);
  const se = 1 / Math.sqrt(n - 3);
  return [Math.tanh(z - 1.96 * se), Math.tanh(z + 1.96 * se)];
}

/**
 * Cross-correlation corr(a_t, b_{t+lag}) for every lag in [-maxLag, maxLag].
 * Sign convention: positive lag = b lags a (a LEADS b), because a_t is paired
 * with a LATER b value. Negative lag means b leads a.
 * `n` is the number of overlapping pairs used at that lag, so callers can
 * judge (and put a CI on) each r. Undefined correlations (constant data, <2
 * pairs) are reported as r = 0 to keep the output NaN-free.
 */
export function crossCorrelation(
  a: number[],
  b: number[],
  maxLag: number,
): { lag: number; r: number; n: number }[] {
  const out: { lag: number; r: number; n: number }[] = [];
  for (let lag = -maxLag; lag <= maxLag; lag += 1) {
    // Pair a[i] with b[i + lag]; keep only indices valid in both series.
    const start = Math.max(0, -lag);
    const end = Math.min(a.length, b.length - lag);
    const n = Math.max(0, end - start);
    const xs = a.slice(start, start + n);
    const ys = b.slice(start + lag, start + lag + n);
    out.push({ lag, r: pearson(xs, ys) ?? 0, n });
  }
  return out;
}
