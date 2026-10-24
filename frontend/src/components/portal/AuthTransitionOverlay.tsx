"use client";

import { useEffect, useState } from "react";
import type { Role } from "@/lib/portal-types";

export interface TransitionState {
  active: boolean;
  type: "login" | "logout" | "register";
  role?: Role;
  name?: string;
}

interface AuthTransitionOverlayProps {
  transition: TransitionState;
}

export function AuthTransitionOverlay({ transition }: AuthTransitionOverlayProps) {
  const [step, setStep] = useState(1);
  const [progress, setProgress] = useState(15);

  const isLogout = transition.type === "logout";
  const roleName = transition.role === "banker" ? "Bank Underwriting Officer" : "Borrower Portal";
  const targetConsole = transition.role === "banker" ? "Bank Underwriting Console" : "Borrower Dashboard";

  useEffect(() => {
    // Step progression timer sequence
    const t1 = setTimeout(() => {
      setStep(2);
      setProgress(55);
    }, 400);

    const t2 = setTimeout(() => {
      setStep(3);
      setProgress(95);
    }, 850);

    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-slate-950/85 backdrop-blur-2xl transition-all duration-500 animate-in fade-in"
      role="status"
      aria-live="polite"
      aria-label={isLogout ? "Signing out..." : "Signing in..."}
    >
      {/* Ambient glowing radial backdrop */}
      <div
        className={`pointer-events-none absolute h-[500px] w-[500px] rounded-full blur-[120px] transition-all duration-700 ${
          isLogout
            ? "bg-indigo-500/15"
            : transition.role === "banker"
            ? "bg-emerald-500/20"
            : "bg-teal-500/20"
        }`}
      />

      {/* Center Modal Card */}
      <div className="relative mx-4 w-full max-w-md overflow-hidden rounded-3xl border border-slate-700/60 bg-slate-900/90 p-8 shadow-[0_0_60px_rgba(0,0,0,0.6)] backdrop-blur-xl transition-all duration-300">
        {/* Top subtle accent glow bar */}
        <div
          className={`absolute inset-x-0 top-0 h-1 bg-gradient-to-r ${
            isLogout
              ? "from-slate-600 via-indigo-500 to-slate-600"
              : "from-emerald-600 via-teal-400 to-emerald-500"
          }`}
        />

        {/* Central Animated Orbital Rings */}
        <div className="relative mx-auto my-4 flex h-28 w-28 items-center justify-center">
          {/* Outer rotating gradient ring */}
          <div
            className={`absolute inset-0 rounded-full border-2 border-transparent border-t-emerald-400 border-r-teal-300 animate-spin-slow ${
              isLogout ? "!border-t-indigo-400 !border-r-slate-400" : ""
            }`}
          />

          {/* Middle counter-rotating dashed ring */}
          <div
            className={`absolute inset-2 rounded-full border border-dashed border-slate-600 animate-spin-reverse-slow ${
              isLogout ? "border-indigo-500/40" : "border-emerald-500/40"
            }`}
          />

          {/* Ambient inner glow */}
          <div
            className={`absolute inset-4 rounded-full blur-md animate-pulse-glow ${
              isLogout ? "bg-indigo-500/25" : "bg-emerald-500/25"
            }`}
          />

          {/* Center Icon */}
          <div className="relative z-10 flex h-14 w-14 items-center justify-center rounded-2xl bg-slate-950/80 shadow-inner ring-1 ring-white/10">
            {isLogout ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                className="h-7 w-7 text-indigo-400 transition-transform duration-300 hover:scale-110"
              >
                <path
                  d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : step === 3 ? (
              <svg
                viewBox="0 0 24 24"
                fill="none"
                className="h-8 w-8 text-emerald-400 animate-in zoom-in duration-200"
              >
                <path
                  d="M5 13l4 4L19 7"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" className="h-7 w-7 text-emerald-400">
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
                <path d="M12 21V9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </div>
        </div>

        {/* Dynamic Typography & Stage Feedback */}
        <div className="mt-2 text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-slate-700/60 bg-slate-800/60 px-3 py-1 text-[11px] font-medium tracking-wide text-slate-300">
            <span
              className={`h-2 w-2 rounded-full ${
                isLogout ? "bg-indigo-400 animate-ping" : "bg-emerald-400 animate-pulse"
              }`}
            />
            {isLogout ? "SECURE LOGOUT" : `AUTHENTICATED: ${roleName.toUpperCase()}`}
          </div>

          <h3 className="mt-3 text-xl font-semibold tracking-tight text-white">
            {isLogout
              ? "Signing Out Safely"
              : transition.name
              ? `Welcome, ${transition.name}`
              : "Access Granted"}
          </h3>

          {/* Stepped Transition Messages */}
          <div className="mt-2 min-h-[38px] text-xs leading-relaxed text-slate-400">
            {isLogout ? (
              step === 1 ? (
                <p className="animate-in fade-in duration-200">
                  Revoking cryptographic session token &amp; access cookies…
                </p>
              ) : step === 2 ? (
                <p className="animate-in fade-in duration-200">
                  Clearing secure client-side cache and credentials…
                </p>
              ) : (
                <p className="text-emerald-300 font-medium animate-in fade-in duration-200">
                  Session safely terminated. Returning to landing page…
                </p>
              )
            ) : step === 1 ? (
              <p className="animate-in fade-in duration-200">
                Validating signature &amp; establishing HttpOnly session…
              </p>
            ) : step === 2 ? (
              <p className="animate-in fade-in duration-200">
                Synchronizing dual-portal permissions &amp; evidence queue…
              </p>
            ) : (
              <p className="text-emerald-300 font-medium animate-in fade-in duration-200">
                Opening {targetConsole}…
              </p>
            )}
          </div>
        </div>

        {/* Animated Progress Bar */}
        <div className="mt-5">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className={`h-full transition-all duration-500 ease-out ${
                isLogout
                  ? "bg-gradient-to-r from-slate-500 to-indigo-400"
                  : "bg-gradient-to-r from-teal-500 via-emerald-400 to-emerald-300"
              }`}
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* Security Assurance Badge Footer */}
        <div className="mt-6 flex items-center justify-center gap-3 border-t border-slate-800/80 pt-4 text-[10px] text-slate-500">
          <span className="flex items-center gap-1">
            <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 text-emerald-500/80">
              <path
                d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            256-bit TLS Encrypted
          </span>
          <span>•</span>
          <span>HttpOnly &amp; Secure</span>
          <span>•</span>
          <span>RFC 7323 Hardened</span>
        </div>
      </div>
    </div>
  );
}
