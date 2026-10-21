"use client";

/**
 * Client-side session context.
 *
 * The JWT is persisted in localStorage and re-validated against `/api/auth/me`
 * on boot, so a stale or tampered token cannot leave the UI in a half-signed-in
 * state. Server-side role checks remain the real authorisation boundary; this
 * provider only decides which portal to render.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { portalApi } from "@/lib/portal-api";
import type { AuthResponse, PortalUser, Role } from "@/lib/portal-types";

const STORAGE_KEY = "agritrust.session";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  user: PortalUser | null;
  token: string | null;
  status: AuthStatus;
  login: (email: string, password: string) => Promise<PortalUser>;
  register: (input: {
    email: string;
    password: string;
    full_name: string;
    phone?: string;
  }) => Promise<PortalUser>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

interface StoredSession {
  token: string;
  user: PortalUser;
}

function readStoredSession(): StoredSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredSession>;
    if (!parsed.token || !parsed.user?.role) return null;
    return { token: parsed.token, user: parsed.user as PortalUser };
  } catch {
    return null;
  }
}

function storeSession(session: StoredSession | null): void {
  if (typeof window === "undefined") return;
  if (session) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  else window.localStorage.removeItem(STORAGE_KEY);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // The initial session is read during the first render rather than in an
  // effect: localStorage is synchronous, and setting it from an effect would
  // trigger an avoidable second render on every page load.
  const initialSession = readStoredSession();
  const [user, setUser] = useState<PortalUser | null>(initialSession?.user ?? null);
  const [token, setToken] = useState<string | null>(initialSession?.token ?? null);
  const [status, setStatus] = useState<AuthStatus>(
    initialSession ? "loading" : "anonymous",
  );

  useEffect(() => {
    const stored = readStoredSession();
    if (!stored) return; // status is already "anonymous"

    let cancelled = false;
    (async () => {
      try {
        // Re-validate: a token may have expired while the tab was closed.
        const freshUser = await portalApi.me(stored.token);
        if (cancelled) return;
        setUser(freshUser);
        setToken(stored.token);
        storeSession({ token: stored.token, user: freshUser });
        setStatus("authenticated");
      } catch {
        if (cancelled) return;
        storeSession(null);
        setStatus("anonymous");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const applySession = useCallback((response: AuthResponse) => {
    setUser(response.user);
    setToken(response.access_token);
    storeSession({ token: response.access_token, user: response.user });
    setStatus("authenticated");
    return response.user;
  }, []);

  const login = useCallback(
    async (email: string, password: string) => applySession(await portalApi.login(email, password)),
    [applySession],
  );

  const register = useCallback(
    async (input: { email: string; password: string; full_name: string; phone?: string }) =>
      applySession(await portalApi.register(input)),
    [applySession],
  );

  const logout = useCallback(() => {
    storeSession(null);
    setUser(null);
    setToken(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo(
    () => ({ user, token, status, login, register, logout }),
    [user, token, status, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}

export function homeForRole(role: Role): string {
  return role === "banker" ? "/banker" : "/borrower";
}


