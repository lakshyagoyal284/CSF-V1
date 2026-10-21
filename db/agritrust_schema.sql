-- ===========================================================================
--  AgriTrust AI - dual-portal alternative credit scoring schema
--  Target: PostgreSQL 13+ (IDENTITY, JSONB, generated columns)
--
--  Workflow this schema implements
--  -------------------------------
--    borrower registers  -> users (role='borrower')
--    borrower submits    -> applications (status='pending')
--                           asset_declarations (declared collateral)
--                           document_proofs   (land records, bills, receipts)
--    banker reviews      -> document_proofs.verified_status, asset_declarations.verified_fields
--    banker scores       -> credit_score_results (score, tier, limit, SHAP JSONB)
--    every step is logged in underwriting_audit_log
--
--  Why the workflow is modelled explicitly
--  ---------------------------------------
--  The ML model must never be reachable by the applicant. The `status` column
--  is therefore the state machine that gates scoring:
--
--      pending ──verify──> verified ──score──> scored
--         └──────────── reject ─────────────> rejected
--
--  Only a bank officer can move a file to `verified`/`scored`; the API enforces
--  the role, and this schema enforces the valid states.
--
--  Usage:
--      createdb agritrust
--      psql -d agritrust -f db/agritrust_schema.sql
--
--  The API also supports SQLite for zero-config demos. The SQLAlchemy models in
--  `backend/portal_models.py` mirror this file; PostgreSQL remains the source of
--  truth for a real deployment.
-- ===========================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Enum-like domains
--    Kept as VARCHAR + CHECK rather than native ENUM so the same model layer
--    can run on SQLite (demo/tests) and PostgreSQL (deployment).
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 1. users - borrowers and bank officers
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id              VARCHAR(36)  PRIMARY KEY,
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,       -- PBKDF2-SHA256, never plaintext
    role            VARCHAR(16)  NOT NULL CHECK (role IN ('borrower', 'banker')),
    full_name       VARCHAR(160) NOT NULL,
    phone           VARCHAR(20),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT users_email_format CHECK (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    CONSTRAINT users_phone_format CHECK (phone IS NULL OR phone ~ '^[0-9+\- ]{8,20}$')
);

-- Case-insensitive uniqueness: Alice@bank.com and alice@bank.com are one account.
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower ON users (lower(email));
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

COMMENT ON TABLE users IS
    'Portal identities. `role` drives every authorisation decision in the API: '
    'borrowers may only create and read their own applications, bankers may '
    'verify and score.';

