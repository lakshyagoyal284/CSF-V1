/**
 * Types mirroring the AgriTrust dual-portal API (`backend/portal_schemas.py`).
 *
 * The borrower and banker contracts are deliberately separate: a borrower
 * response has no score fields at all, so the UI cannot accidentally render
 * one.
 */

import type { Explainability, RiskTier, ScoreBand } from "./types";

export type Role = "borrower" | "banker";
export type ApplicantCategory = "farmer" | "small_business";
export type RegionTier = "rural" | "semi_urban" | "urban";

// Kept as stable lookup helpers so both the shell and the report pages can route
// without importing one another.
export const ROLE_ROUTES: Readonly<Record<Role, string>> = Object.freeze({
  borrower: "/",
  banker: "/banker",
});

export function homeForRole(role: Role): string {
  return ROLE_ROUTES[role];
}

export function reportsRouteForRole(role: Role): string {
  return ROLE_ROUTES[role] + "/reports";
}
export type CropType =
  | "rice"
  | "wheat"
  | "cotton"
  | "sugarcane"
  | "horticulture"
  | "pulses"
  | "not_applicable";
export type ShopOwnership = "owned" | "rented" | "not_applicable";
export type ApplicationStatus = "pending" | "verified" | "scored" | "rejected";
export type DocumentStatus = "pending" | "verified" | "rejected";
export type DocType =
  | "land_record"
  | "property_deed"
  | "utility_bill"
  | "inventory_receipt"
  | "bank_statement"
  | "upi_ledger"
  | "equipment_invoice"
  | "other";

export interface PortalUser {
  id: string;
  email: string;
  role: Role;
  full_name: string;
  phone: string | null;
}

export interface AuthResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: PortalUser;
}

export interface DocumentProof {
  id: string;
  doc_type: DocType;
  file_url: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  verified_status: DocumentStatus;
  verified_at: string | null;
  notes: string | null;
  uploaded_at: string;
}

export interface AssetDeclaration {
  land_acres: number | null;
  soil_quality: number | null;
  crop_yield_val: number | null;
  land_market_value: number | null;
  irrigation_score: number | null;
  livestock_val: number | null;
  shop_inventory_val: number | null;
  shop_area_sqft: number | null;
  shop_ownership: ShopOwnership;
  property_val: number | null;
  equipment_val: number | null;
  utility_score: number | null;
  utility_delays_12m: number | null;
  upi_volume: number | null;
  avg_bank_balance: number | null;
  annual_income_proxy: number | null;
  declared_total_asset_value: number;
  verified_fields: string[];
  verified_at: string | null;
}

/** What a borrower is allowed to see. Note the absence of score fields. */
export interface BorrowerApplication {
  id: string;
  status: ApplicationStatus;
  applicant_name: string;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  requested_loan_amount: number;
  loan_tenure_months: number;
  outstanding_debt: number;
  monthly_emi_obligation: number;
  submitted_at: string;
  verified_at: string | null;
  scored_at: string | null;
  rejection_reason: string | null;
  assets: AssetDeclaration | null;
  documents: DocumentProof[];
}

export interface BankerApplicationSummary {
  id: string;
  status: ApplicationStatus;
  applicant_name: string;
  applicant_email: string;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  requested_loan_amount: number;
  submitted_at: string;
  document_count: number;
  verified_document_count: number;
  verified_field_count: number;
  credit_score: number | null;
  risk_tier: RiskTier | null;
  recommended_limit: number | null;
}

export interface ScoreResult {
  id: string;
  application_id: string;
  credit_score: number;
  risk_tier: RiskTier;
  tier_label: string;
  decision: string;
  recommended_limit: number;
  probability_of_default: number;
  score_band: ScoreBand;
  policy_breakdown: {
    recommended_incremental_limit?: number;
    collateral_cap?: number;
    income_cap?: number;
    binding_constraint?: string;
    ltv_applied?: number;
    requested_amount_supported?: boolean;
    indicative_rate_band?: { min_annual_rate: number; max_annual_rate: number };
    model_risk_tier?: RiskTier;
  };
  data_quality: {
    completeness_ratio?: number;
    satisfied_groups?: number;
    total_groups?: number;
    missing_evidence?: string[];
    warnings?: string[];
    allows_pre_approval?: boolean;
  };
  explainability: Explainability;
  model_version: string;
  generated_at: string;
  officer_notes?: string | null;
  decline_option?: string | null;
  decline_reason?: string | null;
}

export interface BankerApplicationDetail extends BankerApplicationSummary {
  borrower_id: string;
  phone: string | null;
  age: number | null;
  dependents: number | null;
  years_in_operation: number | null;
  crop_type: string | null;
  loan_tenure_months: number;
  outstanding_debt: number;
  monthly_emi_obligation: number;
  verified_at: string | null;
  scored_at: string | null;
  rejection_reason: string | null;
  assets: AssetDeclaration | null;
  documents: DocumentProof[];
  latest_score: ScoreResult | null;
}

export interface ApplicationListResponse {
  total: number;
  items: BankerApplicationSummary[];
  limit: number;
  offset: number;
}

export interface BankerReportItem {
  id: string;
  applicant_name: string;
  applicant_email: string;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  requested_loan_amount: number;
  submitted_at: string;
  status: ApplicationStatus;
  rejection_reason: string | null;
  credit_score: number | null;
  risk_tier: RiskTier | null;
  decision: string | null;
  recommended_limit: number | null;
}

export interface BorrowerReportItem {
  id: string;
  applicant_name: string;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  requested_loan_amount: number;
  loan_tenure_months: number;
  submitted_at: string;
  status: ApplicationStatus;
  rejection_reason: string | null;
  credit_score: number | null;
  risk_tier: RiskTier | null;
  decision: string | null;
  recommended_limit: number | null;
}

export interface ReportsListResponse {
  items: Array<BankerReportItem | BorrowerReportItem>;
}

// Keep a typed union visible for code that needs to narrow the list items.
export type ReportsListItem = BankerReportItem | BorrowerReportItem;

export interface AuditEntry {
  id: string;
  application_id: string | null;
  actor_role: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

/** Everything the borrower declares, in the form's own vocabulary. */
export interface ApplicationPayload {
  applicant_name: string;
  consent_accepted: boolean;
  phone?: string | null;
  applicant_category: ApplicantCategory;
  region_tier: RegionTier;
  age?: number | null;
  dependents?: number | null;
  years_in_operation?: number | null;
  crop_type?: CropType;
  requested_loan_amount: number;
  loan_tenure_months: number;
  outstanding_debt: number;
  monthly_emi_obligation: number;
  assets: {
    land_acres?: number | null;
    soil_quality?: number | null;
    crop_yield_val?: number | null;
    land_market_value?: number | null;
    irrigation_score?: number | null;
    livestock_val?: number | null;
    shop_inventory_val?: number | null;
    shop_area_sqft?: number | null;
    shop_ownership?: ShopOwnership;
    property_val?: number | null;
    equipment_val?: number | null;
    utility_score?: number | null;
    utility_delays_12m?: number | null;
    upi_volume?: number | null;
    avg_bank_balance?: number | null;
    annual_income_proxy?: number | null;
  };
}

export interface UploadFileEntry {
  id: string;
  file: File;
  docType: DocType;
}

export interface VerifyAndScorePayload {
  verified_fields?: string[];
  document_ids?: string[];
  verify_all?: boolean;
  officer_notes?: string | null;
  decline_option?: string | null;
  decline_reason?: string | null;
}
