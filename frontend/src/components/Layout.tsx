import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

const links = [
  { to: "/chat", label: "Chat", auditorOnly: false },
  { to: "/upload", label: "Upload", auditorOnly: false },
  { to: "/findings", label: "Findings", auditorOnly: true },
  { to: "/runs", label: "Runs", auditorOnly: true },
  { to: "/documents", label: "Documents", auditorOnly: true },
  { to: "/evaluation", label: "Evaluation", auditorOnly: true },
];

export default function Layout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const isAuditor = me?.role === "auditor";

  async function signOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen bg-linear-to-br from-indigo-50 via-white to-violet-50 text-slate-900">
      <header className="bg-linear-to-r from-indigo-600 to-violet-600 text-white shadow-md">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3">
          <span className="font-semibold tracking-wide">KB Conflict Auditor</span>
          <nav className="flex flex-wrap gap-2 text-sm">
            {links
              .filter((link) => isAuditor || !link.auditorOnly)
              .map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className={({ isActive }) =>
                    isActive
                      ? "rounded-full bg-white/20 px-3 py-1 font-medium text-white"
                      : "rounded-full px-3 py-1 text-indigo-100 hover:bg-white/10 hover:text-white"
                  }
                >
                  {link.label}
                </NavLink>
              ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-indigo-100">
              {me?.username} · {me?.role}
              {me?.department ? ` · ${me.department}` : ""}
            </span>
            <button type="button" onClick={signOut} className="font-medium text-white hover:underline">
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
