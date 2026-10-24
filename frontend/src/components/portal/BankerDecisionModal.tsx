"use client";

import { useEffect, useId, useState } from "react";
import { formatINR } from "@/lib/format";
import type { BankerApplicationDetail, ScoreResult } from "@/lib/portal-types";

export type DecisionModalView = "gateway" | "decline" | "accept" | "none";

export interface DeclineDecisionParams {
  category: string;
  categoryLabel: string;
  adverseFactors: string[];
  narrative: string;
  policyRef: string;
}

export interface AcceptDecisionParams {
  sanctionAmount: number;
  interestRate: number;
  tenureMonths: number;
  facilityScheme: string;
  approvalRationale: string;
  borrowerNote: string;
}

interface BankerDecisionModalProps {
  isOpen: boolean;
  view: DecisionModalView;
  onClose: () => void;
  onSelectView: (view: DecisionModalView) => void;
  detail: BankerApplicationDetail;
  latest: ScoreResult;
  onRecheck: () => void;
  onHold: () => void;
  onConfirmDecline: (params: DeclineDecisionParams) => Promise<void>;
  onConfirmAccept: (params: AcceptDecisionParams) => Promise<void>;
  isSubmitting: boolean;
}

const DECLINE_CATEGORIES = [
  {
    id: "insufficient_cashflow",
    label: "Insufficient Cash-Flow & Debt Service Coverage (DSCR < 1.25)",
  },
  {
    id: "excessive_debt",
    label: "High Leverage / Existing Debt Overhang Exceeds FOIR Limit",
  },
  {
    id: "unverified_collateral",
    label: "Discrepancy in Declared Land Records / Collateral Title Defect",
  },
  {
    id: "low_credit_score",
    label: "Calculated Score Below Minimum Policy Threshold for Risk Tier",
  },
  {
    id: "utility_delinquency",
    label: "Recurrent Operational or Utility Delinquencies in Past 12 Months",
  },
  {
    id: "unregistered_premises",
    label: "Shop Premises Tenancy / Trade License Mismatch or Defect",
  },
  {
    id: "regulatory_compliance",
    label: "Regulatory Non-Compliance under Priority Sector Lending Norms",
  },
];

const ADVERSE_FACTORS_LIST = [
  "Debt-to-Income / FOIR exceeds 60%",
  "Discrepancy between declared revenue and UPI receipts",
  "Land revenue records (RoR / 7/12) title dispute or unverified encumbrance",
  "Utility payment delay count > 3 in trailing 12 months",
  "Declared inventory / machinery valuation unsupported by physical inspection",
  "Shop establishment license expired or premises lease non-registered",
  "Crop climate risk & regional yield volatility index high",
  "Net average bank balance insufficient to service proposed EMI",
];

const FARMER_SCHEMES = [
  "Kisan Credit Card (KCC) - Crop Cultivation Facility",
  "Agri Term Loan - Farm Mechanization & Irrigation",
  "Agricultural Infrastructure & Land Development Credit",
  "Dairy & Animal Husbandry Working Capital Facility",
  "Solar Pump & Green Agri Equipment Scheme",
];

const SHOP_SCHEMES = [
  "Pradhan Mantri MUDRA Yojana (Kishor / Tarun)",
  "MSME Secured Working Capital Facility",
  "Shop Inventory & Trade Receivables Credit Line",
  "Micro Enterprise Equipment & Technology Upgradation",
  "CGTMSE Collateral-Free Micro Credit",
];

