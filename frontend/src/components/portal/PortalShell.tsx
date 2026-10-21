"use client";

/** Shared chrome and guards for the borrower and banker portals. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { homeForRole, useAuth } from "@/components/portal/AuthProvider";
import type { ApplicationStatus, Role } from "@/lib/portal-types";

export function BrandMark({ className = "h-9 w-9" }: { className?: string }) {
  return (
    <span
      className={`inline-flex items-center justify-center rounded-xl bg-emerald-500/15 ring-1 ring-emerald-400/30 ${className}`}
      aria-hidden
    >
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5 text-emerald-400">
        <path
          d="M12 21c0-5.5 3.5-9.5 8-11-.5 5.5-3.7 9.4-8 11Z"
          fill="currentColor"
          opacity="0.85"
        />
        <path
          d="M12 21C9.8 16.9 7 13.5 4 11.5c.6 4.3 3.8 7.7 8 9.5Z"
          fill="currentColor"
          opacity="0.55"
        />
        <path d="M12 21V9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </span>
  );
}

/** Redirects to the correct portal when the session or role is wrong. */
export function RoleGate({ role, children }: { role: Role; children: React.ReactNode }) {
  const { user, status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace("/");
    } else if (status === "authenticated" && user && user.role !== role) {
      router.replace(homeForRole(user.role));
    }
  }, [status, user, role, router]);

  if (status !== "authenticated" || !user || user.role !== role) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-100">
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-5 py-4 text-sm text-slate-600 shadow-sm">
          <Spinner />
          Checking your session…
        </div>
      </div>
    );
  }
  return <>{children}</>;
}

export function PortalHeader({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  const { user, logout } = useAuth();
  const router = useRouter();

  return (
    <header className="border-b border-slate-800 bg-slate-900">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex items-center gap-3">
          <BrandMark />
          <div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-emerald-400">
                AgriTrust AI
              </span>
              {eyebrow && (
                <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-300">
                  {eyebrow}
                </span>
              )}
            </div>
            <h1 className="text-sm font-semibold text-white">{title}</h1>
            {subtitle && <p className="text-[11px] text-slate-400">{subtitle}</p>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {actions}
          {user && (
            <div className="flex items-center gap-2 rounded-full border border-slate-700 bg-slate-800/70 py-1 pl-3 pr-1">
              <div className="text-right">
                <p className="text-xs font-medium leading-tight text-white">{user.full_name}</p>
                <p className="text-[10px] uppercase tracking-wide text-slate-400">{user.role}</p>
              </div>
              <button
                type="button"
                onClick={() => {
                  logout();
                  router.replace("/");
                }}
                className="rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-medium text-slate-200 transition hover:bg-slate-600"
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

const STATUS_STYLES: Record<ApplicationStatus, { label: string; className: string; dot: string }> = {
  pending: {
    label: "Pending verification",
    className: "border-amber-200 bg-amber-50 text-amber-800",
    dot: "bg-amber-500",
  },
  verified: {
    label: "Verified",
    className: "border-sky-200 bg-sky-50 text-sky-800",
    dot: "bg-sky-500",
  },
  scored: {
    label: "Scored",
    className: "border-emerald-200 bg-emerald-50 text-emerald-800",
    dot: "bg-emerald-500",
  },
  rejected: {
    label: "Rejected",
    className: "border-rose-200 bg-rose-50 text-rose-800",
    dot: "bg-rose-500",
  },
};

export function StatusBadge({
  status,
  size = "md",
}: {
  status: ApplicationStatus;
  size?: "sm" | "md";
}) {
  const style = STATUS_STYLES[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-semibold ${style.className} ${
        size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs"
      }`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={`animate-spin text-slate-400 ${className}`} aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function ErrorAlert({
  message,
  fields = [],
  onDismiss,
}: {
  message: string;
  fields?: string[];
  onDismiss?: () => void;
}) {
  return (
    <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800" role="alert">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">{message}</p>
          {fields.length > 0 && (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
              {fields.slice(0, 6).map((field, index) => (
                <li key={`${index}-${field}`}>{field}</li>
              ))}
            </ul>
          )}
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-md px-1.5 text-rose-500 transition hover:bg-rose-100"
            aria-label="Dismiss error"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}
