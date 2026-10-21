# AgriTrust AI - Technical Architecture

AgriTrust is a B2B alternative credit-scoring platform for banks and microfinance
institutions. It scores unbanked farmers and small business owners on tangible
assets (land, property, equipment, inventory) and alternative cash-flow proxies
(UPI turnover, utility payment discipline, bank balances).

The defining product rule is **role separation**:

- **Borrowers** declare assets, upload proofs, and track status. They cannot run
  the model, read a score, or see the risk tier.
- **Bank officers** verify the evidence and are the only role that can execute
  the scorecard. They see the 300-900 score, tier, recommended limit and signed
  SHAP reasons.

That rule is enforced in the API, not just hidden in the UI.

---

## 1. System flow

```
┌─────────────────────────────┐          ┌──────────────────────────────────┐
│ Borrower portal             │          │ Banker underwriting desk         │
│ Next.js 16 / React 19       │          │ Next.js 16 / React 19            │
│                             │          │                                  │
│ /  landing + auth           │          │ /banker                          │
│ /borrower multi-step form   │          │  queue · evidence review         │
│   identity → assets →       │          │  document preview modal          │
│   cash flow → proofs →      │          │  verify fields + proofs          │
│   review                    │          │  Calculate Credit Score          │
│ status tracker (no score)   │          │  gauge · tier · limit · SHAP     │
└──────────────┬──────────────┘          └────────────────┬─────────────────┘
               │  multipart + JWT                         │ JSON + JWT
               ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ FastAPI service (backend/)                                               │
│                                                                          │
│  auth.py           PBKDF2 password hashes · HS256 JWT · RBAC dependencies│
│  portal_routes.py  /api/auth · /api/borrower · /api/banker · /api/files  │
│  portal_service.py workflow state machine · ownership checks · audit     │
│  portal_schemas.py request/response contracts · product↔model mapping    │
│  storage.py        object-storage adapter (local, validated uploads)     │
│  model_registry.py lazy model artifact + cached SHAP explainer           │
│  service.py        scoring orchestration (shared with legacy API)        │
└───────────────┬──────────────────────────────┬───────────────────────────┘
                │                              │
                ▼                              ▼
┌──────────────────────────────┐   ┌──────────────────────────────────────┐
│ PostgreSQL (production DDL)  │   │ ml/ offline + shared scoring library │
│ db/agritrust_schema.sql      │   │  config.py      feature contract     │
│  users                       │   │  data_generator.py synthetic book    │
│  applications                │   │  features.py    ratios + one-hot     │
│  asset_declarations          │   │  scoring.py     PD→score→tier→limit  │
│  document_proofs             │   │  explain.py     SHAP→score points    │
│  credit_score_results        │   │  train.py       XGBoost + metrics    │
│  underwriting_audit_log      │   └──────────────┬───────────────────────┘
└──────────────────────────────┘                  │
                                      artifacts/credit_model.pkl
                                      (model + feature contract + metadata)
```

SQLite is the default database for a zero-config demo and for tests; the same
SQLAlchemy models run against PostgreSQL. The reviewed PostgreSQL DDL is the
source of truth for a real deployment.

---

## 2. Repository map

| Path | Responsibility |
|---|---|
| `backend/portal_routes.py` | Dual-portal HTTP surface |
| `backend/portal_service.py` | Submission, verification, scoring, rejection, audit |
| `backend/portal_schemas.py` | API contracts and product→model field mapping |
| `backend/auth.py` | Password hashing, JWT issue/verify, role dependencies |
| `backend/storage.py` | Simulated object storage + content validation |
| `backend/portal_models.py` | SQLAlchemy models mirroring the PostgreSQL DDL |
| `backend/portal_db.py` | Engine/session lifecycle and demo seeding |
| `backend/demo_data.py` | One-click demo file with generated PDF proofs |
| `backend/main.py` | FastAPI app, lifespan, CORS, legacy scoring routes |
| `backend/service.py` | Single scoring implementation used by both portals |
| `db/agritrust_schema.sql` | PostgreSQL DDL, indexes, trigger, views |
| `ml/` | Synthetic portfolio, feature contract, scorecard, SHAP, training |
| `frontend/src/app/borrower` | Borrower route (`RoleGate role="borrower"`) |
| `frontend/src/app/banker` | Banker route (`RoleGate role="banker"`) |
| `frontend/src/components/portal/BorrowerApplicationForm.tsx` | Multi-step form + uploads |
| `frontend/src/components/portal/BankerUnderwritingDashboard.tsx` | Queue, verification, score gauge, SHAP |
| `frontend/src/components/portal/DocumentPreviewModal.tsx` | Authenticated blob preview |
| `tests/test_portal_*.py` | Auth/RBAC, workflow, storage tests |

