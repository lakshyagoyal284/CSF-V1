# AgriTrust AI

**Alternative credit scoring for the farmers and shopkeepers the bureau never saw.**

AgriTrust is a B2B platform that lets a bank or microfinance institution score a
borrower who has no bureau history and no salary slip. It uses what the borrower
actually owns (land, property, equipment, livestock, inventory) and alternative
cash-flow proxies (UPI turnover, utility payment discipline, bank balances), and
returns a standard **300-900 score**, a risk tier, a recommended credit limit and
the **signed SHAP reasons** behind the decision.

The product is a **dual portal**:

| Borrower portal | Bank underwriting desk |
|---|---|
| Register / sign in | Banker-only sign in |
| Declare identity, assets, cash flow and the ask | Review the pending queue |
| Upload PDF/image proofs | Preview proofs and verify each declaration |
| Track status (never the score) | **Calculate Credit Score** (banker-only) |
| Cannot execute or view the model | Score dial · risk tier · limit · SHAP factors |

Role separation is enforced by the API, not just by hiding buttons:
a borrower token can only create and read its own files, and scoring refuses to
run until the evidence is verified.

Full design: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## 1. Quickstart

```bash
# 1. Python environment
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
.venv/bin/python     -m pip install -r requirements.txt      # macOS / Linux

# 2. Train the model (writes artifacts/credit_model.pkl and metrics)
python train_model.py

# 3. API + interactive docs
uvicorn main:app --reload --port 8000        # http://localhost:8000/docs

# 4. Web app
cd frontend && npm install && npm run dev     # http://localhost:3000
```

The portal database defaults to `sqlite:///./data/agritrust.db` and document
bytes to `data/uploads/`, so no infrastructure is required for a demo.

### Demo accounts

| Role | Email | Password / Provisioning |
|---|---|---|
| Bank officer | `banker@agritrust.ai` | Provision manually: `python scripts/bootstrap_admin.py` |
| Farmer | `farmer@agritrust.ai` | `Farmer@123` (or self-register) |
| Shopkeeper | `shop@agritrust.ai` | `Shop@123` (or self-register) |

> **Security Note:** Bank officer and administrator accounts are never seeded with default hardcoded passwords (C1). Provision your officer account using the secure CLI bootstrap script:
> ```bash
> python scripts/bootstrap_admin.py --email banker@agritrust.ai --name "Priya Nair" --role banker
> ```

On the landing page, click a demo card to prefill borrower credentials. The banker
dashboard has a **Load demo file** button that creates a pending application with
two generated PDF proofs, so the whole workflow can be demonstrated in under a
minute.

---

## 2. Architecture at a glance

```
Borrower portal (Next.js)                 Banker desk (Next.js)
  form + proofs + status                    queue + verification + gauge + SHAP
            │                                          │
            └────────────── JWT + multipart / JSON ────┘
                                 │
                     FastAPI (backend/)
       auth.py · portal_routes.py · portal_service.py
       portal_schemas.py · storage.py · model_registry.py
             │                              │
   PostgreSQL / SQLite                XGBoost + SHAP (ml/)
   users, applications,               frozen feature contract,
   asset_declarations,                PD → 300-900 score,
   document_proofs,                   tier, limit policy,
   credit_score_results,              signed explanations
   underwriting_audit_log
```

- **Database:** `db/agritrust_schema.sql` (PostgreSQL 13+) is the deployment
  schema. SQLAlchemy models in `backend/portal_models.py` also run on SQLite for
  zero-config demos and tests.
- **Storage:** `backend/storage.py` simulates cloud object storage. It validates
  MIME type *and* magic bytes, caps uploads at 10 MB, generates opaque keys and
  refuses path traversal. Swap it for S3/GCS without touching the routes.
- **Auth:** PBKDF2-SHA256 password hashes and HS256 JWTs. Every banker route has
  a server-side role dependency; the database role is authoritative, not the
  token claim.
