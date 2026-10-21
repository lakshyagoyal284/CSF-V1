"use client";

/** Borrower portal: submit an application and track its verification status. */

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/portal/AuthProvider";
import { BorrowerApplicationForm } from "@/components/portal/BorrowerApplicationForm";
import {
  ErrorAlert,
  PortalHeader,
  Spinner,
  StatusBadge,
} from "@/components/portal/PortalShell";
import { formatDateTime, formatINR } from "@/lib/format";
import { portalApi, PortalApiError } from "@/lib/portal-api";
import type { BorrowerApplication } from "@/lib/portal-types";

const STATUS_COPY: Record<BorrowerApplication["status"], string> = {
  pending: "Waiting for a bank officer to reconcile your proofs.",
  verified: "Evidence verified. The bank can now calculate the credit decision.",
  scored: "The bank has completed its assessment. The decision is shared through your branch.",
  rejected: "The bank could not proceed with this file. See the reason below.",
};

export function BorrowerPortal() {
  const { token, user, logout } = useAuth();
  const [applications, setApplications] = useState<BorrowerApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      // State is only updated after the request resolves, never synchronously
      // during the mount effect.
      const list = await portalApi.myApplications(token);
      setApplications(list);
      setError(null);
    } catch (caught) {
      if (caught instanceof PortalApiError && caught.status === 401) {
        logout();
        return;
      }
      setError(
        caught instanceof PortalApiError
          ? caught.message
          : "Could not load your applications.",
      );
    } finally {
      setLoading(false);
    }
  }, [token, logout]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    // `.then` callbacks are asynchronous, so the mount does not cascade a
    // synchronous state update (which the react-hooks lint rule forbids).
    portalApi.myApplications(token).then(
      (list) => {
        if (cancelled) return;
        setApplications(list);
        setError(null);
        setLoading(false);
      },
      (caught: unknown) => {
        if (cancelled) return;
        if (caught instanceof PortalApiError && caught.status === 401) {
          logout();
          return;
        }
        setError(
          caught instanceof PortalApiError
            ? caught.message
            : "Could not load your applications.",
        );
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, logout]);

  if (!token || !user) return null;

  return (
    <div className="min-h-screen bg-slate-100">
      <PortalHeader
        eyebrow="Borrower portal"
        title="Your credit application"
        subtitle="Declare your assets, upload proofs, and track verification."
      />

      <main className="mx-auto grid max-w-[1440px] grid-cols-1 gap-4 px-4 py-5 lg:grid-cols-12">
        <section className="lg:col-span-7 xl:col-span-8">
          <BorrowerApplicationForm token={token} onSubmitted={refresh} />
        </section>

        <aside className="space-y-4 lg:col-span-5 xl:col-span-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">My applications</h2>
                <p className="text-[11px] text-slate-500">
                  You can see status and documents. Scores are visible to the bank only.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setLoading(true);
                  void refresh();
                }}
                className="rounded-lg border border-slate-200 px-2 py-1 text-[11px] font-medium text-slate-600 transition hover:bg-slate-50"
              >
                Refresh
              </button>
            </div>

            {error && (
              <div className="mt-3">
                <ErrorAlert message={error} onDismiss={() => setError(null)} />
              </div>
            )}

            {loading ? (
              <div className="mt-4 flex items-center gap-2 text-xs text-slate-500">
                <Spinner /> Loading your files…
              </div>
            ) : applications.length === 0 ? (
              <div className="mt-4 rounded-xl border border-dashed border-slate-300 p-5 text-center">
                <p className="text-xs font-medium text-slate-600">No applications yet</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  Complete the form to send your first file for verification.
                </p>
              </div>
            ) : (
              <ul className="mt-3 space-y-3">
                {applications.map((application) => (
                  <li key={application.id} className="rounded-xl border border-slate-200 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-xs font-semibold text-slate-800">
                          {formatINR(application.requested_loan_amount)} ·{" "}
                          {application.loan_tenure_months} months
                        </p>
                        <p className="mt-0.5 font-mono text-[10px] text-slate-400">
                          #{application.id.slice(0, 8)} · {formatDateTime(application.submitted_at)}
                        </p>
                      </div>
                      <StatusBadge status={application.status} size="sm" />
                    </div>

                    <p className="mt-2 text-[11px] leading-relaxed text-slate-600">
                      {STATUS_COPY[application.status]}
                    </p>

                    {application.rejection_reason && (
                      <p className="mt-2 rounded-lg bg-rose-50 p-2 text-[11px] text-rose-700">
                        {application.rejection_reason}
                      </p>
                    )}

                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                        {application.documents.length} document
                        {application.documents.length === 1 ? "" : "s"}
                      </span>
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                        {application.applicant_category === "farmer" ? "Farmer" : "Shop / MSME"}
                      </span>
                      {application.documents.some((doc) => doc.verified_status === "verified") && (
                        <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700">
                          Proofs verified
                        </span>
                      )}
                    </div>

                    {application.documents.length > 0 && (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-[11px] font-medium text-slate-500 hover:text-slate-700">
                          View documents
                        </summary>
                        <ul className="mt-1.5 space-y-1">
                          {application.documents.map((document) => (
                            <li key={document.id} className="flex items-center justify-between gap-2 text-[10px]">
                              <span className="truncate text-slate-600">{document.original_filename}</span>
                              <span
                                className={
                                  document.verified_status === "verified"
                                    ? "text-emerald-600"
                                    : "text-slate-400"
                                }
                              >
                                {document.verified_status}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-4 text-[11px] leading-relaxed text-slate-500 shadow-sm">
            <p className="text-xs font-semibold text-slate-800">How this works</p>
            <ol className="mt-2 space-y-1.5">
              <li>
                <span className="font-semibold text-slate-700">1. Submit</span> - declarations and
                proofs are stored against a pending application.
              </li>
              <li>
                <span className="font-semibold text-slate-700">2. Verify</span> - a bank officer
                checks each value against your documents.
              </li>
              <li>
                <span className="font-semibold text-slate-700">3. Score</span> - the bank runs the
                credit model. The score, reasons and limit stay inside the bank portal.
              </li>
            </ol>
          </div>
        </aside>
      </main>
    </div>
  );
}