The requested component names map to TypeScript files because this repository is
TypeScript-strict: `BorrowerApplicationForm.jsx` →
`BorrowerApplicationForm.tsx`, `BankerUnderwritingDashboard.jsx` →
`BankerUnderwritingDashboard.tsx`.

---

## 3. Data model

### 3.1 Tables

**`users`** - `id`, `email` (case-insensitive unique), `password_hash`, `role`
(`borrower` | `banker`), `full_name`, `phone`, `is_active`, `last_login_at`,
timestamps.

**`applications`** - one credit file per submission. Holds a point-in-time
identity snapshot plus the ask (`requested_loan_amount`, `loan_tenure_months`,
`outstanding_debt`, `monthly_emi_obligation`) and workflow stamps
(`status`, `submitted_at`, `verified_at/by`, `scored_at`, `rejected_at/by`,
`rejection_reason`). Status is constrained to
`pending | verified | scored | rejected`.

**`asset_declarations`** - one row per application. Core product fields:
`land_acres`, `soil_quality`, `crop_yield_val`, `shop_inventory_val`,
`equipment_val`, `utility_score`, `upi_volume`. Additional fields capture
`land_market_value`, `irrigation_score`, `livestock_val`, `shop_area_sqft`,
`shop_ownership`, `property_val`, `utility_delays_12m`, `avg_bank_balance`,
`annual_income_proxy`. `declared_total_asset_value` is a generated column.
`verified_fields` is JSONB listing exactly which declarations a banker checked.

**`document_proofs`** - metadata only: `doc_type`, `file_url`, opaque
`storage_key`, original filename, content type, size, `verified_status`
(`pending | verified | rejected`), verifier and timestamp. Bytes live behind the
storage adapter.

**`credit_score_results`** - append-only decision history: `credit_score`,
`risk_tier`, `recommended_limit`, `shap_explanations_json`, `generated_at`, plus
`probability_of_default`, `decision`, `score_band`, `data_quality`,
`policy_breakdown`, `model_version`, `decided_by`, `officer_notes`. A re-score
inserts a new row.

**`underwriting_audit_log`** - application, actor, role, action and JSONB
details for submission, verification, scoring and rejection.

### 3.2 Workflow

```
pending ──verify──▶ verified ──score──▶ scored
   │                   │
   └──────reject───────┴──────────────▶ rejected
```

- Documents can only be added while an application is `pending`.
- Scoring is refused unless at least one declaration field and one document are
  verified.
- A rejected file cannot be verified or scored.
- A scored file cannot be rejected (a new application is required).
- Every transition writes an audit row.

### 3.3 Views

- `v_underwriting_queue` - queue with verified evidence counts and latest score.
- `v_scoring_funnel` - monthly volume, average score/PD and recommended limits by tier.
- `v_reason_code_frequency` - adverse-action reason monitoring from the SHAP JSONB.

---

## 4. API contract

All routes are under `/api`. Except for register/login, every route requires
`Authorization: Bearer <jwt>`.

### Authentication

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/auth/register` | public | Borrower self-registration; returns a token |
| POST | `/auth/login` | public | Email/password → JWT (`role` claim) |
| GET | `/auth/me` | any | Current identity |

### Borrower (role=borrower)

| Method | Path | Purpose |
|---|---|---|
| POST | `/borrower/application` | Multipart: `payload` JSON + files + `doc_types` |
| GET | `/borrower/applications` | Own applications; never includes a score |
| GET | `/borrower/applications/{id}` | Own application status |
| POST | `/borrower/applications/{id}/documents` | Add proofs while pending |

### Banker (role=banker)

| Method | Path | Purpose |
|---|---|---|
| GET | `/banker/applications?status=pending` | Queue with document URLs and evidence counts |
| GET | `/banker/applications/{id}` | Full file + latest score |
| POST | `/banker/applications/{id}/verify` | Mark fields/proofs verified |
| POST | `/banker/verify-and-score/{id}` | Verify + XGBoost + SHAP + persist |
| GET | `/banker/applications/{id}/score` | Latest persisted result |
| POST | `/banker/applications/{id}/reject` | Reject with a required reason |
| GET | `/banker/applications/{id}/audit` | Workflow trail |
| POST | `/banker/demo-applications` | Demo file with generated PDF proofs |

### Documents

`GET /files/{key}` streams the bytes inline. A banker can read any proof; a
borrower can only read proofs on their own applications. Ownership failures
return 404 to avoid confirming that another applicant's file exists.

### Example: submit an application

```bash
curl -X POST localhost:8000/api/borrower/application \
  -H "Authorization: Bearer $BORROWER_TOKEN" \
  -F 'payload={
        "applicant_name":"Asha Kumari",
        "applicant_category":"farmer",
        "region_tier":"rural",
        "requested_loan_amount":300000,
        "loan_tenure_months":36,
        "assets":{
          "land_acres":4.5,"soil_quality":74,"crop_yield_val":236000,
          "land_market_value":1450000,"irrigation_score":78,
          "livestock_val":180000,"property_val":520000,
          "equipment_val":760000,"utility_score":82,
          "utility_delays_12m":1,"upi_volume":62000,
          "avg_bank_balance":68000,"annual_income_proxy":300000
        }
      }' \
  -F 'documents=@land_record.pdf' -F 'doc_types=land_record' \
  -F 'documents=@utility_statement.pdf' -F 'doc_types=utility_bill'