-- ---------------------------------------------------------------------------
-- 2. applications - the underwriting file header
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS applications (
    id                     VARCHAR(36) PRIMARY KEY,
    borrower_id            VARCHAR(36) NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    status                 VARCHAR(16) NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'verified', 'scored', 'rejected')),

    -- Applicant-declared identity. Denormalised on purpose: a credit file is a
    -- point-in-time record and must not change when the user later edits their
    -- profile.
    applicant_name         VARCHAR(160) NOT NULL,
    phone                  VARCHAR(20),
    applicant_category     VARCHAR(24) NOT NULL
                           CHECK (applicant_category IN ('farmer', 'small_business')),
    region_tier            VARCHAR(24) NOT NULL DEFAULT 'rural'
                           CHECK (region_tier IN ('rural', 'semi_urban', 'urban')),
    age                    SMALLINT CHECK (age IS NULL OR age BETWEEN 18 AND 100),
    dependents             SMALLINT CHECK (dependents IS NULL OR dependents BETWEEN 0 AND 30),
    years_in_operation     SMALLINT CHECK (years_in_operation IS NULL OR years_in_operation BETWEEN 0 AND 80),
    crop_type              VARCHAR(24) DEFAULT 'not_applicable'
                           CHECK (crop_type IN ('rice','wheat','cotton','sugarcane',
                                                'horticulture','pulses','not_applicable')),

    -- The facility requested and the existing debt burden.
    requested_loan_amount  NUMERIC(16,2) NOT NULL CHECK (requested_loan_amount > 0),
    loan_tenure_months     SMALLINT NOT NULL DEFAULT 36
                           CHECK (loan_tenure_months BETWEEN 6 AND 360),
    outstanding_debt       NUMERIC(16,2) NOT NULL DEFAULT 0 CHECK (outstanding_debt >= 0),
    monthly_emi_obligation NUMERIC(16,2) NOT NULL DEFAULT 0 CHECK (monthly_emi_obligation >= 0),

    -- Workflow stamps (who did what, and when).
    submitted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at            TIMESTAMPTZ,
    verified_by            VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    scored_at              TIMESTAMPTZ,
    rejected_at            TIMESTAMPTZ,
    rejected_by            VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    rejection_reason       TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT applications_scored_requires_verification CHECK (
        status <> 'scored' OR verified_at IS NOT NULL
    ),
    CONSTRAINT applications_rejected_requires_reason CHECK (
        status <> 'rejected' OR rejection_reason IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_applications_status_submitted
    ON applications (status, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_applications_borrower
    ON applications (borrower_id, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_applications_category
    ON applications (applicant_category);

COMMENT ON TABLE applications IS
    'One row per credit application. `status` is the workflow state machine and '
    'is the only thing that authorises scoring: only a banker can advance a file '
    'to verified/scored.';

-- ---------------------------------------------------------------------------
-- 3. asset_declarations - tangible collateral + alternative cash-flow proxies
-- ---------------------------------------------------------------------------
--  Column names follow the product vocabulary (land_acres, soil_quality,
--  shop_inventory_val ...) rather than the model's internal feature names. The
--  service layer owns the mapping, in one place, so the API can be refactored
--  without touching the credit file.

CREATE TABLE IF NOT EXISTS asset_declarations (
    id                    VARCHAR(36) PRIMARY KEY,
    application_id        VARCHAR(36) NOT NULL UNIQUE
                          REFERENCES applications (id) ON DELETE CASCADE,

    -- Farming collateral
    land_acres            NUMERIC(10,2) CHECK (land_acres IS NULL OR land_acres >= 0),
    soil_quality          SMALLINT CHECK (soil_quality IS NULL OR soil_quality BETWEEN 0 AND 100),
    crop_yield_val        NUMERIC(16,2) CHECK (crop_yield_val IS NULL OR crop_yield_val >= 0),
    land_market_value     NUMERIC(16,2) CHECK (land_market_value IS NULL OR land_market_value >= 0),
    irrigation_score      SMALLINT CHECK (irrigation_score IS NULL OR irrigation_score BETWEEN 0 AND 100),
    livestock_val         NUMERIC(16,2) CHECK (livestock_val IS NULL OR livestock_val >= 0),

    -- Business collateral
    shop_inventory_val    NUMERIC(16,2) CHECK (shop_inventory_val IS NULL OR shop_inventory_val >= 0),
    shop_area_sqft        NUMERIC(10,2) CHECK (shop_area_sqft IS NULL OR shop_area_sqft >= 0),
    shop_ownership        VARCHAR(24) DEFAULT 'not_applicable'
                          CHECK (shop_ownership IN ('owned', 'rented', 'not_applicable')),

    -- Shared collateral
    property_val          NUMERIC(16,2) CHECK (property_val IS NULL OR property_val >= 0),
    equipment_val         NUMERIC(16,2) CHECK (equipment_val IS NULL OR equipment_val >= 0),

    -- Alternative cash-flow proxies
    utility_score         SMALLINT CHECK (utility_score IS NULL OR utility_score BETWEEN 0 AND 100),
    utility_delays_12m    SMALLINT CHECK (utility_delays_12m IS NULL OR utility_delays_12m BETWEEN 0 AND 36),
    upi_volume            NUMERIC(16,2) CHECK (upi_volume IS NULL OR upi_volume >= 0),
    avg_bank_balance      NUMERIC(16,2) CHECK (avg_bank_balance IS NULL OR avg_bank_balance >= 0),
    annual_income_proxy   NUMERIC(16,2) CHECK (annual_income_proxy IS NULL OR annual_income_proxy >= 0),

    -- Collateral total maintained by the database, not by application code.
    declared_total_asset_value NUMERIC(18,2) GENERATED ALWAYS AS (
        COALESCE(land_market_value, 0) + COALESCE(property_val, 0) +
        COALESCE(equipment_val, 0)     + COALESCE(livestock_val, 0) +
        COALESCE(shop_inventory_val, 0)
    ) STORED,

    -- Verification: the list of field names a banker has checked against the
    -- uploaded proofs, e.g. ["land_acres","land_market_value"].
    verified_fields       JSONB NOT NULL DEFAULT '[]'::jsonb,
    verified_by           VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    verified_at           TIMESTAMPTZ,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_asset_declarations_app
    ON asset_declarations (application_id);
CREATE INDEX IF NOT EXISTS idx_asset_declarations_verified_fields
    ON asset_declarations USING GIN (verified_fields jsonb_path_ops);

COMMENT ON TABLE asset_declarations IS
    'Declared collateral and alternative cash-flow evidence, one row per '
    'application. `verified_fields` records exactly which declarations a banker '
    'has reconciled against document_proofs.';

-- ---------------------------------------------------------------------------
-- 4. document_proofs - simulated cloud object storage metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS document_proofs (
    id                VARCHAR(36) PRIMARY KEY,
    application_id    VARCHAR(36) NOT NULL
                      REFERENCES applications (id) ON DELETE CASCADE,
    doc_type          VARCHAR(40) NOT NULL
                      CHECK (doc_type IN ('land_record', 'property_deed', 'utility_bill',
                                          'inventory_receipt', 'bank_statement', 'upi_ledger',
                                          'equipment_invoice', 'other')),
    file_url          TEXT NOT NULL,                -- /api/files/<storage_key>
    storage_key       VARCHAR(160) NOT NULL,        -- simulated object key
    original_filename VARCHAR(255) NOT NULL,
    content_type      VARCHAR(100) NOT NULL
                      CHECK (content_type IN ('image/png', 'image/jpeg', 'image/webp', 'application/pdf')),
    size_bytes        INTEGER NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),
    verified_status   VARCHAR(16) NOT NULL DEFAULT 'pending'
                      CHECK (verified_status IN ('pending', 'verified', 'rejected')),
    verified_by       VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    verified_at       TIMESTAMPTZ,
    notes             TEXT,
    uploaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT document_proofs_verification_stamp CHECK (
        verified_status = 'pending'
        OR (verified_by IS NOT NULL AND verified_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_document_proofs_app
    ON document_proofs (application_id, uploaded_at);
CREATE INDEX IF NOT EXISTS idx_document_proofs_status
    ON document_proofs (verified_status);

COMMENT ON TABLE document_proofs IS
    'Metadata for borrower-uploaded proofs. Bytes live in object storage (here: '
    'the local `data/uploads` directory behind a storage adapter); the database '
    'stores only the opaque key and the verification decision.';

-- ---------------------------------------------------------------------------
-- 5. credit_score_results - the model decision (append-only audit history)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS credit_score_results (
    id                      VARCHAR(36) PRIMARY KEY,
    application_id          VARCHAR(36) NOT NULL
                            REFERENCES applications (id) ON DELETE CASCADE,

    credit_score            SMALLINT NOT NULL CHECK (credit_score BETWEEN 300 AND 900),
    risk_tier               VARCHAR(8) NOT NULL CHECK (risk_tier IN ('LOW', 'MEDIUM', 'HIGH')),
    recommended_limit       NUMERIC(16,2) NOT NULL CHECK (recommended_limit >= 0),
    shap_explanations_json  JSONB NOT NULL,      -- full explainability payload
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Decision context (why the number is what it is).
    probability_of_default  NUMERIC(7,6) CHECK (probability_of_default BETWEEN 0 AND 1),
    decision                VARCHAR(32) NOT NULL
                            DEFAULT 'MANUAL_REVIEW'
                            CHECK (decision IN ('PRE_APPROVED', 'MANUAL_REVIEW',
                                                'DECLINE_OR_SECURED_ONLY')),
    score_band              JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_quality            JSONB NOT NULL DEFAULT '{}'::jsonb,
    policy_breakdown        JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_version           VARCHAR(80) NOT NULL,
    decided_by              VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    officer_notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_credit_score_results_app
    ON credit_score_results (application_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_score_results_tier
    ON credit_score_results (risk_tier, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_credit_score_results_shap
    ON credit_score_results USING GIN (shap_explanations_json jsonb_path_ops);

COMMENT ON TABLE credit_score_results IS
    'Append-only scoring history. A re-score inserts a new row; the dashboard '
    'shows the latest. SHAP reasons are stored as JSONB so adverse-action codes '
    'can be queried across the book.';

-- ---------------------------------------------------------------------------
-- 6. underwriting_audit_log - who did what, with the evidence
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS underwriting_audit_log (
    id             VARCHAR(36) PRIMARY KEY,
    application_id VARCHAR(36) REFERENCES applications (id) ON DELETE CASCADE,
    actor_id       VARCHAR(36) REFERENCES users (id) ON DELETE SET NULL,
    actor_role     VARCHAR(16),
    action         VARCHAR(48) NOT NULL,
    details        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_application
    ON underwriting_audit_log (application_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON underwriting_audit_log (action, created_at DESC);

COMMENT ON TABLE underwriting_audit_log IS
    'Immutable trail of submissions, verifications, scores and rejections. '
    'Required for model governance and for defending a declined application.';

-- ---------------------------------------------------------------------------
-- 7. updated_at trigger
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_applications_updated_at ON applications;
CREATE TRIGGER trg_applications_updated_at
    BEFORE UPDATE ON applications FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_asset_declarations_updated_at ON asset_declarations;
CREATE TRIGGER trg_asset_declarations_updated_at
    BEFORE UPDATE ON asset_declarations FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- 8. Views for the banker dashboard and portfolio monitoring
-- ---------------------------------------------------------------------------

-- Pending/verified queue with evidence counts, one row per application.
CREATE OR REPLACE VIEW v_underwriting_queue AS
SELECT
    a.id,
    a.status,
    a.applicant_name,
    a.applicant_category,
    a.region_tier,
    a.requested_loan_amount,
    a.submitted_at,
    a.verified_at,
    COUNT(DISTINCT d.id)                                        AS document_count,
    COUNT(DISTINCT d.id) FILTER (WHERE d.verified_status = 'verified') AS verified_document_count,
    jsonb_array_length(ad.verified_fields)                      AS verified_field_count,
    cs.credit_score,
    cs.risk_tier,
    cs.recommended_limit
FROM applications a
LEFT JOIN asset_declarations ad ON ad.application_id = a.id
LEFT JOIN document_proofs d    ON d.application_id = a.id
LEFT JOIN LATERAL (
    SELECT * FROM credit_score_results r
    WHERE r.application_id = a.id
    ORDER BY r.generated_at DESC
    LIMIT 1
) cs ON TRUE
GROUP BY a.id, ad.verified_fields, cs.credit_score, cs.risk_tier, cs.recommended_limit;

COMMENT ON VIEW v_underwriting_queue IS
    'Banker work queue: application status plus how much evidence has actually '
    'been verified, and the latest score when one exists.';

-- Monthly funnel and portfolio risk mix.
CREATE OR REPLACE VIEW v_scoring_funnel AS
SELECT
    date_trunc('month', r.generated_at)       AS scoring_month,
    r.risk_tier,
    COUNT(*)                                  AS files_scored,
    ROUND(AVG(r.credit_score), 1)             AS avg_score,
    ROUND(AVG(r.probability_of_default), 4)   AS avg_pd,
    SUM(r.recommended_limit)                  AS total_limit_recommended,
    ROUND(AVG(r.recommended_limit), 2)        AS avg_limit
FROM credit_score_results r
GROUP BY 1, 2;

-- Reason-code frequency across scored files (model governance).
CREATE OR REPLACE VIEW v_reason_code_frequency AS
SELECT
    factor ->> 'label'                                   AS reason_code,
    factor ->> 'direction'                               AS direction,
    COUNT(*)                                             AS appearances,
    ROUND(AVG((factor ->> 'score_impact_points')::numeric), 1) AS avg_score_points
FROM credit_score_results r,
     LATERAL jsonb_array_elements(
         COALESCE(r.shap_explanations_json -> 'top_factors', '[]'::jsonb)
     ) AS factor
GROUP BY 1, 2;

COMMIT;

-- ===========================================================================
--  Production hardening notes (not applied in the demo)
-- ===========================================================================
--  * Row-level security: bankers see their branch book, borrowers only their own
--    files. The API enforces ownership today; RLS makes it defence in depth.
--  * Encrypt PII columns (pgcrypto) and keep KYC documents in a private bucket
--    with short-lived signed URLs instead of the local storage adapter.
--  * Append-only grants: REVOKE UPDATE, DELETE ON credit_score_results,
--    underwriting_audit_log FROM agritrust_app;
--  * Partition credit_score_results by generated_at month at scale.
-- ===========================================================================
