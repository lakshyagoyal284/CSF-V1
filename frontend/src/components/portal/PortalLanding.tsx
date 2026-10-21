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
    password: "Banker@123",
    detail: "Verify evidence and run the credit model",
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

  useEffect(() => {
    if (status === "authenticated" && user) router.replace(homeForRole(user.role));
  }, [status, user, router]);

  const resetFeedback = () => {
    setError(null);
    setFieldErrors([]);
  };

  const fillDemo = (account: (typeof DEMO_ACCOUNTS)[number]) => {
    setMode("login");
    setEmail(account.email);
    setPassword(account.password);
    resetFeedback();
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
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[520px] bg-[radial-gradient(ellipse_at_top,rgba(16,185,129,0.16),transparent_60%)]" />

      <div className="relative mx-auto grid min-h-screen max-w-6xl grid-cols-1 gap-10 px-5 py-10 lg:grid-cols-[1.1fr_0.9fr] lg:items-center lg:py-16">
        {/* narrative */}
        <section>
          <div className="flex items-center gap-3">
            <BrandMark className="h-11 w-11" />
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-emerald-400">
                AgriTrust AI
              </p>
              <p className="text-sm text-slate-400">Alternative credit infrastructure</p>
            </div>
          </div>

          <h1 className="mt-8 max-w-xl text-4xl font-semibold leading-tight text-white sm:text-5xl">
            Credit for the farmers and shopkeepers the bureau never saw.
          </h1>
          <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-300">
            Applicants declare what they actually own - land, equipment, inventory - and
            their digital cash-flow footprint. Bank officers verify the evidence, then run an
            explainable XGBoost scorecard. The applicant never touches the model.
          </p>

          <dl className="mt-8 grid max-w-xl grid-cols-2 gap-3 text-xs">
            {[
              ["300-900", "Calibrated credit score"],
              ["3 tiers", "Pre-approved · review · high risk"],
              ["SHAP", "Signed, ranked reasons"],
              ["Dual portal", "Borrower and banker, separated"],
            ].map(([value, label]) => (
              <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
                <dt className="text-base font-semibold text-emerald-300">{value}</dt>
                <dd className="mt-0.5 text-slate-400">{label}</dd>
              </div>
            ))}
          </dl>

          <div className="mt-8">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-500">
              Demo identities
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              {DEMO_ACCOUNTS.map((account) => (
                <button
                  key={account.email}
                  type="button"
                  data-testid={`demo-${account.label.toLowerCase().replaceAll(" ", "-")}`}
                  onClick={() => fillDemo(account)}
                  className="group rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-left transition hover:border-emerald-500/40 hover:bg-slate-900"
                >
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
            <p className="mt-2 text-[11px] text-slate-500">
              Click a card to prefill the form. Demo passwords follow the pattern Role@123.
            </p>
          </div>
        </section>

        {/* auth card */}
        <section className="w-full">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-6 shadow-2xl shadow-black/40 backdrop-blur">
            <div className="flex rounded-lg bg-slate-950/60 p-1 text-xs font-medium">
              {(["login", "register"] as Mode[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => {
                    setMode(value);
                    resetFeedback();
                  }}
                  className={`flex-1 rounded-md px-3 py-2 transition ${
                    mode === value
                      ? "bg-slate-800 text-white shadow"
                      : "text-slate-400 hover:text-slate-200"
                  }`}
                >
                  {value === "login" ? "Sign in" : "Create borrower account"}
                </button>
              ))}
            </div>

            <h2 className="mt-5 text-lg font-semibold text-white">
              {mode === "login" ? "Access your portal" : "Start your application"}
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
                className="w-full rounded-lg bg-emerald-500 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {busy
                  ? "Please wait…"
                  : mode === "login"
                    ? "Sign in"
                    : "Create account & continue"}
              </button>
            </form>

            <div className="mt-5 border-t border-slate-800 pt-4 text-[11px] leading-relaxed text-slate-500">
              <p className="font-medium text-slate-400">Role separation is enforced by the API</p>
              <p className="mt-1">
                A borrower token can only create and read its own application. Scoring requires a
                banker token, verified evidence and a server-side role check.
              </p>
            </div>
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
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-400">
        {label}
      </span>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        required={required}
        autoComplete={autoComplete}
        className="w-full rounded-lg border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500/40"
      />
    </label>
  );
}
