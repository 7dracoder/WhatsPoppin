import { Link, Outlet } from "react-router-dom";
import { ThemeSwitcher } from "../features/theme/ThemeSwitcher";
import { ProfileButton } from "../features/theme/ProfileButton";

export default function AppLayout() {
  return (
    <div className="min-h-[100dvh]" style={{ background: "var(--bg)" }}>
      <header
        className="flex items-center justify-between border-b px-6 py-4"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      >
        <div className="flex items-center gap-4">
          <Link to="/" className="font-semibold" style={{ color: "var(--text-primary)" }}>
            What'sPoppin
          </Link>
        </div>
        <div className="flex items-center gap-4">
          <ThemeSwitcher />
          <ProfileButton />
        </div>
      </header>
      <main className="h-[calc(100dvh-65px)] overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
