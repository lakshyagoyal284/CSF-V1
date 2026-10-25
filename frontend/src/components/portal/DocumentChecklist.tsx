"use client";

import { useMemo, useState } from "react";
import type { ApplicantCategory, DocType } from "@/lib/portal-types";

export type PriorityLevel = "critical" | "high" | "recommended" | "optional";

export interface ChecklistDocument {
  id: string;
  title: string;
  subtitle: string;
  description: string;
  docType: DocType;
  priority: PriorityLevel;
  priorityLabel: string;
  badgeStyle: {
    bg: string;
    text: string;
    border: string;
    dot: string;
  };
}

export const FARMER_CHECKLIST: ChecklistDocument[] = [
  {
    id: "farmer_land_record",
    title: "Land Revenue Record / 7/12 Extract (Khasra / Khatauni)",
    subtitle: "Acreage & Agricultural Title Proof",
    description: "Official state revenue record validating land ownership, survey number, and declared cultivable acreage.",
    docType: "land_record",
    priority: "critical",
    priorityLabel: "Critical / Mandatory",
    badgeStyle: {
      bg: "bg-rose-50",
      text: "text-rose-700",
      border: "border-rose-200",
      dot: "bg-rose-500",
    },
  },
  {
    id: "farmer_kyc",
    title: "Aadhaar Card & PAN Card (KYC Identity Proof)",
    subtitle: "Government Photo ID & Address Proof",
    description: "Valid Aadhaar and PAN card copies verifying applicant identity, legal name, and primary residence.",
    docType: "other",
    priority: "critical",
    priorityLabel: "Critical / Mandatory",
    badgeStyle: {
      bg: "bg-rose-50",
      text: "text-rose-700",
      border: "border-rose-200",
      dot: "bg-rose-500",
    },
  },
  {
    id: "farmer_bank_stmt",
    title: "Bank Passbook / 6 Months Bank Account Statement",
    subtitle: "Cash-flow & Agricultural Banking Record",
    description: "Shows agricultural deposit turnover, PM-KISAN or state DBT subsidies, and average credit balance.",
    docType: "bank_statement",
    priority: "high",
    priorityLabel: "High Priority",
    badgeStyle: {
      bg: "bg-amber-50",
      text: "text-amber-800",
      border: "border-amber-200",
      dot: "bg-amber-500",
    },
  },
  {
    id: "farmer_utility",
    title: "Agricultural Electricity / Tube-well Invoice",
    subtitle: "Utility Discipline & Farm Operation Proof",
    description: "Paid electric utility bill for tubewell/pump connection demonstrating operational continuity and payment score.",
    docType: "utility_bill",
    priority: "high",
    priorityLabel: "High Priority",
    badgeStyle: {
      bg: "bg-amber-50",
      text: "text-amber-800",
      border: "border-amber-200",
      dot: "bg-amber-500",
    },
  },
  {
    id: "farmer_crop_yield",
    title: "Crop Sowing / Harvest Mandi Sale Receipts (J-Form / Girdawari)",
    subtitle: "Annual Yield & Market Produce Record",
    description: "APMC / Mandi procurement receipts or village patwari girdawari validating seasonal crop turnover.",
    docType: "other",
    priority: "recommended",
    priorityLabel: "Recommended",
    badgeStyle: {
      bg: "bg-emerald-50",
      text: "text-emerald-700",
      border: "border-emerald-200",
      dot: "bg-emerald-500",
    },
  },
  {
    id: "farmer_upi",
    title: "UPI / PSP Digital Payment Ledger Statement",
    subtitle: "Digital Collections Footprint",
    description: "Digital payment QR / UPI merchant statements from direct-to-consumer crop sales and input purchases.",
    docType: "upi_ledger",
    priority: "recommended",
    priorityLabel: "Recommended",
    badgeStyle: {
      bg: "bg-emerald-50",
      text: "text-emerald-700",
      border: "border-emerald-200",
      dot: "bg-emerald-500",
    },
  },
  {
    id: "farmer_machinery",
    title: "Tractor / Farm Equipment Purchase Invoice or RC",
    subtitle: "Machinery Asset Substantiation",
    description: "Tax invoice or registration certificate for tractor, harvester, solar pump, or micro-irrigation machinery.",
    docType: "equipment_invoice",
    priority: "optional",
    priorityLabel: "Optional / Bonus",
    badgeStyle: {
      bg: "bg-slate-100",
      text: "text-slate-600",
      border: "border-slate-200",
      dot: "bg-slate-400",
    },
  },
  {
    id: "farmer_soil_kcc",
    title: "Soil Health Card / Kisan Credit Card (KCC) Record",
    subtitle: "Soil Quality & Past Credit History",
    description: "Official Soil Health Card (verifying declared soil/irrigation score) or past active KCC loan booklet.",
    docType: "land_record",
    priority: "optional",
    priorityLabel: "Optional / Bonus",
    badgeStyle: {
      bg: "bg-slate-100",
      text: "text-slate-600",
      border: "border-slate-200",
      dot: "bg-slate-400",
    },
  },
];

