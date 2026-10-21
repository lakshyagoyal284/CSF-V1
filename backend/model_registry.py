"""Model loading and lifecycle.

The inference artifact is a single pickled bundle (see :func:`ml.train.save_artifacts`)
containing the fitted XGBoost model, the frozen feature contract, the training
metadata and a reference sample. This module loads it once, caches it, and
builds the SHAP explainer alongside it - the explainer is expensive to
construct per request and cheap to keep in memory.

Failure is explicit: a missing or stale artifact raises
:class:`ModelNotAvailable`, which the API surfaces as HTTP 503 with an
instruction to run ``python train_model.py``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

from ml.config import FEATURE_COLUMNS
from ml.explain import CreditExplainer
from ml.train import ARTIFACT_SCHEMA_VERSION

logger = logging.getLogger(__name__)


class ModelNotAvailable(RuntimeError):
    """Raised when the scoring model cannot be loaded."""


@dataclass
class LoadedModel:
    """A fitted model plus everything needed to score and explain a file."""

    model: Any
    explainer: CreditExplainer
    feature_columns: list[str]
    metadata: dict[str, Any]
    reference_sample: pd.DataFrame | None
    path: Path

    @property
    def version(self) -> str:
        """Human-readable model version for the audit trail."""
        trained_at = str(self.metadata.get("trained_at", "unknown"))
        engine = str(self.metadata.get("engine_version", "0"))
        return f"acs-{engine}-{trained_at[:10]}"

    @property
    def metrics(self) -> dict[str, Any]:
        return self.metadata.get("metrics", {})


class ModelRegistry:
    """Thread-safe, lazily-loaded holder for the scoring model."""

    def __init__(self, model_path: str | Path) -> None:
        self.model_path = Path(model_path)
        self._loaded: LoadedModel | None = None
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._loaded is not None

    def get(self) -> LoadedModel:
        """Return the loaded model, loading it on first use."""
        if self._loaded is None:
            with self._lock:
                # Double-checked locking: only one thread loads the artifact.
                if self._loaded is None:
                    self._loaded = self._load()
        return self._loaded

    def reload(self) -> LoadedModel:
        """Force a reload (hot-swapping a freshly trained model)."""
        with self._lock:
            self._loaded = self._load()
        return self._loaded

    # -- internals ----------------------------------------------------------

    def _load(self) -> LoadedModel:
        if not self.model_path.exists():
            raise ModelNotAvailable(
                f"Model artifact not found at '{self.model_path}'. "
                "Train it first with: python train_model.py"
            )

        try:
            bundle = joblib.load(self.model_path)
        except Exception as exc:  # pragma: no cover - corrupt artifact path
            raise ModelNotAvailable(f"Could not deserialise '{self.model_path}': {exc}") from exc

        if not isinstance(bundle, dict) or "model" not in bundle:
            raise ModelNotAvailable(
                f"'{self.model_path}' is not a credit-scoring bundle. "
                "Re-run: python train_model.py"
            )

        schema_version = int(bundle.get("schema_version", 0))
        if schema_version != ARTIFACT_SCHEMA_VERSION:
            raise ModelNotAvailable(
                f"Artifact schema v{schema_version} does not match the expected "
                f"v{ARTIFACT_SCHEMA_VERSION}. Re-run: python train_model.py"
            )

        columns = list(bundle.get("feature_columns", FEATURE_COLUMNS))
        missing = [column for column in FEATURE_COLUMNS if column not in columns]
        if missing:
            # The contract is frozen: serving a model whose columns drifted from
            # the code would produce silently wrong scores.
            raise ModelNotAvailable(
                f"Model feature contract is missing {len(missing)} column(s): {missing[:5]}"
            )

        model = bundle["model"]
        logger.info(
            "Loaded credit model from %s (trained_at=%s, rows=%s)",
            self.model_path,
            bundle.get("trained_at"),
            bundle.get("metrics", {}).get("model", {}).get("training_rows"),
        )
        return LoadedModel(
            model=model,
            explainer=CreditExplainer(model, feature_columns=columns),
            feature_columns=columns,
            metadata={
                "trained_at": bundle.get("trained_at"),
                "engine_version": bundle.get("engine_version"),
                "metrics": bundle.get("metrics", {}),
                "data_report": bundle.get("data_report", {}),
            },
            reference_sample=bundle.get("reference_feature_sample"),
            path=self.model_path,
        )


# ---------------------------------------------------------------------------
# process-wide singleton
# ---------------------------------------------------------------------------

_registry: ModelRegistry | None = None
_registry_lock = threading.Lock()


def get_registry(model_path: str | Path | None = None) -> ModelRegistry:
    """Return the process-wide registry, creating it on first use."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                from backend.config import get_settings

                path = model_path if model_path is not None else get_settings().model_path
                _registry = ModelRegistry(path)
    elif model_path is not None and Path(model_path) != _registry.model_path:
        # A different path was requested (tests do this): build a fresh registry.
        return ModelRegistry(model_path)
    return _registry


def reset_registry() -> None:
    """Drop the cached registry (used by tests and on config reload)."""
    global _registry
    with _registry_lock:
        _registry = None