```

### Example: banker one-click verify + score

```bash
curl -X POST localhost:8000/api/banker/verify-and-score/$APP_ID \
  -H "Authorization: Bearer $BANKER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"verify_all": true, "officer_notes": "RoR and utility statement checked."}'
```

Response (abridged):

```json
{
  "credit_score": 874,
  "risk_tier": "LOW",
  "tier_label": "Pre-Approved",
  "decision": "PRE_APPROVED",
  "recommended_limit": 230000,
  "probability_of_default": 0.019942,
  "score_band": {"low_risk_min": 680, "medium_risk_min": 580},
  "policy_breakdown": {
    "collateral_cap": 1600000,
    "income_cap": 230000,
    "binding_constraint": "income",
    "indicative_rate_band": {"min_annual_rate": 9.5, "max_annual_rate": 11.5}
  },
  "explainability": {
    "summary": "Main strengths: ...",
    "top_factors": [
      {
        "label": "Proposed Debt Service Ratio",
        "value": "9%",
        "score_impact_points": 38,
        "direction": "reduces_risk",
        "narrative": "..."
      }
    ],
    "all_factors": ["..."]
  },
  "model_version": "acs-1.0.0-2026-10-19",
  "generated_at": "2026-10-20T07:31:21Z"
}
```

---

## 5. ML pipeline

`train_model.py` (implemented in `ml/train.py`) is a single reproducible command:

```bash
python train_model.py                    # synthetic portfolio + training + artifacts
python train_model.py --refresh-data     # regenerate the portfolio
python train_model.py --n-records 8000 --seed 7
```

It generates 1,000+ synthetic farmer/shopkeeper applications with a latent
creditworthiness process, a calibrated repayment base rate and label noise;
trains an XGBoost classifier for `repaid_on_time`; evaluates on a stratified
hold-out split (ROC-AUC, Gini, KS, PR-AUC, Brier, decile calibration, per-segment
and per-tier default rates); and writes:

- `artifacts/credit_model.pkl` - model bundle + frozen feature contract + metadata
- `artifacts/training_metrics.json`
- `artifacts/feature_importance.csv`
- `artifacts/shap_global_importance.csv`
- `data/synthetic_portfolio.csv`

### 5.1 Product fields → model features

| Borrower declares | Model feature |
|---|---|
| `land_acres` | `land_size_acres` |
| `soil_quality` | `soil_quality_score` |
| `crop_yield_val` | `crop_yield_annual_value` |
| `shop_inventory_val` | `inventory_value` |
| `equipment_val` | `equipment_value` |
| `utility_score` | `utility_payment_consistency_score` |
| `upi_volume` | `monthly_upi_turnover` |
| `property_val` | `property_value` |
| `livestock_val` | `livestock_value` |
| `utility_delays_12m` | `utility_payment_delays_12m` |
| `avg_bank_balance` | `avg_monthly_bank_balance` |
| `annual_income_proxy` | `annual_income_proxy` |

The mapping exists only in `ApplicationCreate.to_score_request()`, so the model
never learns the product's column names and the API cannot drift from the frozen
feature contract.

### 5.2 Scorecard and limit

Default probability → score uses a points-to-double-the-odds transform:

```
score = 524.5 + 89.64 · ln(p_repay / (1 − p_repay))   clipped to [300, 900]
```

Policy cut-offs: **LOW ≥ 680** (pre-approved), **MEDIUM 580-679** (manual
review), **HIGH < 580**. The recommended limit is the tighter of a collateral cap
(LTV × pledged value) and an income cap (debt service ≤ 30% of income), then
haircut by tier, rounded to ₹5,000 and floored at ₹25,000.

### 5.3 Explanations

SHAP `TreeExplainer` values are grouped into credit concepts, converted into
score points and returned as `top_factors`, `positive_factors`,
`negative_factors` and the full waterfall. The signed values use the same unit as
the dial, and additivity against the served margin is pinned by tests.

---

## 6. Security model

| Control | Implementation |
|---|---|
| Password storage | PBKDF2-HMAC-SHA256, 210k iterations, per-user salt, constant-time compare, transparent rehash on login |
| Session | HS256 JWT with `sub`, `role`, `iss`, `iat`, `nbf`, `exp` (8h default) |
| Authorisation | `require_role()` dependencies on every banker route; role read from the DB, not trusted from the token |
| Borrower isolation | Ownership check on every read; 404 rather than 403 to avoid disclosing other files |
| Score exposure | Separate borrower response schema with no score fields |
| Evidence gate | Scoring refused without at least one verified declaration and one verified document |
| Upload validation | MIME allow-list + magic-byte sniffing + declared/detected match + 10 MB cap |
| Path traversal | Server-generated opaque keys, strict key regex, resolved-path containment |
| Document access | Bearer-authenticated fetch; previews use short-lived blob URLs |
| Audit | Append-only application log plus append-only score history |
| Secrets | `ACS_JWT_SECRET` env var; demo default is explicitly insecure |

Not included in this hackathon build: rate limiting, MFA, email verification,
PII field encryption, virus scanning, signed object-storage URLs, row-level
security, fairness testing. These are required before production.

---

## 7. Configuration

All backend settings use the `ACS_` prefix and can live in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `ACS_MODEL_PATH` | `artifacts/credit_model.pkl` | Model artifact |
| `ACS_PORTAL_DATABASE_URL` | `sqlite:///./data/agritrust.db` | Portal DB (PostgreSQL in production) |
| `ACS_PORTAL_CREATE_TABLES` | auto | Create on SQLite; PostgreSQL uses the DDL |
| `ACS_PORTAL_SEED_DEMO` | `true` | Seed demo logins |
| `ACS_JWT_SECRET` | dev value | **Override in any shared environment** |
| `ACS_JWT_EXPIRY_MINUTES` | `480` | Token lifetime |
| `ACS_UPLOAD_DIR` | `data/uploads` | Simulated object storage root |
| `ACS_MAX_UPLOAD_MB` | `10` | Per-file size cap |
| `ACS_CORS_ORIGINS` | `http://localhost:3000,...` | Browser origins |