export const SHOP_CHECKLIST: ChecklistDocument[] = [
  {
    id: "shop_trade_license",
    title: "Municipal Trade License / Shop & Establishment Certificate / Udyam",
    subtitle: "Commercial Entity & Legal Authorization",
    description: "Local municipal corporation commercial permit or MSME Udyam registration validating business existence.",
    docType: "property_deed",
    priority: "critical",
    priorityLabel: "Critical / Mandatory",
    badgeStyle: {
      bg: "bg-rose-50",
      text: "text-rose-700",
      border: "border-rose-200",
      dot: "bg-rose-500",
    },
  },
  {
    id: "shop_kyc",
    title: "Proprietor Aadhaar & PAN Card (KYC Proof)",
    subtitle: "Identity & Personal Guarantee Document",
    description: "Identity and address proof of the business proprietor or primary applicant.",
    docType: "other",
    priority: "critical",
    priorityLabel: "Critical / Mandatory",
    badgeStyle: {
      bg: "bg-rose-50",
      text: "text-rose-700",
      border: "border-rose-200",
      dot: "bg-rose-500",
    },
  },
  {
    id: "shop_bank_stmt",
    title: "Current / Savings Account Bank Statements (6 Months)",
    subtitle: "Business Cash-flow & Working Capital Throughput",
    description: "Bank statements showing daily cash and digital deposits, supplier RTGS/NEFT payments, and monthly balances.",
    docType: "bank_statement",
    priority: "high",
    priorityLabel: "High Priority",
    badgeStyle: {
      bg: "bg-amber-50",
      text: "text-amber-800",
      border: "border-amber-200",
      dot: "bg-amber-500",
    },
  },
  {
    id: "shop_utility",
    title: "Commercial Electricity / Utility Invoice",
    subtitle: "Commercial Tariff Payment Record",
    description: "Latest paid commercial tariff electricity bill verifying physical location and utility payment discipline.",
    docType: "utility_bill",
    priority: "high",
    priorityLabel: "High Priority",
    badgeStyle: {
      bg: "bg-amber-50",
      text: "text-amber-800",
      border: "border-amber-200",
      dot: "bg-amber-500",
    },
  },
  {
    id: "shop_inventory",
    title: "Wholesale Supplier Purchase Invoices / Stock Receipts",
    subtitle: "Stock Value & Inventory Turnover Proof",
    description: "Invoices from wholesale distributors demonstrating inventory purchasing and declared stock valuation.",
    docType: "inventory_receipt",
    priority: "recommended",
    priorityLabel: "Recommended",
    badgeStyle: {
      bg: "bg-emerald-50",
      text: "text-emerald-700",
      border: "border-emerald-200",
      dot: "bg-emerald-500",
    },
  },
  {
    id: "shop_upi_qr",
    title: "Merchant QR Code / POS / UPI Settlement Statements",
    subtitle: "Digital Customer Collections Footprint",
    description: "PhonePe/Google Pay/Paytm merchant settlement ledger showing daily digital retail sales.",
    docType: "upi_ledger",
    priority: "recommended",
    priorityLabel: "Recommended",
    badgeStyle: {
      bg: "bg-emerald-50",
      text: "text-emerald-700",
      border: "border-emerald-200",
      dot: "bg-emerald-500",
    },
  },
  {
    id: "shop_lease_deed",
    title: "Premises Ownership Title Deed or Registered Rent Agreement",
    subtitle: "Shop Premise Tenancy Proof",
    description: "Registered commercial lease agreement or title deed for declared owned business premises.",
    docType: "property_deed",
    priority: "optional",
    priorityLabel: "Optional / Bonus",
    badgeStyle: {
      bg: "bg-slate-100",
      text: "text-slate-600",
      border: "border-slate-200",
      dot: "bg-slate-400",
    },
  },
  {
    id: "shop_equipment",
    title: "Commercial Equipment / Machinery Invoices (Deep Freezer, POS, Scale)",
    subtitle: "Tangible Business Equipment Proof",
    description: "Tax invoices or AMC contracts for commercial machinery, display chillers, or electronic billing setups.",
    docType: "equipment_invoice",
    priority: "optional",
    priorityLabel: "Optional / Bonus",
    badgeStyle: {
      bg: "bg-slate-100",
      text: "text-slate-600",
      border: "border-slate-200",
      dot: "bg-slate-400",
    },
  },
];

