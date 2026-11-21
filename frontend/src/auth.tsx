import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, clearTokens, hasTokens, setSignedOutHandler } from "./api";
import type { Me, Role } from "./types";

interface AuthState {
  me: Me | null; // null = still checking
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    // If the tokens can no longer be refreshed while the app is open, go back to the login page.
    setSignedOutHandler(() => setMe({ authenticated: false }));

    // Page (re)loaded: tokens saved from an earlier login? Ask who they belong to
    // (an expired access token is refreshed automatically by api.ts).
    if (!hasTokens()) {
      setMe({ authenticated: false });
      return;
    }
    api
      .me()
      .then((data) => {
        if (!data.authenticated) clearTokens();
        setMe(data);
      })
      .catch(() => {
        clearTokens();
        setMe({ authenticated: false });
      });
  }, []);

  async function login(username: string, password: string) {
    setMe(await api.login(username, password));
  }

  async function logout() {
    await api.logout();
    setMe({ authenticated: false });
  }

  return <AuthContext.Provider value={{ me, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const state = useContext(AuthContext);
  if (!state) throw new Error("useAuth must be used inside <AuthProvider>");
  return state;
}

/** Shows children only to logged-in users (and only auditors if role="auditor"). */
export function RequireAuth({ children, role }: { children: ReactNode; role?: Role }) {
  const { me } = useAuth();
  const location = useLocation();
  if (me === null) return <p className="p-6 text-sm text-slate-500">Loading…</p>;
  if (!me.authenticated) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (role === "auditor" && me.role !== "auditor") return <Navigate to="/chat" replace />;
  return <>{children}</>;
}

/** Start page: auditors land on Findings, employees on Chat. */
export function HomeRedirect() {
  const { me } = useAuth();
  return <Navigate to={me?.role === "auditor" ? "/findings" : "/chat"} replace />;
}