function generateDefaultBorrowerNote(
  applicantName: string,
  category: string,
  sanctionAmount: number,
  interestRate: number,
  tenureMonths: number,
  facilityScheme: string,
): string {
  const isFarmer = category === "farmer";
  return `Dear ${applicantName},

Congratulations! We are pleased to inform you that your credit application has received IN-PRINCIPLE SANCTION from our Underwriting Desk.

SANCTION HIGHLIGHTS:
• Approved Loan Facility: ${facilityScheme}
• Sanctioned Credit Limit: ₹${sanctionAmount.toLocaleString("en-IN")}
• Applicable Interest Rate: ${interestRate}% p.a.
• Repayment Tenure: ${tenureMonths} Months

NEXT STEPS TO INITIATE LOAN DISBURSEMENT:
If you wish to initiate and avail this loan facility, you are cordially requested to visit our nearest bank branch in person to complete document verification and sign the loan agreement.

Please bring the following ORIGINAL physical documents with you:
1. Government Photo ID & Address Proof: Original Aadhaar Card and PAN Card (for biometric e-KYC).
2. Proof of Livelihood / Operations:
   ${
     isFarmer
       ? "• Original Land Revenue Records (7/12 Extract, Khasra-Khatauni, Jamabandi, or registered Sale Deed)\n   • Kisan Passbook or agricultural proof"
       : "• Original Shop & Establishment Certificate (Gumasta), Trade License, or Udyam MSME Certificate\n   • Commercial tenancy agreement or ownership tax receipt"
   }
3. Banking Records: Updated Bank Passbook or last 6 months stamped bank statement.
4. Two (2) recent passport-sized color photographs.

BRANCH VISITING HOURS:
Monday to Friday, 10:00 AM to 4:00 PM (excluding bank holidays).
Please report directly to the Agricultural & MSME Credit Desk to meet the Loan Field Officer.

We look forward to welcoming you to the branch and partnering in your growth!

Warm regards,
AgriTrust Underwriting & Sanction Desk
AgriTrust Banking Corporation`;
}

