"use client";

/**
 * Bank officer underwriting dashboard.
 *
 * The officer's job is a sequence, and the UI mirrors it:
 *   1. pick a pending file from the queue
 *   2. reconcile each declaration and proof (preview the documents)
 *   3. calculate the credit decision - the only action that runs the model
 *
 * The API re-checks every step. Disabling a button here is convenience, not
 * security: `POST /api/banker/verify-and-score` refuses unverified evidence and
 * refuses any token that is not a banker.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/components/portal/AuthProvider";
import { DocumentPreviewModal } from "@/components/portal/DocumentPreviewModal";
import {
  ErrorAlert,
  PortalHeader,
  Spinner,
  StatusBadge,
} from "@/components/portal/PortalShell";
import {
  BankerDecisionModal,
  type DecisionModalView,
  type DeclineDecisionParams,
  type AcceptDecisionParams,
} from "@/components/portal/BankerDecisionModal";
import { ShapFactorList } from "@/components/portal/ShapFactorList";
import { RiskTierBadge } from "@/components/RiskTierBadge";
import { ScoreDial } from "@/components/ScoreDial";
import { formatDateTime, formatINR } from "@/lib/format";
import { portalApi, PortalApiError } from "@/lib/portal-api";
import type {
  ApplicationListResponse,
  ApplicationStatus,
  AuditEntry,
  BankerApplicationDetail,
  BankerApplicationSummary,
  DocumentProof,
  ScoreResult,
} from "@/lib/portal-types";

type QueueFilter = ApplicationStatus | "all";

const FILTERS: Array<{ value: QueueFilter; label: string }> = [
  { value: "pending", label: "Pending" },
  { value: "verified", label: "Verified" },
  { value: "scored", label: "Scored" },
  { value: "all", label: "All files" },
];

/** Declared fields the banker may verify, with display metadata. */
const ASSET_FIELDS: Array<{
  key: string;
  label: string;
  money?: boolean;
  suffix?: string;
}> = [
  { key: "land_acres", label: "Land holding", suffix: "acres" },
  { key: "land_market_value", label: "Land market value", money: true },
  { key: "soil_quality", label: "Soil quality", suffix: "/100" },
  { key: "irrigation_score", label: "Irrigation score", suffix: "/100" },
  { key: "crop_yield_val", label: "Crop value (12m)", money: true },
  { key: "livestock_val", label: "Livestock value", money: true },
  { key: "shop_inventory_val", label: "Inventory value", money: true },
  { key: "shop_area_sqft", label: "Shop area", suffix: "sq ft" },
  { key: "shop_ownership", label: "Shop premises" },
  { key: "property_val", label: "Property value", money: true },
  { key: "equipment_val", label: "Equipment / machinery", money: true },
  { key: "upi_volume", label: "Monthly UPI turnover", money: true },
  { key: "annual_income_proxy", label: "Annual income proxy", money: true },
  { key: "utility_score", label: "Utility payment score", suffix: "/100" },
  { key: "utility_delays_12m", label: "Utility delays (12m)" },
  { key: "avg_bank_balance", label: "Average bank balance", money: true },
];

function fieldValue(detail: BankerApplicationDetail, key: string): string {
  const assets = detail.assets;
  if (!assets) return "—";
  const value = (assets as unknown as Record<string, unknown>)[key];
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    const meta = ASSET_FIELDS.find((field) => field.key === key);
    if (meta?.money) return formatINR(value);
    return `${value.toLocaleString("en-IN")}${meta?.suffix ? ` ${meta.suffix}` : ""}`;
  }
  return String(value).replaceAll("_", " ");
}

