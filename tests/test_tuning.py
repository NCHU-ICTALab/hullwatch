"""Optuna 接線煙霧測試（3 trials，只驗證管線接得起來）。"""

import pytest

from app import schema
from app.pipeline.events import align_events
from app.pipeline.features import build_features, clean_reference_stats
from app.pipeline.tuning import tune
from app.synth.generator import GeneratorConfig, generate


@pytest.mark.slow
def test_tune_smoke():
    data = generate(GeneratorConfig(n_ships=3, start="2021-01-01", end="2022-12-31", seed=7))
    noon = schema.apply_quality_filter(schema.normalize_noon_reports(data["noon_reports"]), 4, 22)
    aligned = align_events(noon, data["events"], baseline_window_days=45)
    feat = build_features(aligned, clean_reference_stats(aligned))
    out = tune(feat, n_trials=3)
    assert "best_params" in out and out["best_value"] > 0