- **Audit:** submissions (including the borrower's consent record), verifications,
  document rejections, scores and application rejections append to
  `underwriting_audit_log`; scores append to `credit_score_results` so a re-score
  never overwrites history.

---

## 3. API

| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/api/auth/register` | public | Borrower self-registration |
| POST | `/api/auth/login` | public | JWT with role claim |
| GET | `/api/auth/me` | any | Current identity |
| POST | `/api/borrower/application` | borrower | Multipart submission + proofs |
| GET | `/api/borrower/applications` | borrower | Own files, no scores |
| GET | `/api/borrower/applications/{id}` | borrower | Own status |
| POST | `/api/borrower/applications/{id}/documents` | borrower | Add proofs while pending |
| GET | `/api/banker/applications?status=pending` | banker | Queue with evidence counts |
| GET | `/api/banker/applications/{id}` | banker | Full file + latest score |
| POST | `/api/banker/applications/{id}/verify` | banker | Mark fields/proofs verified |
| POST | `/api/banker/verify-and-score/{id}` | banker | Verify + XGBoost + SHAP + persist |
| GET | `/api/banker/applications/{id}/score` | banker | Latest result |
| GET | `/api/banker/applications/{id}/history` | banker | Append-only decision history |
| GET | `/api/banker/applications/{id}/decision-letter` | banker | PDF decision letter |
| POST | `/api/banker/applications/{id}/documents/{doc_id}/reject` | banker | Reject one proof with a reason |
| POST | `/api/banker/applications/{id}/reject` | banker | Reject with reason |
| GET | `/api/banker/applications/{id}/audit` | banker | Workflow trail |
| POST | `/api/banker/demo-applications` | banker | Demo file with PDF proofs |
| GET | `/api/files/{key}` | owner or banker | Authenticated document bytes |

One-click verify + score:

```bash
curl -X POST localhost:8000/api/banker/verify-and-score/$APP_ID \
  -H "Authorization: Bearer $BANKER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"verify_all": true, "officer_notes": "Land record and utility bill checked."}'
```

The response contains `credit_score`, `risk_tier`, `tier_label`, `decision`,
`recommended_limit`, `probability_of_default`, the score band, the limit-policy
breakdown and `explainability.top_factors` / `all_factors`.

The legacy single-portal scoring playground is still available at
`/api/score`, `/api/simulate` and `/console`; those endpoints now require a
banker JWT as well, so the model is never executable without the banker role.

---

## 4. The model

`train_model.py` (implemented in `ml/train.py`):

1. Generates a reproducible synthetic portfolio of 1,000+ farmer and shopkeeper
   applications with a latent creditworthiness process, a calibrated repayment
   base rate and label noise.
2. Trains XGBoost to predict `repaid_on_time`.
3. Evaluates on a stratified hold-out (ROC-AUC, Gini, KS, PR-AUC, Brier, decile
   calibration, per-segment and per-tier default rates).
4. Fits SHAP and stores grouped global importance.
5. Saves `artifacts/credit_model.pkl`, `training_metrics.json`,
   `feature_importance.csv`, `shap_global_importance.csv` and the generated
   portfolio CSV.

Generated artifact (git-ignored; rebuild with `python train_model.py`), 7,800 training / 2,400 test rows:

```
ROC-AUC  0.8715    Gini 0.7429    KS 0.5825
PR-AUC   0.9528    Brier 0.1097
Tier cut-offs: LOW >= 680, MEDIUM 580-679, HIGH < 580
Tier default rates: LOW 5.9%, MEDIUM 25.8%, HIGH 62.9%
```

**Model status.** The served model is the synthetic-portfolio model above. The
optional mixed-source model (`python scripts/train_combined.py`) is
experimental: its bank/mortgage/rating labels are proxies, not repayment
outcomes, and most of its 0.885 hold-out AUC comes from identifying the source
dataset (a source-only baseline scores 0.844; within-bank AUC is 0.528). Read
[`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) before using it.

The scorecard maps repayment odds to 300-900:

```
score = 524.5 + 89.64 · ln(p_repay / (1 − p_repay))
```

The limit is the tighter of a collateral LTV cap and an income cap (debt service
≤ 30% of income), haircut by tier. SHAP attributions are converted into the same
score-point unit shown on the dial.

**Training and serving share one feature contract** (`ml/config.py` +
`ml/features.py`), and missing values are never imputed on one side only: XGBoost
learns the missing-value direction from the same NaN matrix the API feeds it.

---

## 5. Frontend

Next.js 16 (App Router) + React 19 + Tailwind v4, TypeScript strict.

| Route | Component | Purpose |
|---|---|---|
| `/` | `PortalLanding` | Role explanation, sign-in, borrower registration, demo accounts |
| `/borrower` | `BorrowerPortal` → `BorrowerApplicationForm` | 5-step form, uploads, status tracker (no score) |
| `/banker` | `BankerUnderwritingDashboard` | Queue, evidence verification, preview modal, score, SHAP |
| `/console` | legacy `UnderwritingDashboard` | Model playground (not part of the portal workflow) |

The requested `.jsx` blueprints are implemented as `.tsx` because the project is
TypeScript-strict. Key components:

- `BorrowerApplicationForm.tsx` - identity, assets, cash flow and ask, document
  proofs, review and submit. Segment-aware payloads; empty numbers stay missing
  rather than becoming zero.
- `BankerUnderwritingDashboard.tsx` - table of applications, per-field and
  per-document verification, "Calculate Credit Score", 300-900 SVG gauge, risk
  tier badge, recommended limit, signed SHAP bars, officer notes, rejection flow
  and audit trail.
- `DocumentPreviewModal.tsx` - fetches proofs with the bearer token and renders
  them from a blob URL (a raw `<iframe src>` cannot authenticate).
- `ScoreDial.tsx`, `RiskTierBadge.tsx` - reused from the original scoring console.

`NEXT_PUBLIC_API_BASE` defaults to `http://localhost:8000`.

---

## 6. Database

```bash
createdb agritrust
psql -d agritrust -f db/agritrust_schema.sql

export ACS_PORTAL_DATABASE_URL="postgresql+psycopg://agritrust:agritrust@localhost:5432/agritrust"
export ACS_PORTAL_CREATE_TABLES=false      # migrations own the schema
```

