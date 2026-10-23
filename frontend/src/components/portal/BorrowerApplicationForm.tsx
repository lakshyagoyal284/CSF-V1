"use client";

/**
 * Multi-step borrower application.
 *
 * Steps follow how an applicant actually thinks about the file:
 *   identity -> assets -> cash flow & ask -> proofs -> review
 *
 * Design notes:
 * * Empty numeric inputs are sent as `null`, not `0`. The model treats a missing
 *   declaration differently from a declared zero.
 * * Segment-specific fields are removed from the payload, not just hidden, so a
 *   farmer cannot accidentally submit shop inventory (the API rejects that too).
 * * The borrower composes and uploads; it never calls a scoring endpoint.
 */

import { ChangeEvent, useMemo, useState } from "react";

import { StatusBadge } from "@/components/portal/PortalShell";
import { portalApi, PortalApiError } from "@/lib/portal-api";
import { useAuth } from "@/components/portal/AuthProvider";
import type {
  ApplicantCategory,
  ApplicationPayload,
  CropType,
  DocType,
  RegionTier,
  ShopOwnership,
  UploadFileEntry,
} from "@/lib/portal-types";

type Step = 0 | 1 | 2 | 3 | 4;

const STEPS = ["Applicant", "Assets", "Cash flow", "Documents", "Review"] as const;

const DOC_TYPES: Array<{ value: DocType; label: string }> = [
  { value: "land_record", label: "Land record / RoR" },
  { value: "property_deed", label: "Property deed" },
  { value: "utility_bill", label: "Utility bill / statement" },
  { value: "inventory_receipt", label: "Inventory receipt" },
  { value: "bank_statement", label: "Bank statement" },
  { value: "upi_ledger", label: "UPI / PSP ledger" },
  { value: "equipment_invoice", label: "Equipment invoice" },
  { value: "other", label: "Other supporting proof" },
];

interface FormState {
  applicant_name: string;
  phone: string;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  age: string;
  dependents: string;
  years_in_operation: string;
  crop_type: CropType;

  land_acres: string;
  soil_quality: string;
  crop_yield_val: string;
  land_market_value: string;
  irrigation_score: string;
  livestock_val: string;

  shop_inventory_val: string;
  shop_area_sqft: string;
  shop_ownership: ShopOwnership;

  property_val: string;
  equipment_val: string;

  upi_volume: string;
  annual_income_proxy: string;
  utility_score: string;
  utility_delays_12m: string;
  avg_bank_balance: string;

  requested_loan_amount: string;
  loan_tenure_months: string;
  outstanding_debt: string;
  monthly_emi_obligation: string;
}

const EMPTY: FormState = {
  applicant_name: "",
  phone: "",
  applicant_category: "farmer",
  region_tier: "rural",
  age: "",
  dependents: "",
  years_in_operation: "",
  crop_type: "wheat",
  land_acres: "",
  soil_quality: "",
  crop_yield_val: "",
  land_market_value: "",
  irrigation_score: "",
  livestock_val: "",
  shop_inventory_val: "",
  shop_area_sqft: "",
  shop_ownership: "owned",
  property_val: "",
  equipment_val: "",
  upi_volume: "",
  annual_income_proxy: "",
  utility_score: "",
  utility_delays_12m: "",
  avg_bank_balance: "",
  requested_loan_amount: "",
  loan_tenure_months: "36",
  outstanding_debt: "",
  monthly_emi_obligation: "",
};

const DEMO: FormState = {
  applicant_name: "Asha Kumari",
  phone: "+91 98765 43210",
  applicant_category: "farmer",
  region_tier: "rural",
  age: "44",
  dependents: "3",
  years_in_operation: "12",
  crop_type: "wheat",
  land_acres: "4.5",
  soil_quality: "74",
  crop_yield_val: "236000",
  land_market_value: "1450000",
  irrigation_score: "78",
  livestock_val: "180000",
  shop_inventory_val: "",
  shop_area_sqft: "",
  shop_ownership: "owned",
  property_val: "520000",
  equipment_val: "760000",
  upi_volume: "62000",
  annual_income_proxy: "300000",
  utility_score: "82",
  utility_delays_12m: "1",
  avg_bank_balance: "68000",
  requested_loan_amount: "300000",
  loan_tenure_months: "36",
  outstanding_debt: "70000",
  monthly_emi_obligation: "2400",
};

