"use client";

/** Public entry point: explains the dual portal and authenticates both roles. */

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { BrandMark } from "@/components/portal/PortalShell";
import { homeForRole, useAuth } from "@/components/portal/AuthProvider";
import { PortalApiError } from "@/lib/portal-api";

type Mode = "login" | "register";

const DEMO_ACCOUNTS = [
  {
    label: "Bank officer",
    email: "banker@agritrust.ai",
    password: "",
    detail: "Provision via bootstrap script",
  },
  {
    label: "Farmer",
    email: "farmer@agritrust.ai",
    password: "Farmer@123",
    detail: "Submit land, crop and UPI evidence",
  },
  {
    label: "Shopkeeper",
    email: "shop@agritrust.ai",
    password: "Shop@123",
    detail: "Submit inventory and utility evidence",
  },
];

const TAB_LABELS: Record<Mode, { short: string; long: string }> = {
  login: { short: "Sign in", long: "Access your portal" },
  register: { short: "Create account", long: "Start your application" },
};

export function PortalLanding() {
  const router = useRouter();
  const { status, user, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);
  const [infoNotice, setInfoNotice] = useState<string | null>(null);

  useEffect(() => {
    if (status === "authenticated" && user) router.replace(homeForRole(user.role));
  }, [status, user, router]);

  const resetFeedback = () => {
    setError(null);
    setFieldErrors([]);
    setInfoNotice(null);
  };

  const fillDemo = (account: (typeof DEMO_ACCOUNTS)[number]) => {
    setMode("login");
    setEmail(account.email);
    setPassword(account.password);
    resetFeedback();
    if (!account.password) {
      setInfoNotice(
        "Bank officer accounts must be provisioned via scripts/bootstrap_admin.py. Please enter your provisioned password."
      );
    }
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    resetFeedback();
    try {
      const signedIn = mode === "login"
        ? await login(email.trim(), password)
        : await register({
            email: email.trim(),
            password,
            full_name: fullName.trim(),
            phone: phone.trim() || undefined,
          });
      router.replace(homeForRole(signedIn.role));
    } catch (caught) {
      if (caught instanceof PortalApiError) {
        setError(caught.message);
        setFieldErrors(caught.fields);
      } else {
        setError("Sign-in failed unexpectedly. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative min-h-screen bg-slate-950 text-slate-100">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(ellipse_at_top,rgba(16,185,129,0.18),transparent_65%)]" />

      <div className="relative mx-auto grid min-h-screen max-w-6xl grid-cols-1 gap-12 px-5 py-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:py-16">
        {/* narrative */}
        <section className="space-y-7">
          <div className="flex items-center gap-3">
            <BrandMark className="h-10 w-10" />
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-emerald-400">
                AgriTrust AI
              </p>
              <p className="text-sm text-slate-400">Alternative credit infrastructure</p>
            </div>
          </div>

          <div>
            <h1 className="max-w-xl text-4xl font-semibold leading-tight text-white sm:text-5xl">
              Credit for the farmers and shopkeepers the bureau never saw.
            </h1>
            <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-300">
              Applicants declare what they actually own — land, equipment, inventory — and their
              digital cash-flow footprint. Bank officers verify the evidence, then run an explainable
              XGBoost scorecard. The applicant never touches the model.
            </p>
          </div>

          <dl className="grid max-w-xl grid-cols-2 gap-3 text-xs">
            {[
              ["300–900", "Calibrated credit score"],
              ["3 tiers", "Pre-approved · review · high risk"],
              ["SHAP", "Signed, ranked reasons"],
              ["Dual portal", "Borrower and banker, separated"],
            ].map(([value, label]) => (
              <div
                key={label}
                className="rounded-xl border border-slate-800/80 bg-slate-900/50 p-3 transition hover:border-slate-700/80"
              >
                <dt className="text-base font-semibold text-emerald-300">{value}</dt>
                <dd className="mt-0.5 text-slate-400">{label}</dd>
              </div>
            ))}
          </dl>

          <div className="space-y-3">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              Demo identities
            </p>
            <div className="grid gap-2.5 sm:grid-cols-3">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  data-testid={`demo-${account.label.toLowerCase().replaceAll(" ", "-")}`}
                  onClick={() => fillDemo(account)}
                  className="group relative rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-emerald-500/50 hover:bg-slate-900 hover:shadow-lg hover:shadow-emerald-500/10 active:scale-[0.98]"
                >
                  <span className="absolute right-3 top-3 text-[10px] font-mono text-slate-500 transition-colors group-hover:text-emerald-400">
                    try me
                  </span>
                  <p className="text-xs font-semibold text-white group-hover:text-emerald-300">
                    {account.label}
                  </p>
                  <p className="mt-1 text-[11px] leading-snug text-slate-400">{account.detail}</p>
                  <p className="mt-2 truncate font-mono text-[10px] text-slate-500">
                    {account.email}
                  </p>
                </button>
              ))}
            </div>
            <p className="text-[11px] text-slate-500">
              Click a card to prefill the form — demo passwords follow the pattern Role@123.
            </p>
          </div>
        </section>

        {/* auth card */}
        <section className="w-full">
          <div className="rounded-2xl border border-slate-800/80 bg-slate-900/85 p-6 shadow-2xl shadow-black/40 backdrop-blur">
            <div className="flex rounded-xl bg-slate-950/70 p-1">
              {((Object.keys(TAB_LABELS) as Mode[])).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setMode(value);
                    resetFeedback();
                  }}
                  aria-pressed={mode === value}
                  className={`relative flex-1 rounded-lg px-3 py-2 text-xs font-medium transition ${
                    mode === value
                      ? "text-white shadow-sm"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {TAB_LABELS[value].short}
                  {mode === value && (
                    <span className="absolute bottom-0 left-[2px] right-[2px] h-0.5 rounded-full bg-emerald-400" />
                  )}
                </button>
              ))}
            </div>

            <h2 className="mt-5 text-lg font-semibold text-white">
              {TAB_LABELS[mode].long}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              {mode === "login"
                ? "Bank officers land in the underwriting queue. Borrowers track their application status."
                : "Borrower accounts are self-service. Bank officer accounts are provisioned by the institution."}
            </p>

            <form onSubmit={handleSubmit} className="mt-5 space-y-3">
              {mode === "register" && (
                <>
                  <Field
                    label="Full name"
                    value={fullName}
                    onChange={setFullName}
                    placeholder="Asha Kumari"
                    required
                    autoComplete="name"
                  />
                  <Field
                    label="Phone (optional)"
                    value={phone}
                    onChange={setPhone}
                    placeholder="+91 98765 43210"
                    autoComplete="tel"
                  />
                </>
              )}
              <Field
                label="Email"
                type="email"
                value={email}
                onChange={setEmail}
                placeholder="you@example.com"
                required
                autoComplete="email"
              />
              <Field
                label="Password"
                type="password"
                value={password}
                onChange={setPassword}
                placeholder={mode === "register" ? "At least 8 chars, 1 letter + 1 digit" : "••••••••"}
                required
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />

              {infoNotice && (
                <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-200">
                  <p className="font-semibold">{infoNotice}</p>
                </div>
              )}

              {error && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
                  <p className="font-semibold">{error}</p>
                  {fieldErrors.length > 0 && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4">
                      {fieldErrors.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <button
                type="submit"
                disabled={busy}
                data-testid="auth-submit"
                className="group relative w-full overflow-hidden rounded-xl bg-gradient-to-r from-emerald-500 to-teal-400 px-4 py-3 text-sm font-semibold text-slate-950 shadow-md shadow-emerald-500/20 transition-all duration-300 hover:from-emerald-400 hover:to-teal-300 hover:shadow-lg hover:shadow-emerald-500/30 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {/* Button Shimmer highlight */}
                <span className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/25 to-transparent transition-transform duration-700 group-hover:translate-x-full" />

                <div className="flex items-center justify-center gap-2">
                  {busy ? (
                    <>
                      <svg
                        className="h-4 w-4 animate-spin text-slate-950"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                      >
                        <circle
                          className="opacity-25"
                          cx="12"
                          cy="12"
                          r="10"
                          stroke="currentColor"
                          strokeWidth="4"
                        />
                        <path
                          className="opacity-75"
                          fill="currentColor"
                          d="M4 12a8 8 0 018-8v8H4z"
                        />
                      </svg>
                      <span>Authenticating session…</span>
                    </>
                  ) : (
                    <>
                      <span>{mode === "login" ? "Sign in to Dashboard" : "Create Account & Enter Portal"}</span>
                      <svg
                        viewBox="0 0 20 20"
                        fill="currentColor"
                        className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-0.5"
                      >
                        <path
                          fillRule="evenodd"
                          d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z"
                          clipRule="evenodd"
                        />
                      </svg>
                    </>
                  )}
                </div>
              </button>
            </form>

            <p className="mt-5 border-t border-slate-800/80 pt-4 text-[11px] leading-relaxed text-slate-500">
              <span className="font-medium text-slate-400">Role separation is enforced by the API.</span>
              <span className="block mt-1 text-slate-500">
                A borrower token can only create and read its own application. Scoring requires a banker
                token, verified evidence and a server-side role check.
              </span>
            </p>
          </div>

          <p className="mt-4 text-center text-[10px] leading-relaxed text-slate-600">
            Hackathon prototype. Synthetic data and model outputs must not be used for live credit
            decisions.
          </p>
        </section>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required,
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
  autoComplete?: string;
}) {
  const [showPassword, setShowPassword] = useState(false);
  const isPassword = type === "password";
  const effectiveType = isPassword ? (showPassword ? "text" : "password") : type;

  return (
    <label className="group block space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="block text-xs font-medium uppercase tracking-wide text-slate-400 transition-colors group-focus-within:text-emerald-400">
          {label}
          {required && <span className="ml-0.5 text-rose-400" aria-hidden>*</span>}
        </span>
      </div>

      <div className="relative flex items-center transition-all duration-300">
        {/* Left Input Icon */}
        <div className="pointer-events-none absolute left-3.5 flex items-center justify-center text-slate-500 transition-colors duration-300 group-focus-within:text-emerald-400">
          {type === "email" ? (
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : isPassword ? (
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : label.toLowerCase().includes("name") ? (
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          )}
        </div>

        {/* Input with animated focus ring and glow */}
        <input
          type={effectiveType}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          required={required}
          autoComplete={autoComplete}
          className="w-full rounded-xl border border-slate-700/80 bg-slate-950/70 pl-10 pr-10 py-2.5 text-sm text-white placeholder:text-slate-600 transition-all duration-300 focus:border-emerald-400 focus:bg-slate-950 focus:outline-none focus:ring-2 focus:ring-emerald-400/30 focus:shadow-[0_0_20px_rgba(16,185,129,0.18)]"
        />

        {/* Password Show/Hide Toggle */}
        {isPassword && (
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShowPassword((prev) => !prev)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            className="absolute right-3 flex items-center justify-center text-slate-500 transition-colors duration-200 hover:text-emerald-400 focus:outline-none"
          >
            {showPassword ? (
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l18 18"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            )}
          </button>
        )}
      </div>
    </label>
  );
}
