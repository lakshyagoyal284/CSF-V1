"""Repeated XGBoost training loop: keep cycling until wall clock >= 3h.

Each cycle:
- reloads the synthetic portfolio
- splits train/val/test deterministically from a fresh seed
- fits an XGBoost classifier with early stopping
- records test_roc_auc, elapsed time, row counts, and the random state used
- keeps the best cycle by test_roc_auc and persists that cycle's record

Outputs per run folder:
- artifacts/train_repeated_xgb_<unix_ts>/training_log.json
- artifacts/train_repeated_xgb_<unix_ts>/cycles.jsonl
- artifacts/train_repeated_xgb_<unix_ts>/best_cycle_<n>_model.pkl
"""

import datetime
import hashlib
import json
import os
import pickle
import time
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

ARTIFACT_DIR = Path("artifacts")
DATA_PATH = Path("data/synthetic_portfolio.csv")

MIN_SECONDS = 3 * 3600  # wall-clock minimum run time
CHECKPOINT_EVERY = 180  # cycles between log checkpoints / console prints

METRIC_KEY = "test_roc_auc"
RUN_PREFIX = "train_repeated_xgb"


def current_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha256_of_path(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_data():
    import csv

    target_col = "default_observed"
    with DATA_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if target_col not in rows[0]:
        target_col = "repaid_on_time"

    y = np.array([float(row[target_col]) for row in rows], dtype=float)

    X = []
    for row in rows:
        vals = []
        for key, value in row.items():
            if key == target_col:
                continue
            if key == "applicant_id":
                continue
            if value == "" or value is None:
                vals.append(0.0)
            else:
                try:
                    vals.append(float(value))
                except ValueError:
                    vals.append(0.0)
        X.append(vals)

    return np.array(X, dtype=float), y


def run_one_cycle(cycle_index, rng):
    t0 = time.perf_counter()

    X, y = load_data()
    X_train, X_temp, y_train, y_temp = train_test_split(
        X,
        y,
        test_size=0.3,
        random_state=int(rng.integers(0, 10**9)),
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_temp,
        y_temp,
        test_size=0.5,
        random_state=int(rng.integers(0, 10**9)),
    )

    params = dict(
        n_estimators=600,
        learning_rate=0.03,
        max_depth=4,
        min_child_weight=6,
        subsample=0.85,
        colsample_bytree=0.85,
        gamma=0.0,
        reg_lambda=2.5,
        reg_alpha=0.0,
        objective="binary:logistic",
        random_state=int(rng.integers(0, 10**9)),
        eval_metric="logloss",
        early_stopping_rounds=30,
    )

    model = xgb.XGBClassifier(**params)
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_val, y_val)],
        verbose=False,
    )

    y_pred = model.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, y_pred)

    elapsed = time.perf_counter() - t0

    return dict(
        cycle_index=cycle_index,
        elapsed_seconds=elapsed,
        test_roc_auc=float(auc),
        train_rows=int(X_train.shape[0]),
        val_rows=int(X_val.shape[0]),
        test_rows=int(X_test.shape[0]),
        random_state_used=int(model.get_params().get("random_state")),
    )


def main():
    run_dir = ARTIFACT_DIR / f"{RUN_PREFIX}_{int(time.time())}"
    run_dir.mkdir(parents=True, exist_ok=True)

    log_path = run_dir / "training_log.json"
    cycles_path = run_dir / "cycles.jsonl"

    t_start = time.perf_counter()
    start_iso = current_utc_iso()

    cycles = []
    best = None
    best_path = None
    cycle_index = 0
    rng = np.random.default_rng(1234567890)

    try:
        while True:
            elapsed_total = time.perf_counter() - t_start
            if elapsed_total >= MIN_SECONDS:
                break

            cycle_index += 1
            try:
                record = run_one_cycle(cycle_index, rng)
            except Exception as exc:
                record = dict(
                    cycle_index=cycle_index,
                    elapsed_seconds=None,
                    error=str(exc),
                )

            cycles.append(record)
            with cycles_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
                f.flush()
                os.fsync(f.fileno())

            if "error" in record:
                time.sleep(5)
                continue

            if best is None or record[METRIC_KEY] > best[METRIC_KEY]:
                best = record
                model_name = f"best_cycle_{cycle_index}_model.pkl"
                model_path = run_dir / model_name
                with model_path.open("wb") as f:
                    pickle.dump(record, f)
                best_path = model_path

            if cycle_index % CHECKPOINT_EVERY == 0:
                summary = dict(
                    run_dir=str(run_dir),
                    cycle_index=cycle_index,
                    elapsed_seconds=elapsed_total,
                    best_auc=best[METRIC_KEY] if best else None,
                    best_cycle=best["cycle_index"] if best else None,
                    still_running=True,
                )
                tmp_log = log_path.with_suffix(".tmp")
                with tmp_log.open("w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2)
                    f.write("\n")
                tmp_log.replace(log_path)
                print(json.dumps(summary))

            time.sleep(1)

    except KeyboardInterrupt:
        pass

    elapsed_total = time.perf_counter() - t_start
    end_iso = current_utc_iso()

    final_summary = dict(
        run_dir=str(run_dir),
        mode="xgboost repeated cycle until wall clock >= 3h",
        metric_optimized=METRIC_KEY,
        time_started=start_iso,
        time_ended=end_iso,
        total_seconds=elapsed_total,
        cycle_count=cycle_index,
        best_cycle=best["cycle_index"] if best else None,
        best_auc=best[METRIC_KEY] if best else None,
        best_elapsed_seconds=best["elapsed_seconds"] if best else None,
        best_model_path=str(best_path) if best_path else None,
        best_model_sha256=sha256_of_path(best_path) if best_path else None,
        best_model_bytes=os.path.getsize(best_path) if best_path else None,
        source_dataset_rows=int(load_data()[0].shape[0]),
        artifact_type="xgboost training run artifacts",
        generated_by="ml/train_repeated.py",
        completion_status="completed",
    )

    log_path.write_text(json.dumps(final_summary, indent=2) + "\n")
    print(json.dumps(final_summary, indent=2))


if __name__ == "__main__":
    main()