export function BankerDecisionModal({
  isOpen,
  view,
  onClose,
  onSelectView,
  detail,
  latest,
  onRecheck,
  onHold,
  onConfirmDecline,
  onConfirmAccept,
  isSubmitting,
}: BankerDecisionModalProps) {
  const isFarmer = detail.applicant_category === "farmer";
  const defaultScheme = isFarmer ? FARMER_SCHEMES[0] : SHOP_SCHEMES[0];
  const defaultSanctionAmount = Math.round(
    latest.recommended_limit > 0 ? latest.recommended_limit : detail.requested_loan_amount,
  );
  const defaultRate =
    latest.policy_breakdown?.indicative_rate_band?.min_annual_rate ?? 8.75;
  const defaultTenure = detail.loan_tenure_months || 36;

  // Decline form state
  const [declineCategory, setDeclineCategory] = useState(DECLINE_CATEGORIES[0].id);
  const [declineFactors, setDeclineFactors] = useState<string[]>([
    ADVERSE_FACTORS_LIST[0],
  ]);
  const [declineNarrative, setDeclineNarrative] = useState("");
  const [declinePolicy, setDeclinePolicy] = useState(
    "RBI Master Direction - Priority Sector Lending (PSL) & Agri Credit Norms 2026",
  );
  const [declineError, setDeclineError] = useState<string | null>(null);

  // Accept form state
  const [sanctionAmount, setSanctionAmount] = useState<number>(defaultSanctionAmount);
  const [interestRate, setInterestRate] = useState<number>(defaultRate);
  const [tenureMonths, setTenureMonths] = useState<number>(defaultTenure);
  const [facilityScheme, setFacilityScheme] = useState<string>(defaultScheme);
  const [approvalRationale, setApprovalRationale] = useState(
    `Applicant demonstrates strong cash-flow proxy via UPI turnover, verified collateral, and low probability of default (${(latest.probability_of_default * 100).toFixed(1)}%). Recommended for in-principle approval.`,
  );
  const [borrowerNote, setBorrowerNote] = useState<string>("");
  const [acceptError, setAcceptError] = useState<string | null>(null);

  // Unique IDs for form controls
  const declineCategorySelectId = useId();
  const declinePolicyInputId = useId();
  const declineNarrativeTextareaId = useId();
  const sanctionAmountInputId = useId();
  const interestRateInputId = useId();
  const tenureMonthsInputId = useId();
  const facilitySchemeSelectId = useId();
  const approvalRationaleTextareaId = useId();
  const borrowerNoteTextareaId = useId();

  // Initialize borrower note when modal opens or sanction params change initially
  useEffect(() => {
    if (view === "accept" && !borrowerNote) {
      setBorrowerNote(
        generateDefaultBorrowerNote(
          detail.applicant_name,
          detail.applicant_category,
          sanctionAmount,
          interestRate,
          tenureMonths,
          facilityScheme,
        ),
      );
    }
  }, [
    view,
    borrowerNote,
    detail.applicant_name,
    detail.applicant_category,
    sanctionAmount,
    interestRate,
    tenureMonths,
    facilityScheme,
  ]);

  if (!isOpen || view === "none") return null;

  const toggleFactor = (factor: string) => {
    setDeclineFactors((prev) =>
      prev.includes(factor) ? prev.filter((item) => item !== factor) : [...prev, factor],
    );
  };

  const handleDeclineSubmit = async () => {
    if (declineNarrative.trim().length < 10) {
      setDeclineError("Please provide a detailed decline explanation (minimum 10 characters).");
      return;
    }
    setDeclineError(null);
    const categoryMeta =
      DECLINE_CATEGORIES.find((cat) => cat.id === declineCategory) ?? DECLINE_CATEGORIES[0];
    await onConfirmDecline({
      category: declineCategory,
      categoryLabel: categoryMeta.label,
      adverseFactors: declineFactors,
      narrative: declineNarrative.trim(),
      policyRef: declinePolicy.trim(),
    });
  };

  const handleAcceptSubmit = async () => {
    if (sanctionAmount <= 0) {
      setAcceptError("Please specify a valid sanction amount greater than zero.");
      return;
    }
    if (interestRate <= 0) {
      setAcceptError("Please specify a valid annual interest rate.");
      return;
    }
    if (tenureMonths <= 0) {
      setAcceptError("Please specify a valid repayment tenure in months.");
      return;
    }
    if (approvalRationale.trim().length < 10) {
      setAcceptError("Please provide an approval rationale (minimum 10 characters).");
      return;
    }
    if (borrowerNote.trim().length < 30) {
      setAcceptError("Please provide clear branch instructions note for the borrower.");
      return;
    }
    setAcceptError(null);
    await onConfirmAccept({
      sanctionAmount,
      interestRate,
      tenureMonths,
      facilityScheme,
      approvalRationale: approvalRationale.trim(),
      borrowerNote: borrowerNote.trim(),
    });
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="banker-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-slate-950/75 backdrop-blur-md overflow-y-auto animate-in fade-in duration-200"
    >
      <div className="relative w-full max-w-3xl rounded-3xl border border-slate-700/60 bg-slate-900 text-slate-100 shadow-2xl overflow-hidden my-auto">
        {/* Header Strip */}
        <div className="border-b border-slate-800 bg-slate-900/90 px-6 py-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-lg font-bold">
              {view === "gateway" && "⚡"}
              {view === "decline" && "❌"}
              {view === "accept" && "✅"}
            </span>
            <div>
              <div className="flex items-center gap-2">
                <h3 id="banker-modal-title" className="text-base font-semibold text-white">
                  {view === "gateway" && "Underwriting Decision Gateway"}
                  {view === "decline" && "Decline Application Form"}
                  {view === "accept" && "Acceptance & Sanction Form"}
                </h3>
                <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-[10px] font-medium text-slate-300 border border-slate-700">
                  {isFarmer ? "🌾 Farmer" : "🏪 Shop / MSME"}
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Applicant: <strong className="text-slate-200">{detail.applicant_name}</strong> · Ask:{" "}
                <span className="tabular-nums text-emerald-400 font-medium">
                  {formatINR(detail.requested_loan_amount)}
                </span>
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <div className="text-right hidden sm:block">
              <span className="text-[10px] uppercase tracking-wide text-slate-400 block">
                Calculated Score
              </span>
              <span className="text-sm font-bold tabular-nums text-emerald-400">
                {latest.credit_score}{" "}
                <span className="text-[11px] font-normal text-slate-400">({latest.risk_tier})</span>
              </span>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="rounded-xl border border-slate-700 p-2 text-slate-400 transition hover:bg-slate-800 hover:text-white disabled:opacity-50"
              title="Close modal"
              aria-label="Close modal"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M6 18L18 6M6 6l12 12"
                />
              </svg>
            </button>
          </div>
        </div>

        {/* ========================================================================= */}
        {/* VIEW 1: GATEWAY (Recheck | Hold | Decline | Accept & Send) */}
        {/* ========================================================================= */}
        {view === "gateway" && (
          <div className="p-6 space-y-6">
            {/* Quick Metrics Banner */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 rounded-2xl bg-slate-800/60 border border-slate-800 p-4">
              <div>
                <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-400">
                  Credit Score
                </span>
                <p className="text-lg font-bold text-white tabular-nums mt-0.5">
                  {latest.credit_score} <span className="text-xs text-slate-400 font-normal">/ 900</span>
                </p>
              </div>
              <div>
                <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-400">
                  Risk Tier
                </span>
                <p className="text-sm font-semibold text-emerald-400 mt-0.5">{latest.tier_label}</p>
              </div>
              <div>
                <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-400">
                  Recommended Limit
                </span>
                <p className="text-base font-bold text-white tabular-nums mt-0.5">
                  {formatINR(latest.recommended_limit)}
                </p>
              </div>
              <div>
                <span className="text-[10px] uppercase font-semibold tracking-wider text-slate-400">
                  Default Probability
                </span>
                <p className="text-sm font-semibold text-amber-400 mt-0.5">
                  {(latest.probability_of_default * 100).toFixed(1)}%
                </p>
              </div>
            </div>

            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">
                Choose an Underwriting Action to Proceed:
              </h4>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3.5">
                {/* 1. RECHECK */}
                <button
                  type="button"
                  data-testid="decision-btn-recheck"
                  onClick={onRecheck}
                  disabled={isSubmitting}
                  className="group relative flex flex-col text-left rounded-2xl border border-amber-500/30 bg-amber-950/20 p-4 transition-all hover:bg-amber-950/40 hover:border-amber-500/60 hover:shadow-lg hover:shadow-amber-950/30 active:scale-[0.99] disabled:opacity-50"
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-amber-500/20 text-amber-400 text-sm font-bold border border-amber-500/30">
                      🔄
                    </span>
                    <span className="font-semibold text-amber-200 group-hover:text-amber-100 text-sm">
                      Recheck
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-amber-300/80 mb-3 flex-1">
                    Clear all verified checkmarks. Re-open the file so the officer must manually inspect
                    every declared asset and document proof again.
                  </p>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-400 group-hover:underline">
                    Reset & Manual Audit →
                  </span>
                </button>

                {/* 2. HOLD */}
                <button
                  type="button"
                  data-testid="decision-btn-hold"
                  onClick={onHold}
                  disabled={isSubmitting}
                  className="group relative flex flex-col text-left rounded-2xl border border-sky-500/30 bg-sky-950/20 p-4 transition-all hover:bg-sky-950/40 hover:border-sky-500/60 hover:shadow-lg hover:shadow-sky-950/30 active:scale-[0.99] disabled:opacity-50"
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-sky-500/20 text-sky-400 text-sm font-bold border border-sky-500/30">
                      ⏸️
                    </span>
                    <span className="font-semibold text-sky-200 group-hover:text-sky-100 text-sm">
                      Hold (15s Pause)
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-sky-300/80 mb-3 flex-1">
                    Retain the calculated score on screen and dismiss this pop-out for 15 seconds to
                    inspect external records or consult committee.
                  </p>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-sky-400 group-hover:underline">
                    Dismiss for 15 Seconds →
                  </span>
                </button>

                {/* 3. DECLINE */}
                <button
                  type="button"
                  data-testid="decision-btn-decline"
                  onClick={() => onSelectView("decline")}
                  disabled={isSubmitting}
                  className="group relative flex flex-col text-left rounded-2xl border border-rose-500/30 bg-rose-950/20 p-4 transition-all hover:bg-rose-950/40 hover:border-rose-500/60 hover:shadow-lg hover:shadow-rose-950/30 active:scale-[0.99] disabled:opacity-50"
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-rose-500/20 text-rose-400 text-sm font-bold border border-rose-500/30">
                      ❌
                    </span>
                    <span className="font-semibold text-rose-200 group-hover:text-rose-100 text-sm">
                      Decline Proposal
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-rose-300/80 mb-3 flex-1">
                    Open decline form with adverse factors checklist, policy references, and statutory
                    underwriting justification fields.
                  </p>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-rose-400 group-hover:underline">
                    Complete Decline Form →
                  </span>
                </button>

                {/* 4. ACCEPT AND SEND */}
                <button
                  type="button"
                  data-testid="decision-btn-accept"
                  onClick={() => onSelectView("accept")}
                  disabled={isSubmitting}
                  className="group relative flex flex-col text-left rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-4 transition-all hover:bg-emerald-950/40 hover:border-emerald-500/60 hover:shadow-lg hover:shadow-emerald-950/30 active:scale-[0.99] disabled:opacity-50"
                >
                  <div className="flex items-center gap-2.5 mb-2">
                    <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-emerald-500/20 text-emerald-400 text-sm font-bold border border-emerald-500/30">
                      ✅
                    </span>
                    <span className="font-semibold text-emerald-200 group-hover:text-emerald-100 text-sm">
                      Accept & Send
                    </span>
                  </div>
                  <p className="text-xs leading-relaxed text-emerald-300/80 mb-3 flex-1">
                    Set sanctioned loan parameters, interest rate, tenure, and dispatch an official
                    in-principle sanction note with branch visit instructions.
                  </p>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-400 group-hover:underline">
                    Open Sanction & Note Form →
                  </span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* VIEW 2: DECLINE FORM */}
        {/* ========================================================================= */}
        {view === "decline" && (
          <div className="p-6 space-y-4 max-h-[80vh] overflow-y-auto">
            {declineError && (
              <div className="rounded-xl border border-rose-500/30 bg-rose-950/40 p-3 text-xs text-rose-200">
                ⚠️ {declineError}
              </div>
            )}

            {/* Primary Category Dropdown */}
            <div>
              <label
                htmlFor={declineCategorySelectId}
                className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
              >
                Primary Reason for Decline <span className="text-rose-400">*</span>
              </label>
              <select
                id={declineCategorySelectId}
                value={declineCategory}
                onChange={(e) => setDeclineCategory(e.target.value)}
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white focus:border-rose-500 focus:outline-none focus:ring-1 focus:ring-rose-500"
              >
                {DECLINE_CATEGORIES.map((cat) => (
                  <option key={cat.id} value={cat.id}>
                    {cat.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Adverse Factors Checklist */}
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5">
                Specific Adverse Credit & Collateral Factors (Select all that apply)
              </label>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {ADVERSE_FACTORS_LIST.map((factor) => {
                  const isChecked = declineFactors.includes(factor);
                  return (
                    <button
                      key={factor}
                      type="button"
                      onClick={() => toggleFactor(factor)}
                      className={`flex items-start gap-2.5 rounded-xl border p-2.5 text-left text-xs transition ${
                        isChecked
                          ? "border-rose-500/60 bg-rose-950/30 text-rose-200"
                          : "border-slate-800 bg-slate-800/40 text-slate-400 hover:border-slate-700 hover:text-slate-200"
                      }`}
                    >
                      <span
                        className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] ${
                          isChecked
                            ? "border-rose-500 bg-rose-600 text-white font-bold"
                            : "border-slate-600 bg-slate-800"
                        }`}
                      >
                        {isChecked && "✓"}
                      </span>
                      <span className="leading-snug">{factor}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Regulatory Policy Reference */}
            <div>
              <label
                htmlFor={declinePolicyInputId}
                className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
              >
                Regulatory & Bank Policy Circular Basis
              </label>
              <input
                id={declinePolicyInputId}
                type="text"
                value={declinePolicy}
                onChange={(e) => setDeclinePolicy(e.target.value)}
                placeholder="e.g. RBI Master Direction - Priority Sector Lending Norms 2026"
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white focus:border-rose-500 focus:outline-none focus:ring-1 focus:ring-rose-500"
              />
            </div>

            {/* Officer Explanatory Remarks (Mandatory Textarea) */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label
                  htmlFor={declineNarrativeTextareaId}
                  className="block text-xs font-semibold uppercase tracking-wider text-slate-300"
                >
                  Underwriter Explanatory Justification (Mandatory){" "}
                  <span className="text-rose-400">*</span>
                </label>
                <span className="text-[10px] text-slate-500">
                  {declineNarrative.length} characters
                </span>
              </div>
              <textarea
                id={declineNarrativeTextareaId}
                rows={4}
                value={declineNarrative}
                onChange={(e) => setDeclineNarrative(e.target.value)}
                placeholder="Describe clearly why this application is being declined. Detail discrepancies observed between declarations and uploaded proofs, debt obligations exceeding debt capacity, or adverse bureau/utility factors..."
                className="w-full rounded-xl border border-slate-700 bg-slate-800 p-3 text-xs text-white placeholder:text-slate-500 focus:border-rose-500 focus:outline-none focus:ring-1 focus:ring-rose-500 leading-relaxed"
              />
            </div>

            {/* Actions */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-800">
              <button
                type="button"
                onClick={() => onSelectView("gateway")}
                disabled={isSubmitting}
                className="rounded-xl border border-slate-700 px-4 py-2 text-xs font-medium text-slate-300 transition hover:bg-slate-800"
              >
                ← Back to Options
              </button>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={isSubmitting}
                  className="rounded-xl px-4 py-2 text-xs font-medium text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  data-testid="confirm-decline-button"
                  onClick={() => void handleDeclineSubmit()}
                  disabled={isSubmitting}
                  className="rounded-xl bg-rose-600 px-5 py-2 text-xs font-semibold text-white shadow-lg shadow-rose-950/50 transition hover:bg-rose-500 active:scale-[0.98] disabled:opacity-50"
                >
                  {isSubmitting ? "Recording Decline…" : "Confirm & Issue Formal Decline"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ========================================================================= */}
        {/* VIEW 3: ACCEPTANCE & SANCTION FORM */}
        {/* ========================================================================= */}
        {view === "accept" && (
          <div className="p-6 space-y-4 max-h-[82vh] overflow-y-auto">
            {acceptError && (
              <div className="rounded-xl border border-rose-500/30 bg-rose-950/40 p-3 text-xs text-rose-200">
                ⚠️ {acceptError}
              </div>
            )}

            {/* Sanction Parameters Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label
                  htmlFor={sanctionAmountInputId}
                  className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
                >
                  Sanctioned Amount (₹) <span className="text-emerald-400">*</span>
                </label>
                <input
                  id={sanctionAmountInputId}
                  type="number"
                  min={1000}
                  step={5000}
                  value={sanctionAmount}
                  onChange={(e) => setSanctionAmount(Number(e.target.value) || 0)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white font-semibold focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                <span className="text-[10px] text-slate-400 mt-1 block">
                  Formatted: {formatINR(sanctionAmount)}
                </span>
              </div>

              <div>
                <label
                  htmlFor={interestRateInputId}
                  className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
                >
                  Annual Rate (% p.a.) <span className="text-emerald-400">*</span>
                </label>
                <input
                  id={interestRateInputId}
                  type="number"
                  min={1}
                  max={36}
                  step={0.05}
                  value={interestRate}
                  onChange={(e) => setInterestRate(Number(e.target.value) || 0)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white font-semibold focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                <span className="text-[10px] text-slate-400 mt-1 block">
                  Indicative: {latest.policy_breakdown?.indicative_rate_band?.min_annual_rate ?? 8.5}%–
                  {latest.policy_breakdown?.indicative_rate_band?.max_annual_rate ?? 10.5}%
                </span>
              </div>

              <div>
                <label
                  htmlFor={tenureMonthsInputId}
                  className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
                >
                  Tenure (Months) <span className="text-emerald-400">*</span>
                </label>
                <input
                  id={tenureMonthsInputId}
                  type="number"
                  min={6}
                  max={240}
                  step={6}
                  value={tenureMonths}
                  onChange={(e) => setTenureMonths(Number(e.target.value) || 0)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white font-semibold focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                />
                <span className="text-[10px] text-slate-400 mt-1 block">
                  Requested: {detail.loan_tenure_months} months
                </span>
              </div>
            </div>

            {/* Facility Scheme Selection */}
            <div>
              <label
                htmlFor={facilitySchemeSelectId}
                className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
              >
                Facility / Credit Scheme <span className="text-emerald-400">*</span>
              </label>
              <select
                id={facilitySchemeSelectId}
                value={facilityScheme}
                onChange={(e) => setFacilityScheme(e.target.value)}
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              >
                {(isFarmer ? FARMER_SCHEMES : SHOP_SCHEMES).map((scheme) => (
                  <option key={scheme} value={scheme}>
                    {scheme}
                  </option>
                ))}
              </select>
            </div>

            {/* Approval Rationale */}
            <div>
              <label
                htmlFor={approvalRationaleTextareaId}
                className="block text-xs font-semibold uppercase tracking-wider text-slate-300 mb-1.5"
              >
                Underwriter Approval Justification <span className="text-emerald-400">*</span>
              </label>
              <textarea
                id={approvalRationaleTextareaId}
                rows={2}
                value={approvalRationale}
                onChange={(e) => setApprovalRationale(e.target.value)}
                placeholder="State approval reasoning and how borrower capacity was verified..."
                className="w-full rounded-xl border border-slate-700 bg-slate-800 p-2.5 text-xs text-white placeholder:text-slate-500 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>

            {/* Official Note to Farmer / Shopkeeper (With Branch Visit Instructions) */}
            <div className="rounded-2xl border border-emerald-500/30 bg-emerald-950/20 p-4 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-emerald-500/20 text-emerald-400 text-xs">
                    ✉️
                  </span>
                  <label
                    htmlFor={borrowerNoteTextareaId}
                    className="text-xs font-semibold uppercase tracking-wider text-emerald-200"
                  >
                    Official Instructions Note for Borrower (Dispatched to {detail.applicant_name})
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    setBorrowerNote(
                      generateDefaultBorrowerNote(
                        detail.applicant_name,
                        detail.applicant_category,
                        sanctionAmount,
                        interestRate,
                        tenureMonths,
                        facilityScheme,
                      ),
                    )
                  }
                  className="text-[11px] text-emerald-400 underline hover:text-emerald-300"
                >
                  Reset to Template
                </button>
              </div>

              <p className="text-[11px] text-emerald-300/80">
                This note will be recorded and shared with the applicant. It directs the farmer or
                shopkeeper to visit their branch with all required original physical documents to
                initiate loan execution and disbursement.
              </p>

              <textarea
                id={borrowerNoteTextareaId}
                rows={7}
                value={borrowerNote}
                onChange={(e) => setBorrowerNote(e.target.value)}
                placeholder="Write official in-principle approval instructions..."
                className="w-full rounded-xl border border-emerald-500/30 bg-slate-900/90 p-3 text-xs text-slate-100 placeholder:text-slate-500 font-mono focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500 leading-relaxed"
              />
            </div>

            {/* Actions */}
            <div className="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-800">
              <button
                type="button"
                onClick={() => onSelectView("gateway")}
                disabled={isSubmitting}
                className="rounded-xl border border-slate-700 px-4 py-2 text-xs font-medium text-slate-300 transition hover:bg-slate-800"
              >
                ← Back to Options
              </button>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  disabled={isSubmitting}
                  className="rounded-xl px-4 py-2 text-xs font-medium text-slate-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  data-testid="confirm-accept-button"
                  onClick={() => void handleAcceptSubmit()}
                  disabled={isSubmitting}
                  className="rounded-xl bg-emerald-500 px-5 py-2 text-xs font-semibold text-slate-950 shadow-lg shadow-emerald-950/50 transition hover:bg-emerald-400 active:scale-[0.98] disabled:opacity-50"
                >
                  {isSubmitting ? "Sanctioning & Sending…" : "Confirm Sanction & Send to Borrower"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
