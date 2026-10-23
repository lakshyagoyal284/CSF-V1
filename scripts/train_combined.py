#!/usr/bin/env python
"""Train the optional mixed-source model from the external datasets.

This is **experimental**. The four source datasets do not share a repayment
label: the bank file records an approval decision, the mortgage file records how
many loans a person has repaid, and the rating file is issuer-level. Training on
that mixture produces a model that is very good at identifying which dataset a
row came from, not at predicting whether a farmer will repay.

The served default remains ``artifacts/credit_model.pkl`` from
``python train_model.py``. Use this script only for research, and read
``docs/MODEL_CARD.md`` before drawing conclusions from its metrics.

    python scripts/train_combined.py --bank-rows 60000 --mortgage-rows 60000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.integrate_external_datasets import build_unified_portfolio  # noqa: E402
from ml.train import save_artifacts, train_model  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Train the experimental mixed-source credit model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--bank-rows", type=int, default=60_000)
    parser.add_argument("--mortgage-rows", type=int, default=60_000)
    parser.add_argument("--credit-rows", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--early-stopping-patience", type=int, default=120)
    parser.add_argument("--no-synthetic", action="store_true")
    parser.add_argument("--model-out", default="artifacts/credit_model_combined.pkl")
    parser.add_argument(
        "--metrics-out", default="artifacts/training_metrics_combined.json"
    )
    parser.add_argument(
        "--importance-out", default="artifacts/feature_importance_combined.csv"
    )
    parser.add_argument(
        "--shap-out", default="artifacts/shap_global_importance_combined.csv"
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    portfolio = build_unified_portfolio(
        use_bank=True,
        use_mortgage=True,
        use_credit_ratings=True,
        use_synthetic=not args.no_synthetic,
        bank_sample_rows=args.bank_rows,
        mortgage_sample_rows=args.mortgage_rows,
        credit_sample_rows=args.credit_rows,
    )

    target_rate = float(portfolio["repaid_on_time"].mean())
    sources = (
        {str(k): int(v) for k, v in portfolio["source"].value_counts().items()}
        if "source" in portfolio.columns
        else {}
    )
    print(f"Rows: {len(portfolio):,}  |  target rate: {target_rate:.1%}")
    print("Sources:", json.dumps(sources, indent=2))
    print(
        "WARNING: these labels are proxies (approval decision / history depth /\n"
        "issuer rating), not applicant repayment outcomes. See docs/MODEL_CARD.md."
    )

    result = train_model(
        portfolio,
        seed=args.seed,
        early_stopping_patience=args.early_stopping_patience,
    )
    artifacts = save_artifacts(
        result,
        portfolio=portfolio,
        data_report={
            "source": "combined_external_and_synthetic",
            "rows": len(portfolio),
            "target_rate": target_rate,
            "source_counts": sources,
            "bank_sample_rows": args.bank_rows,
            "mortgage_sample_rows": args.mortgage_rows,
            "credit_sample_rows": args.credit_rows,
            "label_caveat": (
                "Mixed proxy labels: bank approval, mortgage history depth and "
                "issuer rating migration. Not a repayment model."
            ),
        },
        model_path=args.model_out,
        metrics_path=args.metrics_out,
        importance_path=args.importance_out,
        shap_importance_path=args.shap_out,
    )

    if not args.quiet:
        print(f"Model: {artifacts['model_path']} ({artifacts['model_size_mb']} MB)")
        print(f"Hold-out ROC-AUC: {result.test_auc:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