Tables: `users`, `applications`, `asset_declarations`, `document_proofs`,
`credit_score_results`, `underwriting_audit_log`.

Views: `v_underwriting_queue`, `v_scoring_funnel`, `v_reason_code_frequency`.

Workflow: `pending → verified → scored`, with `rejected` as a terminal branch.
Scoring requires at least one verified declaration and one verified document;
re-scores append history; every transition is audited.

---

## 7. Configuration

| Variable | Default | Notes |
|---|---|---|
| `ACS_MODEL_PATH` | `artifacts/credit_model.pkl` | Model bundle |
| `ACS_PORTAL_DATABASE_URL` | `sqlite:///./data/agritrust.db` | PostgreSQL in deployment |
| `ACS_PORTAL_CREATE_TABLES` | auto | Create on SQLite; never on PostgreSQL |
| `ACS_PORTAL_SEED_DEMO` | `true` | Seeds demo logins |
| `ACS_JWT_SECRET` | insecure dev value | **Override in any shared environment** |
| `ACS_JWT_EXPIRY_MINUTES` | `480` | Access-token lifetime |
| `ACS_UPLOAD_DIR` | `data/uploads` | Simulated object storage |
| `ACS_MAX_UPLOAD_MB` | `10` | Per-file cap |
| `ACS_CORS_ORIGINS` | `http://localhost:3000,...` | Allowed browser origins |

---

## 8. Tests and verification

```bash
.venv/Scripts/python -m pytest tests -q        # 136 tests
.venv/Scripts/python scripts/check_schema_consistency.py
.venv/Scripts/python scripts/check_portal_schema.py
cd frontend && npm run lint && npm run build
node scripts/verify-portal.mjs                # API :8000 + UI :3000 required
```

Coverage includes JWT expiry/tampering, role escalation, borrower isolation,
multipart submission, file-content validation, path traversal, the verification
gate, SHAP response shape, re-score history, rejection and audit trail, plus
consent enforcement, per-document rejection, the decision letter and the
external-dataset source tagging.

Browser verification on this checkout: **13/13 checks passed** - banker login →
demo file → authenticated PDF preview → verify → score **890 / LOW / ₹2,30,000**
with SHAP factors → sign out → borrower login → full multi-step submission →
"Application submitted". No browser console errors.

---

## 9. Honest limitations

This is a hackathon-grade prototype, not a live credit system:

- **Synthetic data.** Every reported metric describes the simulator, not a real
  loan book. Nothing has been validated against actual repayments.
- **No probability calibration layer** (isotonic/Platt) yet; XGBoost is
  reasonably calibrated on this data (Brier 0.112) but pricing must not rely on
  it before holdout calibration.
- **Score clipping** at 300/900 loses ranking below ~1.3% and above ~93% PD.
- **No rate limiting, MFA, email verification, PII encryption or malware
  scanning.** The demo JWT secret is deliberately insecure.
- **Declared data is trusted after verification.** Real Account Aggregator/UPI
  and utility feeds, reconciliation and consent artefacts are the next
  integration.
- **Fairness monitoring exists, sign-off does not.** `training_metrics.json`
  now reports default rate, AUC and pre-approval share by segment, region and age
  band, and the model card documents the result. A production release still
  needs disparity thresholds, adverse-action monitoring and a challenger model.
- **The PostgreSQL DDL has not been executed against a live server in this
  build**; it is cross-checked against the ORM, not migrated. SQLite is what the
  tests run on.

---

## 10. Repository layout

```
main.py                          ASGI entry point (`uvicorn main:app`)
train_model.py                   generate → train → evaluate → save artifacts
ml/                              shared model, features, scorecard, SHAP
backend/
  main.py                        FastAPI app + legacy scoring routes
  portal_routes.py               auth / borrower / banker / documents
  portal_service.py              workflow state machine, audit, scoring call
  portal_schemas.py              contracts + product→model mapping
  portal_models.py               SQLAlchemy models (Postgres + SQLite)
  portal_db.py                   engine, sessions, demo seeding
  auth.py                        PBKDF2, JWT, RBAC dependencies
  storage.py                     simulated object storage + validation
  report.py                      shared PDF writer + decision letter
  demo_data.py                   demo file with generated PDF proofs
  model_registry.py              model artifact + cached SHAP explainer
  service.py                     scoring orchestration
db/
  agritrust_schema.sql           dual-portal PostgreSQL DDL
  schema.sql                     legacy single-portal schema
scripts/
  train_combined.py              experimental mixed-source training CLI
  check_portal_schema.py         portal DDL ↔ ORM consistency check
frontend/
  src/app/{page,borrower,banker,console}
  src/components/portal/         portal components
  src/lib/portal-api.ts          typed client + authenticated blob fetch
  scripts/verify-portal.mjs      headless browser end-to-end check
docs/ARCHITECTURE.md             end-to-end technical architecture
docs/MODEL_CARD.md               data, metrics, fairness and limitations
tests/                           pytest suite (136 tests)
artifacts/                       model, metrics, importance (generated)
data/                            synthetic portfolio (generated)
```

> This prototype must not be used for live credit decisions. The dashboard footer
> disclaimer is deliberate.
