/**
 * Every lib/api/* module is a mock today — see new_specs/frontend-architecture-spec.md
 * Section 5 for the real endpoint contracts these stand in for. `delay()` simulates
 * real network/LLM latency so loading states (Section 6 of that spec) are actually
 * exercised during development instead of resolving instantly.
 */
export function delay<T>(value: T, ms: number): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}