interface DocumentChecklistProps {
  applicantCategory: ApplicantCategory;
  checkedIds: Record<string, boolean>;
  onToggleCheck: (id: string) => void;
  attachedDocTypes: DocType[];
  attachedFilesCount: number;
}

export function DocumentChecklist({
  applicantCategory,
  checkedIds,
  onToggleCheck,
  attachedDocTypes,
  attachedFilesCount,
}: DocumentChecklistProps) {
  const [filter, setFilter] = useState<"all" | PriorityLevel>("all");

  const isFarmer = applicantCategory === "farmer";
  const items = isFarmer ? FARMER_CHECKLIST : SHOP_CHECKLIST;

  const filteredItems = useMemo(() => {
    if (filter === "all") return items;
    return items.filter((item) => item.priority === filter);
  }, [items, filter]);

  const checkedCount = useMemo(() => {
    return items.filter((item) => Boolean(checkedIds[item.id])).length;
  }, [items, checkedIds]);

  const criticalCount = useMemo(() => {
    return items.filter((item) => item.priority === "critical").length;
  }, [items]);

  const criticalCheckedCount = useMemo(() => {
    return items.filter((item) => item.priority === "critical" && Boolean(checkedIds[item.id])).length;
  }, [items, checkedIds]);

  return (
    <div
      data-testid="document-checklist"
      className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5 shadow-xs space-y-4"
    >
      {/* Header with Title and Segment Badge */}
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-emerald-100 text-emerald-800 text-xs font-bold">
              ✓
            </span>
            <h3 className="text-sm font-semibold text-slate-900">
              {isFarmer ? "Farmer Document Checklist" : "Shopkeeper / MSME Document Checklist"}
            </h3>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600 border border-slate-200">
              {isFarmer ? "🌾 Agricultural" : "🏪 Commercial"}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Check off what you have available. You can attach any subset of documents and proceed whenever you wish.
          </p>
        </div>

        {/* Progress pill */}
        <div
          data-testid="checklist-progress"
          className="rounded-xl border border-emerald-200 bg-emerald-50/70 px-3 py-1.5 text-right"
        >
          <span className="text-[10px] uppercase font-bold tracking-wider text-emerald-800 block">
            Checklist Status
          </span>
          <span className="text-xs font-semibold tabular-nums text-emerald-900">
            {checkedCount} of {items.length} checked · {attachedFilesCount} attached
          </span>
        </div>
      </div>

      {/* Advisory Non-Blocking Reassurance Banner */}
      <div className="rounded-xl border border-sky-200/80 bg-sky-50/70 p-3 text-xs text-sky-950 flex items-start gap-2.5 leading-relaxed">
        <span className="text-base select-none">💡</span>
        <div className="flex-1">
          <p className="font-semibold text-sky-900">Non-Interfering Checklist Guarantee:</p>
          <p className="mt-0.5 text-[11px] text-sky-800">
            This checklist is an advisory guide to optimize your credit score. You do <strong>NOT</strong> have to attach all {items.length} documents. For example, if you have 4 documents, attach those 4, check them off, and click <strong>&quot;Continue&quot;</strong> to proceed smoothly — you will never be blocked.
          </p>
        </div>
      </div>

      {/* Filter Tabs */}
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        <span className="text-[11px] font-medium text-slate-400 mr-1">Filter:</span>
        {(
          [
            ["all", `All (${items.length})`],
            ["critical", `Critical (${criticalCount})`],
            ["high", "High Priority"],
            ["recommended", "Recommended"],
            ["optional", "Optional"],
          ] as const
        ).map(([value, label]) => {
          const active = filter === value;
          return (
            <button
              key={value}
              type="button"
              data-testid={`checklist-filter-${value}`}
              onClick={() => setFilter(value)}
              className={`rounded-lg px-2.5 py-1 text-[11px] font-medium transition-all ${
                active
                  ? "bg-slate-900 text-white shadow-xs"
                  : "bg-slate-100 text-slate-600 hover:bg-slate-200/80"
              }`}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Checklist Items List */}
      <ul className="space-y-2.5">
        {filteredItems.map((item) => {
          const isChecked = Boolean(checkedIds[item.id]);
          const isAttached = attachedDocTypes.includes(item.docType);

          return (
            <li
              key={item.id}
              data-testid={`checklist-item-${item.id}`}
              onClick={() => onToggleCheck(item.id)}
              className={`group relative flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-all duration-150 select-none ${
                isChecked
                  ? "border-emerald-300 bg-emerald-50/40 shadow-xs"
                  : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50/50"
              }`}
            >
              {/* Checkbox */}
              <div className="pt-0.5">
                <input
                  type="checkbox"
                  id={`check-${item.id}`}
                  data-testid={`checklist-checkbox-${item.id}`}
                  checked={isChecked}
                  readOnly
                  className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 pointer-events-none cursor-pointer"
                />
              </div>

              {/* Text details */}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`text-xs font-semibold transition-colors ${
                      isChecked ? "text-emerald-950 font-bold" : "text-slate-800"
                    }`}
                  >
                    {item.title}
                  </span>

                  {/* Priority Badge */}
                  <span
                    data-testid={`checklist-priority-${item.id}`}
                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${item.badgeStyle.bg} ${item.badgeStyle.text} ${item.badgeStyle.border}`}
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${item.badgeStyle.dot}`} />
                    {item.priorityLabel}
                  </span>

                  {/* Attached Status Badge */}
                  {isAttached && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 text-emerald-800 border border-emerald-200 px-2 py-0.5 text-[10px] font-semibold">
                      Attached ✓
                    </span>
                  )}
                </div>

                <p className="mt-0.5 text-[11px] text-slate-500 leading-relaxed">
                  {item.description}
                </p>
              </div>

              {/* Check indicator */}
              <div className="shrink-0 pt-0.5">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] transition-colors ${
                    isChecked
                      ? "bg-emerald-600 text-white font-bold"
                      : "border border-slate-200 text-transparent group-hover:border-slate-400"
                  }`}
                >
                  ✓
                </span>
              </div>
            </li>
          );
        })}
      </ul>

      {/* Footer Summary & Helpful Tip */}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5 text-[11px] border border-slate-200/80">
        <span className="text-slate-600">
          <strong>Summary:</strong> {criticalCheckedCount} of {criticalCount} critical proofs marked ·{" "}
          {checkedCount} total proofs ready
        </span>
        <span className="text-emerald-700 font-medium">
          Attach proofs below and continue anytime →
        </span>
      </div>
    </div>
  );
}