export function BankerUnderwritingDashboard() {
  const { token, user, logout } = useAuth();
  const [filter, setFilter] = useState<QueueFilter>("pending");
  const [queue, setQueue] = useState<BankerApplicationSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loadingQueue, setLoadingQueue] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BankerApplicationDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set());
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [demoBusy, setDemoBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<string[]>([]);
  const [preview, setPreview] = useState<DocumentProof | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [history, setHistory] = useState<ScoreResult[]>([]);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [declining, setDeclining] = useState(false);
  const [declineReason, setDeclineReason] = useState("");
  const [declineBusy, setDeclineBusy] = useState(false);
  const [decisionModalView, setDecisionModalView] = useState<DecisionModalView>("none");
  const [holdSecondsLeft, setHoldSecondsLeft] = useState<number | null>(null);
  const [decisionActionBusy, setDecisionActionBusy] = useState(false);
  const [decisionSuccessNotice, setDecisionSuccessNotice] = useState<string | null>(null);
  const [recheckNotice, setRecheckNotice] = useState<string | null>(null);

  useEffect(() => {
    if (holdSecondsLeft === null) return;
    const interval = setInterval(() => {
      setHoldSecondsLeft((prev) => {
        if (prev === null) return null;
        if (prev <= 1) {
          clearInterval(interval);
          setDecisionModalView("gateway");
          return null;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [holdSecondsLeft]);

  const handleRecheck = () => {
    setDecisionModalView("none");
    setSelectedFields(new Set());
    setSelectedDocs(new Set());
    setRecheckNotice(
      "🔄 Recheck Active: All verified declaration and document checkmarks have been reset. Please manually verify all data again before recalculating.",
    );
  };

  const handleHold = () => {
    setDecisionModalView("none");
    setHoldSecondsLeft(15);
  };

  const resumeDecisionNow = () => {
    setHoldSecondsLeft(null);
    setDecisionModalView("gateway");
  };

  const dismissHold = () => {
    setHoldSecondsLeft(null);
  };

  const handleConfirmDecline = async (params: DeclineDecisionParams) => {
    if (!token || !detail) return;
    setDecisionActionBusy(true);
    setError(null);
    try {
      const fullReason = [
        `DECLINE CATEGORY: ${params.categoryLabel}`,
        params.adverseFactors.length > 0 ? `ADVERSE FACTORS: ${params.adverseFactors.join("; ")}` : "",
        `POLICY BASIS: ${params.policyRef}`,
        `OFFICER REMARKS: ${params.narrative}`,
      ]
        .filter(Boolean)
        .join("\n\n");

      await portalApi.verifyAndScore(token, detail.id, {
        verified_fields: [...selectedFields],
        document_ids: [...selectedDocs],
        officer_notes: notes.trim() ? `${notes.trim()}\n\n${fullReason}` : fullReason,
        decline_option: "decline",
        decline_reason: fullReason,
      });

      await loadDetail(detail.id);
      if (filter === "all") await loadQueue("all");
      else setFilter("all");

      setDecisionModalView("none");
      setDecisionSuccessNotice(
        `Application formally declined. Decline category (${params.categoryLabel}) and statutory reason recorded.`,
      );
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setDecisionActionBusy(false);
    }
  };

  const handleConfirmAccept = async (params: AcceptDecisionParams) => {
    if (!token || !detail) return;
    setDecisionActionBusy(true);
    setError(null);
    try {
      const fullSanctionRecord = [
        `=== IN-PRINCIPLE CREDIT SANCTION & APPROVAL ===`,
        `Sanctioned Loan Facility: ${params.facilityScheme}`,
        `Approved Amount: ₹${params.sanctionAmount.toLocaleString("en-IN")}`,
        `Annual Interest Rate: ${params.interestRate}% p.a.`,
        `Repayment Tenure: ${params.tenureMonths} Months`,
        `Underwriter Approval Justification: ${params.approvalRationale}`,
        ``,
        `=== OFFICIAL INSTRUCTIONS NOTE FOR BORROWER ===`,
        params.borrowerNote,
      ].join("\n");

      await portalApi.verifyAndScore(token, detail.id, {
        verified_fields: [...selectedFields],
        document_ids: [...selectedDocs],
        officer_notes: fullSanctionRecord,
      });

      await loadDetail(detail.id);
      if (filter === "all") await loadQueue("all");
      else setFilter("all");

      setDecisionModalView("none");
      setDecisionSuccessNotice(
        `✅ Credit facility of ₹${params.sanctionAmount.toLocaleString("en-IN")} sanctioned! Official note and branch instructions recorded for ${detail.applicant_name}.`,
      );
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setDecisionActionBusy(false);
    }
  };

  const handleApiError = useCallback(
    (caught: unknown) => {
      if (caught instanceof PortalApiError) {
        if (caught.status === 401) {
          logout();
          return;
        }
        setError(caught.message);
        setFieldErrors(caught.fields);
      } else {
        setError("Something went wrong. Please try again.");
        setFieldErrors([]);
      }
    },
    [logout],
  );

  // Loaders only update state after their requests resolve. The mount effects
  // below call `.then(callback)` (never `setState` synchronously), so mounting
  // does not cascade a render; explicit spinners start from user-event handlers.
  const applyQueue = useCallback((response: ApplicationListResponse) => {
    setQueue(response.items);
    setTotal(response.total);
    setError(null);
    setSelectedId((current) => {
      if (current && response.items.some((item) => item.id === current)) return current;
      return response.items[0]?.id ?? null;
    });
  }, []);

  const applyDetail = useCallback(
    (application: BankerApplicationDetail, trail: AuditEntry[], scores: ScoreResult[] = []) => {
      setDetail(application);
      setAudit(trail);
      setHistory(scores);
      setSelectedFields(new Set(application.assets?.verified_fields ?? []));
      setSelectedDocs(
        new Set(
          application.documents
            .filter((document) => document.verified_status === "verified")
            .map((document) => document.id),
        ),
      );
      setNotes("");
      setError(null);
      setFieldErrors([]);
    },
    [],
  );

  const loadQueue = useCallback(
    async (nextFilter: QueueFilter = filter) => {
      if (!token) return;
      try {
        applyQueue(await portalApi.bankerApplications(token, nextFilter));
      } catch (caught) {
        handleApiError(caught);
      } finally {
        setLoadingQueue(false);
      }
    },
    [token, filter, handleApiError, applyQueue],
  );

  const loadDetail = useCallback(
    async (id: string) => {
      if (!token) return;
      try {
        const [application, trail, scores] = await Promise.all([
          portalApi.bankerApplication(token, id),
          portalApi.auditTrail(token, id).catch(() => ({ items: [] as AuditEntry[] })),
          portalApi.scoreHistory(token, id).catch(() => [] as ScoreResult[]),
        ]);
        applyDetail(application, trail.items, scores);
      } catch (caught) {
        handleApiError(caught);
      } finally {
        setLoadingDetail(false);
      }
    },
    [token, handleApiError, applyDetail],
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    portalApi.bankerApplications(token, filter).then(
      (response) => {
        if (cancelled) return;
        applyQueue(response);
        setLoadingQueue(false);
      },
      (caught: unknown) => {
        if (cancelled) return;
        handleApiError(caught);
        setLoadingQueue(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [token, filter, applyQueue, handleApiError]);

  useEffect(() => {
    if (!selectedId || !token) return;
    let cancelled = false;
    Promise.all([
      portalApi.bankerApplication(token, selectedId),
      portalApi.auditTrail(token, selectedId).catch(() => ({ items: [] as AuditEntry[] })),
      portalApi.scoreHistory(token, selectedId).catch(() => [] as ScoreResult[]),
    ]).then(
      ([application, trail, scores]) => {
        if (cancelled) return;
        applyDetail(application, trail.items, scores);
        setLoadingDetail(false);
      },
      (caught: unknown) => {
        if (cancelled) return;
        handleApiError(caught);
        setLoadingDetail(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [selectedId, token, applyDetail, handleApiError]);

  const declaredFields = useMemo(() => {
    if (!detail?.assets) return [];
    return ASSET_FIELDS.filter((field) => {
      const value = (detail.assets as unknown as Record<string, unknown>)[field.key];
      if (value === null || value === undefined) return false;
      // "not_applicable" is a stored default, not evidence a banker can check.
      return value !== "not_applicable";
    });
  }, [detail]);

  const latest = detail?.latest_score ?? null;
  const canScore =
    !!detail &&
    detail.status !== "rejected" &&
    selectedFields.size > 0 &&
    selectedDocs.size > 0;

  const toggleField = (key: string) => {
    setSelectedFields((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const toggleDoc = (id: string) => {
    setSelectedDocs((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const calculateScore = async () => {
    if (!token || !detail) return;
    setBusy(true);
    setError(null);
    setFieldErrors([]);
    try {
      await portalApi.verifyAndScore(token, detail.id, {
        verified_fields: [...selectedFields],
        document_ids: [...selectedDocs],
        officer_notes: notes.trim() || null,
      });
      // Reload the file to get the append-only result and verification stamps.
      await loadDetail(detail.id);
      // A scored file leaves the "pending" queue. Switch to the full queue so
      // the decision the officer just produced stays on screen.
      if (filter === "all") await loadQueue("all");
      else setFilter("all");

      // Open the Decision Gateway Pop-out modal immediately upon score calculation!
      setDecisionModalView("gateway");
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setBusy(false);
    }
  };

  const downloadLetter = async () => {
    if (!token) {
      console.error("[DecisionLetter] No auth token available.");
      setError("Session expired. Please log in again.");
      return;
    }
    if (!detail || !detail.id) {
      console.error("[DecisionLetter] No application detail available. Ensure the application has been scored first.");
      setError("No application data available. Please select an application first.");
      return;
    }
    setBusy(true);
    try {
      const url = await portalApi.decisionLetter(token, detail.id);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `decision-${detail.id.slice(0, 8)}.pdf`;
      anchor.style.display = "none";
      document.body.appendChild(anchor);
      anchor.click();
      // Keep the anchor in the DOM briefly so the browser has time to start the download.
      await new Promise((resolve) => setTimeout(resolve, 500));
      anchor.remove();
      // Revoke the blob URL after a delay so the browser has finished downloading.
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
    } catch (caught) {
      console.error("[DecisionLetter] Failed to download decision letter:", caught);
      handleApiError(caught);
    } finally {
      setBusy(false);
    }
  };

  const reject = async () => {
    if (!token || !detail) return;
    if (rejectReason.trim().length < 5) {
      setError("Provide a rejection reason of at least 5 characters.");
      return;
    }
    setBusy(true);
    try {
      await portalApi.reject(token, detail.id, rejectReason.trim());
      await loadDetail(detail.id);
      if (filter === "all") await loadQueue("all");
      else setFilter("all");
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setBusy(false);
    }
  };

  const startReject = () => setRejecting(true);

  const decline = async () => {
    if (!token || !detail) return;
    if (!declineReason.trim()) {
      setError("Add a decline reason before declining.");
      return;
    }
    setDeclineBusy(true);
    try {
      await portalApi.verifyAndScore(token, detail.id, {
        verified_fields: [...selectedFields],
        document_ids: [...selectedDocs],
        officer_notes: notes.trim() || null,
        decline_option: "decline",
        decline_reason: declineReason.trim(),
      });
      await loadDetail(detail.id);
      if (filter === "all") await loadQueue("all");
      else setFilter("all");
      setDeclineReason("");
      setDeclining(false);
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setDeclineBusy(false);
    }
  };

  const loadDemo = async () => {
    if (!token) return;
    setDemoBusy(true);
    setError(null);
    try {
      const application = await portalApi.createDemoApplication(token);
      setFilter("all");
      await loadQueue("all");
      setSelectedId(application.id);
    } catch (caught) {
      handleApiError(caught);
    } finally {
      setDemoBusy(false);
    }
  };

  if (!token || !user) return null;

  return (
    <div className="min-h-screen bg-slate-100">
      <PortalHeader
        eyebrow="Banker portal"
        title="Underwriting desk"
        subtitle="Verify evidence, then run the explainable scorecard."
        actions={
          <div className="flex items-center gap-1.5">
            <select
              value={filter}
              onChange={(event) => {
                setLoadingQueue(true);
                setFilter(event.target.value as QueueFilter);
              }}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-[11px] font-medium text-slate-200"
              aria-label="Filter applications"
            >
              {FILTERS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => {
                setLoadingQueue(true);
                void loadQueue();
              }}
              className="rounded-lg border border-slate-700 px-2.5 py-1.5 text-[11px] font-medium text-slate-200 transition hover:bg-slate-800"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={() => void loadDemo()}
              disabled={demoBusy}
              data-testid="load-demo"
              className="rounded-lg bg-emerald-500 px-2.5 py-1.5 text-[11px] font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-60"
            >
              {demoBusy ? "Creating…" : "Load demo file"}
            </button>
          </div>
        }
      />

      <main className="mx-auto max-w-[1500px] px-4 py-4">
        {error && (
          <div className="mb-3">
            <ErrorAlert
              message={error}
              fields={fieldErrors}
              onDismiss={() => {
                setError(null);
                setFieldErrors([]);
              }}
            />
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
          {/* queue */}
          <section className="xl:col-span-5">
            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">Application queue</h2>
                  <p className="text-[11px] text-slate-500">
                    {total} {filter === "all" ? "file" : filter} file{total === 1 ? "" : "s"} ·
                    click a row to review
                  </p>
                </div>
                {loadingQueue && <Spinner />}
              </div>

              {queue.length === 0 && !loadingQueue ? (
                <div className="p-8 text-center">
                  <p className="text-sm font-medium text-slate-700">Nothing in this queue</p>
                  <p className="mx-auto mt-1 max-w-xs text-xs leading-relaxed text-slate-500">
                    Submit a borrower application, or click <strong>Load demo file</strong> to
                    create a pending file with PDF proofs.
                  </p>
                </div>
              ) : (
                <div className="max-h-[calc(100vh-190px)] overflow-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="sticky top-0 z-10 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-3 py-2 font-semibold">Applicant</th>
                        <th className="px-3 py-2 font-semibold">Ask</th>
                        <th className="px-3 py-2 font-semibold">Evidence</th>
                        <th className="px-3 py-2 font-semibold">Status</th>
                        <th className="px-3 py-2 text-right font-semibold">Score</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {queue.map((item) => (
                        <tr
                          key={item.id}
                          data-testid={`queue-row-${item.id}`}
                          onClick={() => {
                            setLoadingDetail(true);
                            setSelectedId(item.id);
                            setDecisionModalView("none");
                            setHoldSecondsLeft(null);
                            setRecheckNotice(null);
                            setDecisionSuccessNotice(null);
                          }}
                          className={`cursor-pointer transition ${
                            selectedId === item.id
                              ? "bg-emerald-50/70"
                              : "hover:bg-slate-50"
                          }`}
                        >
                          <td className="px-3 py-2.5">
                            <p className="font-semibold text-slate-800">{item.applicant_name}</p>
                            <p className="text-[10px] text-slate-500">
                              {item.applicant_category === "farmer" ? "Farmer" : "Shop / MSME"} ·{" "}
                              {item.region_tier.replace("_", " ")}
                            </p>
                          </td>
                          <td className="px-3 py-2.5 tabular-nums text-slate-700">
                            {formatINR(item.requested_loan_amount)}
                          </td>
                          <td className="px-3 py-2.5">
                            <p className="text-[10px] text-slate-600">
                              {item.verified_document_count}/{item.document_count} docs
                            </p>
                            <p className="text-[10px] text-slate-400">
                              {item.verified_field_count} fields verified
                            </p>
                          </td>
                          <td className="px-3 py-2.5">
                            <StatusBadge status={item.status} size="sm" />
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            {item.credit_score !== null ? (
                              <span className="font-semibold tabular-nums text-slate-800">
                                {item.credit_score}
                              </span>
                            ) : (
                              <span className="text-slate-300">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="mt-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-[11px] leading-relaxed text-slate-500">
              <p className="font-semibold text-slate-700">Scoring is banker-only</p>
              A borrower token cannot reach <code className="font-mono text-[10px]">/api/banker/*</code>.
              The model also refuses to run until at least one declaration and one document are
              verified.
            </div>
          </section>

          {/* detail */}
          <section className="xl:col-span-7">
            {!detail ? (
              <div className="flex h-full min-h-[300px] items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white/60 p-8 text-center">
                <div>
                  <p className="text-sm font-medium text-slate-700">Select an application</p>
                  <p className="mt-1 text-xs text-slate-500">
                    Review the declarations, preview the proofs, then calculate the score.
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Recheck Active Notice */}
                {recheckNotice && (
                  <div
                    data-testid="recheck-active-notice"
                    className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-amber-900 shadow-sm flex items-start justify-between gap-3 animate-in fade-in"
                  >
                    <div className="flex items-start gap-2.5">
                      <span className="text-xl">🔄</span>
                      <div>
                        <p className="text-xs font-bold uppercase tracking-wider text-amber-800">
                          Manual Recheck Mode Active
                        </p>
                        <p className="text-xs text-amber-900/90 mt-0.5 leading-relaxed">
                          All declaration and document approvals have been reset. Please manually verify
                          each declared asset and uploaded proof below before recalculating the credit
                          score.
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setRecheckNotice(null)}
                      className="text-xs font-semibold text-amber-700 hover:text-amber-950 shrink-0"
                    >
                      ✕ Dismiss
                    </button>
                  </div>
                )}

                {/* Decision Success Notice */}
                {decisionSuccessNotice && (
                  <div
                    data-testid="decision-success-notice"
                    className="rounded-2xl border border-emerald-300 bg-emerald-50 p-4 text-emerald-950 shadow-sm flex items-start justify-between gap-3 animate-in fade-in"
                  >
                    <div className="flex items-start gap-2.5">
                      <span className="text-xl">🎉</span>
                      <div>
                        <p className="text-xs font-bold uppercase tracking-wider text-emerald-800">
                          Underwriting Decision Recorded
                        </p>
                        <p className="text-xs text-emerald-900 mt-0.5 leading-relaxed">
                          {decisionSuccessNotice}
                        </p>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setDecisionSuccessNotice(null)}
                      className="text-xs font-semibold text-emerald-700 hover:text-emerald-950 shrink-0"
                    >
                      ✕ Dismiss
                    </button>
                  </div>
                )}

                {/* file header */}
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base font-semibold text-slate-900">
                          {detail.applicant_name}
                        </h2>
                        <StatusBadge status={detail.status} size="sm" />
                        {latest && <RiskTierBadge tier={latest.risk_tier} size="sm" />}
                      </div>
                      <p className="mt-0.5 text-[11px] text-slate-500">
                        {detail.applicant_email} ·{" "}
                        {detail.applicant_category === "farmer" ? "Farmer" : "Shop / MSME"} ·{" "}
                        {detail.region_tier.replace("_", " ")} · submitted{" "}
                        {formatDateTime(detail.submitted_at)}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-[10px] uppercase tracking-wide text-slate-400">Requested</p>
                      <p className="text-lg font-semibold tabular-nums text-slate-900">
                        {formatINR(detail.requested_loan_amount)}
                      </p>
                      <p className="text-[10px] text-slate-500">{detail.loan_tenure_months} months</p>
                    </div>
                  </div>

                  <dl className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
                    <MiniStat label="Existing debt" value={formatINR(detail.outstanding_debt)} />
                    <MiniStat label="Current EMI" value={formatINR(detail.monthly_emi_obligation)} />
                    <MiniStat
                      label="Cash-flow proxies"
                      value={`${detail.assets?.upi_volume ? formatINR(detail.assets.upi_volume) : "—"} UPI`}
                    />
                    <MiniStat
                      label="Collateral declared"
                      value={formatINR(detail.assets?.declared_total_asset_value ?? 0)}
                    />
                  </dl>

                  {detail.rejection_reason && (
                    <p className="mt-3 rounded-lg bg-rose-50 p-2.5 text-[11px] text-rose-700">
                      Rejected: {detail.rejection_reason}
                    </p>
                  )}
                </div>

                {loadingDetail && (
                  <div className="flex items-center gap-2 rounded-xl bg-white px-4 py-3 text-xs text-slate-500">
                    <Spinner /> Refreshing file…
                  </div>
                )}

                <div className="grid grid-cols-1 gap-3 2xl:grid-cols-2">
                  {/* evidence */}
                  <div className="space-y-3">
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">Declared assets</h3>
                          <p className="text-[11px] text-slate-500">
                            Tick each value reconciled against a proof.
                          </p>
                        </div>
                      </div>

                      {declaredFields.length === 0 ? (
                        <p className="mt-3 text-xs text-slate-500">No declarations on file.</p>
                      ) : (
                        <ul className="mt-2 divide-y divide-slate-100">
                          {declaredFields.map((field) => {
                            const alreadyVerified = (detail.assets?.verified_fields ?? []).includes(
                              field.key,
                            );
                            return (
                              <li key={field.key}>
                                <label className="flex cursor-pointer items-center gap-2 py-1.5">
                                  <input
                                    type="checkbox"
                                    data-testid={`verify-field-${field.key}`}
                                    checked={selectedFields.has(field.key)}
                                    onChange={() => toggleField(field.key)}
                                    className="h-3.5 w-3.5 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                                  />
                                  <span className="flex-1 text-[11px] text-slate-600">
                                    {field.label}
                                  </span>
                                  {alreadyVerified && (
                                    <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                                      verified
                                    </span>
                                  )}
                                  <span className="min-w-[92px] text-right text-[11px] font-medium tabular-nums text-slate-800">
                                    {fieldValue(detail, field.key)}
                                  </span>
                                </label>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>

                    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">Document proofs</h3>
                          <p className="text-[11px] text-slate-500">
                            Preview the file, then confirm it matches the declaration.
                          </p>
                        </div>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                          {selectedDocs.size}/{detail.documents.length} verified
                        </span>
                      </div>

                      {detail.documents.length === 0 ? (
                        <p className="mt-3 rounded-lg bg-amber-50 p-2 text-[11px] text-amber-800">
                          No documents uploaded. Scoring is blocked until at least one proof is
                          verified.
                        </p>
                      ) : (
                        <ul className="mt-2 space-y-2">
                          {detail.documents.map((document) => (
                            <li
                              key={document.id}
                              className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 p-2.5"
                            >
                              <input
                                type="checkbox"
                                checked={selectedDocs.has(document.id)}
                                onChange={() => toggleDoc(document.id)}
                                className="h-3.5 w-3.5 rounded border-slate-300 text-emerald-600"
                                aria-label={`Verify ${document.original_filename}`}
                                data-testid={`verify-doc-${document.id}`}
                              />
                              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-[9px] font-bold uppercase text-slate-500">
                                {document.original_filename.split(".").pop()?.slice(0, 4)}
                              </span>
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-[11px] font-medium text-slate-800">
                                  {document.original_filename}
                                </p>
                                <p className="text-[10px] text-slate-500">
                                  {document.doc_type.replaceAll("_", " ")} ·{" "}
                                  {(document.size_bytes / 1024).toFixed(0)} KB ·{" "}
                                  {document.verified_status}
                                </p>
                              </div>
                              <button
                                type="button"
                                onClick={() => setPreview(document)}
                                data-testid={`preview-doc-${document.id}`}
                                className="rounded-md border border-slate-200 px-2 py-1 text-[10px] font-medium text-slate-600 transition hover:bg-slate-50"
                              >
                                Preview
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </div>

                  {/* decision */}
                  <div className="space-y-3">
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">Credit decision</h3>
                          <p className="text-[11px] text-slate-500">
                            XGBoost + SHAP · generated inside the bank portal
                          </p>
                        </div>
                        {latest && (
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                            {latest.model_version}
                          </span>
                        )}
                      </div>

                      {latest ? (
                        <ScorePanel result={latest} />
                      ) : (
                        <div className="mt-4 rounded-xl border border-dashed border-slate-300 p-4">
                          <p className="text-xs font-medium text-slate-700">
                            No score calculated yet
                          </p>
                          <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
                            Verify at least one declaration and one document, then calculate.
                            The API will refuse the request otherwise.
                          </p>
                          <ul className="mt-2 space-y-1 text-[11px]">
                            <li className={selectedFields.size > 0 ? "text-emerald-700" : "text-slate-500"}>
                              {selectedFields.size > 0 ? "✓" : "○"} {selectedFields.size} declaration
                              field{selectedFields.size === 1 ? "" : "s"} selected
                            </li>
                            <li className={selectedDocs.size > 0 ? "text-emerald-700" : "text-slate-500"}>
                              {selectedDocs.size > 0 ? "✓" : "○"} {selectedDocs.size} document
                              {selectedDocs.size === 1 ? "" : "s"} verified
                            </li>
                          </ul>
                        </div>
                      )}

                      <label className="mt-3 block">
                        <span className="mb-1 block text-[10px] font-medium uppercase tracking-wide text-slate-500">
                          Officer notes (stored with the decision)
                        </span>
                        <textarea
                          value={notes}
                          onChange={(event) => setNotes(event.target.value)}
                          rows={2}
                          placeholder="e.g. Land record matched the RoR; inventory counted on site."
                          className="w-full rounded-lg border border-slate-300 px-2.5 py-2 text-xs text-slate-800 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500/30"
                        />
                      </label>

                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={() => void calculateScore()}
                          disabled={!canScore || busy}
                          data-testid="calculate-score"
                          className="flex-1 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:bg-slate-300"
                        >
                          {busy
                            ? "Running model…"
                            : latest
                              ? "Recalculate score"
                              : "Verify & calculate score"}
                        </button>
                        {latest && (
                          <button
                            type="button"
                            onClick={() => setDecisionModalView("gateway")}
                            data-testid="decision-actions-button"
                            className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-indigo-500 shadow-sm"
                            title="Open Decision Gateway (Recheck, Hold, Decline, Accept & Send)"
                          >
                            Decision actions ⚡
                          </button>
                        )}
                        {latest && (
                          <button
                            type="button"
                            onClick={() => setDecisionModalView("accept")}
                            data-testid="accept-and-send-button"
                            className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800 transition hover:bg-emerald-100"
                            title="Sanction credit facility and issue official borrower note"
                          >
                            Accept & Send
                          </button>
                        )}
                        {latest && (
                          <button
                            type="button"
                            onClick={() => void downloadLetter()}
                            disabled={busy}
                            data-testid="download-decision-letter"
                            className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
                            title="Download a PDF decision letter for this application"
                          >
                            Decision letter
                          </button>
                        )}
                        {detail.status !== "rejected" && detail.status !== "scored" && (
                          <button
                            type="button"
                            onClick={() => void startReject()}
                            className="rounded-lg border border-rose-200 px-3 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
                          >
                            Reject
                          </button>
                        )}
                        {latest && (
                          <button
                            type="button"
                            onClick={() => setDecisionModalView("decline")}
                            data-testid="decline-button"
                            className="rounded-lg border border-rose-200 px-3 py-2 text-xs font-medium text-rose-700 transition hover:bg-rose-50"
                          >
                            Decline
                          </button>
                        )}
                      </div>

                      {!canScore && !latest && (
                        <p className="mt-2 text-[10px] text-slate-500">
                          Select at least one declaration and one document to enable scoring.
                        </p>
                      )}

                      {rejecting && (
                        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3">
                          <label className="block text-[10px] font-medium uppercase tracking-wide text-rose-700">
                            Rejection reason (required)
                          </label>
                          <textarea
                            value={rejectReason}
                            onChange={(event) => setRejectReason(event.target.value)}
                            rows={2}
                            placeholder="Why the file cannot proceed…"
                            className="mt-1 w-full rounded-lg border border-rose-200 bg-white px-2 py-1.5 text-xs text-slate-800"
                          />
                          <div className="mt-2 flex gap-2">
                            <button
                              type="button"
                              onClick={() => void reject()}
                              disabled={busy}
                              className="rounded-lg bg-rose-600 px-2.5 py-1.5 text-[11px] font-semibold text-white transition hover:bg-rose-500 disabled:opacity-60"
                            >
                              Confirm rejection
                            </button>
                            <button
                              type="button"
                              onClick={() => setRejecting(false)}
                              className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-rose-700"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}

                      {declining && (
                        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3">
                          <label className="block text-[10px] font-medium uppercase tracking-wide text-rose-700">
                            Decline reason (required)
                          </label>
                          <textarea
                            value={declineReason}
                            onChange={(event) => setDeclineReason(event.target.value)}
                            rows={2}
                            placeholder="Why the bank cannot approve this file…"
                            className="mt-1 w-full rounded-lg border border-rose-200 bg-white px-2 py-1.5 text-xs text-slate-800"
                          />
                          <div className="mt-2 flex gap-2">
                            <button
                              type="button"
                              onClick={() => void decline()}
                              disabled={busy || declineBusy}
                              className="rounded-lg bg-rose-600 px-2.5 py-1.5 text-[11px] font-semibold text-white transition hover:bg-rose-500 disabled:opacity-60"
                            >
                              Confirm decline
                            </button>
                            <button
                              type="button"
                              onClick={() => setDeclining(false)}
                              className="rounded-lg px-2.5 py-1.5 text-[11px] font-medium text-rose-700"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      )}

                    </div>

                    {latest && (
                      <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                        <ShapFactorList explainability={latest.explainability} />
                      </div>
                    )}

                    {history.length > 0 && (
                      <details
                        className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
                        data-testid="decision-history"
                      >
                        <summary className="cursor-pointer text-xs font-semibold text-slate-700">
                          Decision history ({history.length})
                        </summary>
                        <ol className="mt-2 space-y-1.5">
                          {history.map((entry) => (
                            <li
                              key={entry.id}
                              className="flex items-start justify-between gap-2 text-[10px]"
                            >
                              <span className="text-slate-600">
                                {entry.credit_score} · {entry.risk_tier} ·{" "}
                                {formatINR(entry.recommended_limit)}
                              </span>
                              <span className="shrink-0 text-slate-400">
                                {formatDateTime(entry.generated_at)}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}

                    {audit.length > 0 && (
                      <details className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                        <summary className="cursor-pointer text-xs font-semibold text-slate-700">
                          Audit trail ({audit.length})
                        </summary>
                        <ol className="mt-2 space-y-1.5">
                          {audit.slice(0, 8).map((entry) => (
                            <li key={entry.id} className="flex items-start justify-between gap-2 text-[10px]">
                              <span className="text-slate-600">
                                {entry.action.replaceAll("_", " ")}
                                {entry.actor_role ? ` · ${entry.actor_role}` : ""}
                              </span>
                              <span className="shrink-0 text-slate-400">
                                {formatDateTime(entry.created_at)}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </main>

      {preview && token && (
        <DocumentPreviewModal token={token} document={preview} onClose={() => setPreview(null)} />
      )}

      {/* Floating Hold Countdown Pill */}
      {holdSecondsLeft !== null && (
        <div
          data-testid="hold-floating-banner"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-3 rounded-2xl border border-sky-400/40 bg-slate-900/95 px-5 py-3.5 text-white shadow-2xl backdrop-blur-md animate-in slide-in-from-bottom-4"
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-sky-500/20 text-sky-300 font-bold border border-sky-500/30">
            ⏸️
          </div>
          <div>
            <p className="text-xs font-semibold text-sky-100 flex items-center gap-1.5">
              Decision on Hold
              <span className="rounded-full bg-sky-500/20 px-2 py-0.5 text-[10px] font-bold text-sky-300 tabular-nums border border-sky-500/30">
                {holdSecondsLeft}s
              </span>
            </p>
            <p className="text-[11px] text-slate-400">Score remains on screen. Modal reopens automatically.</p>
          </div>
          <div className="flex items-center gap-2 ml-2">
            <button
              type="button"
              data-testid="resume-hold-now"
              onClick={resumeDecisionNow}
              className="rounded-xl bg-sky-500 px-3 py-1.5 text-xs font-semibold text-slate-950 transition hover:bg-sky-400 active:scale-95"
            >
              Resume now
            </button>
            <button
              type="button"
              onClick={dismissHold}
              className="rounded-xl border border-slate-700 px-2.5 py-1.5 text-xs text-slate-400 hover:text-white transition"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Underwriting Decision Pop-Out Modal */}
      {detail && latest && (
        <BankerDecisionModal
          isOpen={decisionModalView !== "none"}
          view={decisionModalView}
          onClose={() => setDecisionModalView("none")}
          onSelectView={setDecisionModalView}
          detail={detail}
          latest={latest}
          onRecheck={handleRecheck}
          onHold={handleHold}
          onConfirmDecline={handleConfirmDecline}
          onConfirmAccept={handleConfirmAccept}
          isSubmitting={decisionActionBusy}
        />
      )}
    </div>
  );
}

function ScorePanel({ result }: { result: ScoreResult }) {
  const policy = result.policy_breakdown;
  return (
    <div className="mt-3" data-testid="score-panel">
      <ScoreDial
        score={result.credit_score}
        band={result.score_band}
        tier={result.risk_tier}
        probabilityOfDefault={result.probability_of_default}
      />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 p-3">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-slate-400">Risk tier</p>
          <div className="mt-1">
            <RiskTierBadge tier={result.risk_tier} label={result.tier_label} />
          </div>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-wide text-slate-400">Recommended limit</p>
          <p className="text-base font-semibold tabular-nums text-slate-900">
            {formatINR(result.recommended_limit)}
          </p>
          <p className="text-[10px] text-rose-600">
            {result.decision.replaceAll("_", " ")}
          </p>
        </div>
      </div>

      <dl className="mt-2 grid grid-cols-2 gap-2 text-[10px]">
        <MiniStat label="PD" value={`${(result.probability_of_default * 100).toFixed(1)}%`} />
        <MiniStat
          label="Binding cap"
          value={(policy.binding_constraint ?? "—").replace("_", " ")}
        />
        <MiniStat label="Collateral cap" value={formatINR(policy.collateral_cap ?? 0)} />
        <MiniStat label="Income cap" value={formatINR(policy.income_cap ?? 0)} />
        <MiniStat
          label="Rate band"
          value={
            policy.indicative_rate_band
              ? `${policy.indicative_rate_band.min_annual_rate}–${policy.indicative_rate_band.max_annual_rate}%`
              : "—"
          }
        />
        <MiniStat
          label="Scored at"
          value={formatDateTime(result.generated_at)}
        />
      </dl>

      {result.data_quality?.warnings && result.data_quality.warnings.length > 0 && (
        <ul className="mt-2 space-y-1 rounded-lg bg-amber-50 p-2 text-[10px] text-amber-800">
          {result.data_quality.warnings.map((warning) => (
            <li key={warning}>• {warning}</li>
          ))}
        </ul>
      )}

      {result.officer_notes && (
        <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-[11px]">
          <p className="font-semibold text-slate-700 uppercase tracking-wide text-[9px] mb-1">
            Officer Underwriting Record & Sanction Notes
          </p>
          <pre className="whitespace-pre-wrap font-sans text-slate-600 leading-relaxed text-[11px]">
            {result.officer_notes}
          </pre>
        </div>
      )}

      {result.decline_reason && (
        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-[11px] text-rose-800">
          <p className="font-semibold uppercase tracking-wide text-[9px] mb-1 text-rose-700">
            Adverse Decline Reason Recorded
          </p>
          <pre className="whitespace-pre-wrap font-sans leading-relaxed text-[11px]">
            {result.decline_reason}
          </pre>
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-2 py-1.5">
      <dt className="text-[9px] uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 truncate text-[11px] font-semibold text-slate-800">{value}</dd>
    </div>
  );
}