const MAX_UPLOAD_MB = 10;
const ACCEPTED = "application/pdf,image/png,image/jpeg,image/webp";

const numeric = (value: string): number | null => {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

function validateStep(step: Step, form: FormState, uploads: UploadFileEntry[]): string[] {
  const errors: string[] = [];

  if (step === 0) {
    if (form.applicant_name.trim().length < 2) errors.push("Enter the applicant's full name.");
    const age = numeric(form.age);
    if (age !== null && (age < 18 || age > 100)) errors.push("Age must be between 18 and 100.");
  }

  if (step === 2) {
    const requested = numeric(form.requested_loan_amount);
    if (!requested || requested <= 0) errors.push("Enter the loan amount requested.");
    const tenure = numeric(form.loan_tenure_months);
    if (tenure === null || tenure < 6 || tenure > 360) {
      errors.push("Tenure must be between 6 and 360 months.");
    }
    const income = [
      numeric(form.upi_volume),
      numeric(form.annual_income_proxy),
      numeric(form.crop_yield_val),
    ];
    if (!income.some((value) => value !== null && value > 0)) {
      errors.push("Declare at least one income proxy: UPI volume, annual income or crop value.");
    }
    const utility = numeric(form.utility_score);
    if (utility !== null && (utility < 0 || utility > 100)) {
      errors.push("Utility score must be between 0 and 100.");
    }
  }

  if (step === 3 && uploads.length === 0) {
    errors.push("Attach at least one proof document for the bank officer to verify.");
  }

  if (step === 4) {
    // Re-run every step on submit: a user can navigate back, clear a value and
    // return without the earlier step having re-validated it.
    errors.push(
      ...validateStep(0, form, uploads),
      ...validateStep(2, form, uploads),
      ...validateStep(3, form, uploads),
    );
  }
  return errors;
}

export function BorrowerApplicationForm({
  token,
  onSubmitted,
}: {
  token: string;
  onSubmitted: () => void;
}) {
  const { user } = useAuth();
  const [step, setStep] = useState<Step>(0);
  const [form, setForm] = useState<FormState>(() => ({
    ...EMPTY,
    applicant_name: user?.full_name ?? "",
    phone: user?.phone ?? "",
  }));
  const [uploads, setUploads] = useState<UploadFileEntry[]>([]);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [successId, setSuccessId] = useState<string | null>(null);

  const isFarmer = form.applicant_category === "farmer";
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const totalAssets = useMemo(() => {
    const values = isFarmer
      ? [form.land_market_value, form.property_val, form.equipment_val, form.livestock_val]
      : [form.shop_inventory_val, form.property_val, form.equipment_val];
    return values.reduce((sum, value) => sum + (numeric(value) ?? 0), 0);
  }, [form, isFarmer]);

  const advance = () => {
    const found = validateStep(step, form, uploads);
    if (found.length > 0) {
      setErrors(found);
      return;
    }
    setErrors([]);
    setStep((current) => Math.min(current + 1, 4) as Step);
  };

  const back = () => {
    setErrors([]);
    setStep((current) => Math.max(current - 1, 0) as Step);
  };

  const addFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files ?? []);
    event.target.value = "";
    const rejected: string[] = [];
    const acceptedFiles: UploadFileEntry[] = [];

    for (const file of chosen) {
      if (uploads.length + acceptedFiles.length >= 10) {
        rejected.push("A maximum of 10 documents can be attached.");
        break;
      }
      if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
        rejected.push(`${file.name} is larger than ${MAX_UPLOAD_MB} MB.`);
        continue;
      }
      if (!["application/pdf", "image/png", "image/jpeg", "image/webp"].includes(file.type)) {
        rejected.push(`${file.name} must be a PDF, PNG, JPEG or WebP file.`);
        continue;
      }
      acceptedFiles.push({
        id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`,
        file,
        docType: isFarmer ? "land_record" : "inventory_receipt",
      });
    }

    if (acceptedFiles.length) setUploads((current) => [...current, ...acceptedFiles]);
    setErrors(rejected);
  };

  const buildPayload = (): ApplicationPayload => {
    const assets: ApplicationPayload["assets"] = {
      property_val: numeric(form.property_val),
      equipment_val: numeric(form.equipment_val),
      utility_score: numeric(form.utility_score),
      utility_delays_12m: numeric(form.utility_delays_12m),
      upi_volume: numeric(form.upi_volume),
      avg_bank_balance: numeric(form.avg_bank_balance),
      annual_income_proxy: numeric(form.annual_income_proxy),
    };

    if (isFarmer) {
      Object.assign(assets, {
        land_acres: numeric(form.land_acres),
        soil_quality: numeric(form.soil_quality),
        crop_yield_val: numeric(form.crop_yield_val),
        land_market_value: numeric(form.land_market_value),
        irrigation_score: numeric(form.irrigation_score),
        livestock_val: numeric(form.livestock_val),
      });
    } else {
      Object.assign(assets, {
        shop_inventory_val: numeric(form.shop_inventory_val),
        shop_area_sqft: numeric(form.shop_area_sqft),
        shop_ownership: form.shop_ownership,
      });
    }

    return {
      applicant_name: form.applicant_name.trim(),
      consent_accepted: true,
      phone: form.phone.trim() || null,
      applicant_category: form.applicant_category,
      region_tier: form.region_tier,
      age: numeric(form.age),
      dependents: numeric(form.dependents),
      years_in_operation: numeric(form.years_in_operation),
      crop_type: isFarmer ? form.crop_type : undefined,
      requested_loan_amount: numeric(form.requested_loan_amount) ?? 0,
      loan_tenure_months: numeric(form.loan_tenure_months) ?? 36,
      outstanding_debt: numeric(form.outstanding_debt) ?? 0,
      monthly_emi_obligation: numeric(form.monthly_emi_obligation) ?? 0,
      assets,
    };
  };

  const submit = async () => {
    const found = validateStep(4, form, uploads);
    if (!accepted) found.push("Confirm that the declared information is accurate.");
    if (found.length) {
      setErrors(found);
      return;
    }

    setBusy(true);
    setErrors([]);
    try {
      const created = await portalApi.submitApplication(token, buildPayload(), uploads);
      setSuccessId(created.id);
      setForm(EMPTY);
      setUploads([]);
      setAccepted(false);
      setStep(0);
      onSubmitted();
    } catch (caught) {
      if (caught instanceof PortalApiError) {
        setErrors([caught.message, ...caught.fields]);
      } else {
        setErrors(["Submission failed unexpectedly. Please try again."]);
      }
    } finally {
      setBusy(false);
    }
  };

  if (successId) {
    return (
      <div className="rounded-2xl border border-emerald-200 bg-white p-6 shadow-sm">
        <div className="flex items-start gap-4">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-xl text-emerald-700">
            ✓
          </span>
          <div>
            <h2 className="text-base font-semibold text-slate-900">Application submitted</h2>
            <p className="mt-1 text-sm leading-relaxed text-slate-600">
              Your file is now with the bank for verification. You can track the status on the
              right. A bank officer will reconcile your declarations against the uploaded proofs
              and calculate the credit decision.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <StatusBadge status="pending" />
              <span className="font-mono text-[11px] text-slate-400">#{successId.slice(0, 8)}</span>
            </div>
            <p className="mt-3 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">
              For security, applicants cannot view the score, the risk tier or the model output.
              Your bank will communicate the decision through its normal channel.
            </p>
            <button
              type="button"
              data-testid="start-another-application"
              onClick={() => setSuccessId(null)}
              className="mt-4 rounded-lg bg-slate-900 px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-slate-700"
            >
              Start another application
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      {/* stepper */}
      <div className="border-b border-slate-100 px-4 py-4 sm:px-6">
        <ol className="flex flex-wrap items-center gap-1.5 text-[11px] font-medium">
          {STEPS.map((label, index) => {
            const state = index === step ? "current" : index < step ? "done" : "todo";
            return (
              <li key={label} className="flex items-center gap-1.5">
                <span
                  className={`flex h-5 w-5 items-center justify-center rounded-full text-[10px] ${
                    state === "current"
                      ? "bg-emerald-600 text-white"
                      : state === "done"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-400"
                  }`}
                >
                  {state === "done" ? "✓" : index + 1}
                </span>
                <span className={state === "todo" ? "text-slate-400" : "text-slate-700"}>
                  {label}
                </span>
                {index < STEPS.length - 1 && <span className="px-1 text-slate-300">·</span>}
              </li>
            );
          })}
        </ol>
      </div>

      <div className="px-4 py-5 sm:px-6">
        {step === 0 && (
          <section className="space-y-4">
            <StepHeading
              title="Who is applying?"
              hint="The segment decides which collateral the bank will assess."
            />
            <div className="grid grid-cols-2 gap-2">
              {(
                [
                  ["farmer", "Farmer", "Land, crop and livestock"],
                  ["small_business", "Shopkeeper / MSME", "Inventory, premises and equipment"],
                ] as const
              ).map(([value, label, hint]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => set("applicant_category", value)}
                  className={`rounded-xl border p-3 text-left transition ${
                    form.applicant_category === value
                      ? "border-emerald-500 bg-emerald-50 ring-1 ring-emerald-500/30"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <p className="text-sm font-semibold text-slate-800">{label}</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>
                </button>
              ))}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Full name" required value={form.applicant_name} onChange={(v) => set("applicant_name", v)} placeholder="Asha Kumari" text />
              <Field label="Phone" value={form.phone} onChange={(v) => set("phone", v)} placeholder="+91 98765 43210" text />
              <Select label="Region" value={form.region_tier} onChange={(v) => set("region_tier", v as RegionTier)}>
                <option value="rural">Rural</option>
                <option value="semi_urban">Semi-urban</option>
                <option value="urban">Urban</option>
              </Select>
              {isFarmer ? (
                <Select label="Primary crop" value={form.crop_type} onChange={(v) => set("crop_type", v as CropType)}>
                  {["wheat", "rice", "cotton", "sugarcane", "horticulture", "pulses"].map((crop) => (
                    <option key={crop} value={crop}>{crop[0].toUpperCase() + crop.slice(1)}</option>
                  ))}
                </Select>
              ) : (
                <Field label="Years in operation" value={form.years_in_operation} onChange={(v) => set("years_in_operation", v)} placeholder="e.g. 7" />
              )}
              <Field label="Age" value={form.age} onChange={(v) => set("age", v)} placeholder="e.g. 44" />
              <Field label="Dependents" value={form.dependents} onChange={(v) => set("dependents", v)} placeholder="e.g. 3" />
              {isFarmer && (
                <Field label="Years in operation" value={form.years_in_operation} onChange={(v) => set("years_in_operation", v)} placeholder="e.g. 12" />
              )}
            </div>
          </section>
        )}

        {step === 1 && (
          <section className="space-y-4">
            <StepHeading
              title={isFarmer ? "Land and farm assets" : "Shop and business assets"}
              hint="Declare what you own. Unverified values are checked against your proofs."
            />
            <div className="grid gap-3 sm:grid-cols-2">
              {isFarmer ? (
                <>
                  <Field label="Land holding" suffix="acres" value={form.land_acres} onChange={(v) => set("land_acres", v)} placeholder="e.g. 4.5" />
                  <Field label="Land market value" suffix="₹" value={form.land_market_value} onChange={(v) => set("land_market_value", v)} placeholder="e.g. 1450000" />
                  <Field label="Soil quality" suffix="/100" value={form.soil_quality} onChange={(v) => set("soil_quality", v)} placeholder="0 - 100" />
                  <Field label="Irrigation score" suffix="/100" value={form.irrigation_score} onChange={(v) => set("irrigation_score", v)} placeholder="0 - 100" />
                  <Field label="Crop value (12 months)" suffix="₹" value={form.crop_yield_val} onChange={(v) => set("crop_yield_val", v)} placeholder="e.g. 236000" />
                  <Field label="Livestock value" suffix="₹" value={form.livestock_val} onChange={(v) => set("livestock_val", v)} placeholder="e.g. 180000" />
                </>
              ) : (
                <>
                  <Field label="Inventory value" suffix="₹" value={form.shop_inventory_val} onChange={(v) => set("shop_inventory_val", v)} placeholder="e.g. 520000" />
                  <Field label="Shop area" suffix="sq ft" value={form.shop_area_sqft} onChange={(v) => set("shop_area_sqft", v)} placeholder="e.g. 300" />
                  <Select label="Premises" value={form.shop_ownership} onChange={(v) => set("shop_ownership", v as ShopOwnership)}>
                    <option value="owned">Owned</option>
                    <option value="rented">Rented</option>
                  </Select>
                </>
              )}
              <Field label="Property value" suffix="₹" value={form.property_val} onChange={(v) => set("property_val", v)} placeholder="e.g. 520000" />
              <Field label="Equipment / machinery" suffix="₹" value={form.equipment_val} onChange={(v) => set("equipment_val", v)} placeholder="e.g. 760000" />
            </div>
            <div className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-xs">
              <span className="text-slate-500">Declared tangible assets</span>
              <span className="font-semibold tabular-nums text-slate-800">
                ₹{totalAssets.toLocaleString("en-IN")}
              </span>
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="space-y-4">
            <StepHeading
              title="Cash flow and the credit ask"
              hint="Alternative data replaces the salary slip: UPI collections, utility discipline and bank balances."
            />
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Monthly UPI turnover" suffix="₹" value={form.upi_volume} onChange={(v) => set("upi_volume", v)} placeholder="e.g. 62000" />
              <Field label="Annual income proxy" suffix="₹" value={form.annual_income_proxy} onChange={(v) => set("annual_income_proxy", v)} placeholder="e.g. 300000" />
              <Field label="Utility payment score" suffix="/100" value={form.utility_score} onChange={(v) => set("utility_score", v)} placeholder="0 - 100" />
              <Field label="Utility delays (12 months)" value={form.utility_delays_12m} onChange={(v) => set("utility_delays_12m", v)} placeholder="e.g. 1" />
              <Field label="Average bank balance" suffix="₹" value={form.avg_bank_balance} onChange={(v) => set("avg_bank_balance", v)} placeholder="e.g. 68000" />
            </div>

            <div className="border-t border-slate-100 pt-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Loan amount requested" suffix="₹" required value={form.requested_loan_amount} onChange={(v) => set("requested_loan_amount", v)} placeholder="e.g. 300000" />
                <Field label="Tenure" suffix="months" value={form.loan_tenure_months} onChange={(v) => set("loan_tenure_months", v)} placeholder="36" />
                <Field label="Existing outstanding debt" suffix="₹" value={form.outstanding_debt} onChange={(v) => set("outstanding_debt", v)} placeholder="e.g. 70000" />
                <Field label="Current monthly EMI" suffix="₹" value={form.monthly_emi_obligation} onChange={(v) => set("monthly_emi_obligation", v)} placeholder="e.g. 2400" />
              </div>
            </div>
          </section>
        )}

        {step === 3 && (
          <section className="space-y-4">
            <StepHeading
              title="Document proofs"
              hint="PDF, PNG, JPEG or WebP up to 10 MB. The banker verifies each declaration against these files."
            />

            <label className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center transition hover:border-emerald-400 hover:bg-emerald-50/40">
              <span className="text-2xl">⇧</span>
              <span className="mt-2 text-sm font-medium text-slate-700">Attach proof documents</span>
              <span className="mt-1 text-[11px] text-slate-500">
                Land records, utility bills, inventory receipts, bank/UPI statements
              </span>
              <input
                type="file"
                accept={ACCEPTED}
                multiple
                onChange={addFiles}
                data-testid="doc-upload"
                className="sr-only"
              />
            </label>

            {uploads.length === 0 ? (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                At least one document is required before the bank can verify and score the file.
              </p>
            ) : (
              <ul className="space-y-2">
                {uploads.map((entry) => (
                  <li key={entry.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 p-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold uppercase text-slate-500">
                      {entry.file.name.split(".").pop()?.slice(0, 4)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium text-slate-800">{entry.file.name}</p>
                      <p className="text-[10px] text-slate-500">
                        {(entry.file.size / 1024).toFixed(0)} KB
                      </p>
                    </div>
                    <select
                      value={entry.docType}
                      onChange={(event) =>
                        setUploads((current) =>
                          current.map((item) =>
                            item.id === entry.id
                              ? { ...item, docType: event.target.value as DocType }
                              : item,
                          ),
                        )
                      }
                      className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-[11px] text-slate-700"
                    >
                      {DOC_TYPES.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => setUploads((current) => current.filter((item) => item.id !== entry.id))}
                      className="rounded-md px-2 py-1 text-xs text-rose-600 transition hover:bg-rose-50"
                    >
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {step === 4 && (
          <section className="space-y-4">
            <StepHeading
              title="Review and submit"
              hint="Once submitted the file is locked for verification. Additional proofs can only be added while it is pending."
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <ReviewBlock title="Applicant">
                <Row label="Name" value={form.applicant_name || "—"} />
                <Row label="Segment" value={isFarmer ? "Farmer" : "Shopkeeper / MSME"} />
                <Row label="Region" value={form.region_tier.replace("_", " ")} />
                <Row label="Phone" value={form.phone || "—"} />
              </ReviewBlock>
              <ReviewBlock title="Credit ask">
                <Row label="Requested" value={money(form.requested_loan_amount)} />
                <Row label="Tenure" value={`${form.loan_tenure_months || "—"} months`} />
                <Row label="Existing debt" value={money(form.outstanding_debt)} />
                <Row label="Current EMI" value={money(form.monthly_emi_obligation)} />
              </ReviewBlock>
              <ReviewBlock title="Cash-flow proxies">
                <Row label="UPI turnover / month" value={money(form.upi_volume)} />
                <Row label="Annual income proxy" value={money(form.annual_income_proxy)} />
                <Row label="Utility score" value={form.utility_score || "—"} />
                <Row label="Utility delays (12m)" value={form.utility_delays_12m || "—"} />
              </ReviewBlock>
              <ReviewBlock title="Collateral">
                <Row label="Declared assets" value={money(String(totalAssets))} />
                <Row label="Documents attached" value={String(uploads.length)} />
                <Row label="Segment assets" value={isFarmer ? `${form.land_acres || "—"} acres` : money(form.shop_inventory_val)} />
                <Row label="Equipment" value={money(form.equipment_val)} />
              </ReviewBlock>
            </div>

            <div className="flex items-start gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600">
              <input
                id="declaration-confirm"
                type="checkbox"
                checked={accepted}
                onChange={(event) => setAccepted(event.target.checked)}
                data-testid="declaration-confirm"
                className="mt-0.5 h-4 w-4 rounded border-slate-300 text-emerald-600"
              />
              <label htmlFor="declaration-confirm" className="cursor-pointer">
                I confirm these declarations are accurate and the uploaded documents are genuine.
                I understand the bank will verify them and that the credit decision is made by the
                bank, not by me.
              </label>
            </div>
          </section>
        )}

        {errors.length > 0 && (
          <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
            <ul className="list-disc space-y-0.5 pl-4">
              {errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-4">
          <div className="flex gap-2">
            {step > 0 && (
              <button type="button" onClick={back} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50">
                Back
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setForm(DEMO);
                setErrors([]);
              }}
              className="rounded-lg px-2 py-2 text-xs font-medium text-emerald-700 transition hover:bg-emerald-50"
            >
              Fill demo values
            </button>
          </div>

          {step < 4 ? (
            <button type="button" onClick={advance} className="rounded-lg bg-slate-900 px-4 py-2 text-xs font-semibold text-white transition hover:bg-slate-700">
              Continue
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={busy}
              data-testid="submit-application"
              className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Submitting…" : "Submit for verification"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// small presentation helpers
// ---------------------------------------------------------------------------

function StepHeading({ title, hint }: { title: string; hint: string }) {
  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
      <p className="mt-1 text-xs leading-relaxed text-slate-500">{hint}</p>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  suffix,
  required,
  text,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  suffix?: string;
  required?: boolean;
  text?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
        {label} {required && <span className="text-rose-500">*</span>}
      </span>
      <div className="relative">
        <input
          type={text ? "text" : "number"}
          min={0}
          step={text ? undefined : "any"}
          inputMode={text ? "text" : "decimal"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          className={`w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500/30 ${
            suffix ? "pr-12" : ""
          }`}
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] text-slate-400">
            {suffix}
          </span>
        )}
      </div>
    </label>
  );
}

function Select({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500/30"
      >
        {children}
      </select>
    </label>
  );
}

function ReviewBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{title}</p>
      <dl className="mt-2 space-y-1">{children}</dl>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <dt className="text-slate-500">{label}</dt>
      <dd className="max-w-[60%] truncate text-right font-medium text-slate-800">{value}</dd>
    </div>
  );
}

function money(value: string): string {
  const parsed = numeric(value);
  if (parsed === null) return "—";
  return `₹${parsed.toLocaleString("en-IN")}`;
}