Frontend: `NEXT_PUBLIC_API_BASE` (defaults to `http://localhost:8000`).

For PostgreSQL, apply `db/agritrust_schema.sql`, then point
`ACS_PORTAL_DATABASE_URL` at it and set `ACS_PORTAL_CREATE_TABLES=false`
(migrations own the schema).

---

## 8. Testing and verification

```bash
# backend: 126 tests, including 36 portal-specific tests
.venv/Scripts/python -m pytest tests -q

# frontend
cd frontend
npm run lint
npm run build
node scripts/verify-portal.mjs   # needs API :8000 and UI :3000
```

The original `/api/score`, `/api/simulate`, `/api/assessment-history` and
`/api/admin/reload-model` routes are banker-only too, so no scoring surface is
reachable by a borrower. `/console` is gated with the same role.

Portal test coverage:

- login success/failure, case-insensitive email, no account enumeration
- tampered and missing tokens
- borrower cannot reach banker routes; banker cannot submit; role escalation refused
- multipart submission, segment validation, income-proxy requirement
- document content sniffing, size limit, path traversal
- ownership of documents and applications
- verification gate (score refused before evidence)
- full verify-and-score response (score, tier, limit, SHAP, policy)
- re-score appends history; audit trail entries
- rejection and re-score refusal
- demo file with previewable PDFs

Browser verification (this checkout): 13/13 checks passed, including bank
login → demo file → PDF preview → verify → gauge 874/LOW/₹2,30,000 → borrower
login → multi-step submission → "Application submitted". No browser console
errors.

---

## 9. Production hardening roadmap

1. Deploy PostgreSQL with the reviewed DDL, migrations, backups and RLS.
2. Replace the local storage adapter with private S3/GCS + signed URLs and
   asynchronous malware scanning.
3. Add rate limiting, MFA for bankers, refresh tokens and a revocation list.
4. Encrypt PII, mask document metadata in logs, and add data-retention controls.
5. Validate the model on real repayment data; calibrate PD; test fairness across
   region, gender and segment; monitor drift.
6. Integrate Account Aggregator / UPI / utility APIs instead of declared values,
   and reconcile the feeds against declarations.
7. Make the audit log and score results append-only at the database role level.
8. Add e-signature and consent artefacts to the application file.
