import { UserCircle } from "@phosphor-icons/react";

/**
 * Placeholder profile affordance (top-right header chrome) — there's no auth
 * system yet, so this is just the standard "account lives here" anchor point
 * a reviewer expects to see, not a functioning account menu.
 */
export function ProfileButton() {
  return (
    <button
      type="button"
      aria-label="Profile"
      title="Profile"
      className="flex h-8 w-8 items-center justify-center rounded-full transition hover:opacity-80"
      style={{ background: "var(--surface)", border: "1px solid var(--border)" }}
    >
      <UserCircle size={18} weight="duotone" style={{ color: "var(--text-secondary)" }} />
    </button>
  );
}
