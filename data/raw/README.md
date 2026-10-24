# Raw datasets

The lender datasets are **not committed** (they are large and licence-bound).
Place them here to run the experimental integration:

| File | Rows | Used for |
|---|---:|---|
| `bank_dataset_2.csv` | 50,000 | `loan_status` (approval outcome) |
| `mortgage_loan_dataset.csv` | 49,990 | `Loans Repaid` (history depth) |
| `credit_ratings.csv` | 17,939 | issuer rating migration |
| `application_test.csv` | 48,744 | score/demo data only — **no label is invented** |

Then:

```bash
python scripts/train_combined.py
```

Read `docs/MODEL_CARD.md` first: these labels are proxies, not repayment
outcomes, and the resulting model is experimental. The served default remains
`python train_model.py`.
