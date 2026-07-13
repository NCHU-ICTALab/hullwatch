"""乾淨基準模型的核心驗收：能否從合成正午報表還原 ground truth speed loss。"""

import numpy as np
import pandas as pd
import pytest

from app import schema
from app.pipeline.baseline import CleanBaselineModel, smooth_speed_loss
from app.pipeline.events import align_events
from app.pipeline.features import build_features, clean_reference_stats
from app.pipeline.validation import leave_one_ship_out, time_blocked
from app.synth.generator import GeneratorConfig, generate


@pytest.fixture(scope="module")
def pipeline_data():
    data = generate(GeneratorConfig(n_ships=6, start="2021-01-01", end="2024-12-31", seed=11))
    noon = schema.normalize_noon_reports(data["noon_reports"])
    noon = schema.apply_quality_filter(noon, 4, 22)
    aligned = align_events(noon, data["events"], baseline_window_days=45)
    refs = clean_reference_stats(aligned)
    feat = build_features(aligned, refs)
    return feat, data["truth"]


@pytest.fixture(scope="module")
def fitted(pipeline_data):
    feat, truth = pipeline_data
    return CleanBaselineModel().fit(feat), feat, truth


def test_monotone_curve(fitted):
    """油耗必須隨航速單調遞增——反演的前提。"""
    model, feat, _ = fitted
    probe = feat.head(1)[["v_rel", "wind", "draft_rel"]].copy()
    grid = pd.concat([probe] * 50, ignore_index=True)
    grid["v_rel"] = np.linspace(0.6, 1.5, 50)
    pred = model.model.predict(grid)
    assert (np.diff(pred) >= -1e-9).all()


def test_inversion_roundtrip(fitted):
    """把模型自己的預測值丟回反演，應還原原本的航速（自洽性）。"""
    model, feat, _ = fitted
    sample = feat[feat["baseline_flag"]].head(200).copy()
    sample["f_rel"] = model.predict_f_rel(sample)  # 完美落在曲線上的油耗
    v_rel_recovered = model.invert_v_rel(sample)
    err = np.abs(v_rel_recovered - feat[feat["baseline_flag"]].head(200)["v_rel"].to_numpy())
    assert np.median(err) < 0.01


def test_speed_loss_recovers_ground_truth(fitted):
    """走路骨架的核心驗收：平滑後 speed loss 對真值 MAE < 2pp、相關 > 0.8。"""
    model, feat, truth = fitted
    scored = smooth_speed_loss(model.score_rows(feat))
    m = scored.merge(truth, on=[schema.SHIP_ID, schema.REPORT_DATE]).dropna(
        subset=["speed_loss_smooth"])
    err = m["speed_loss_smooth"] - m["true_speed_loss"] * 100
    corr = np.corrcoef(m["speed_loss_smooth"], m["true_speed_loss"] * 100)[0, 1]
    assert err.abs().mean() < 2.0, f"MAE={err.abs().mean():.2f}pp"
    assert corr > 0.8, f"corr={corr:.3f}"


def test_excess_foc_positive_when_fouled(fitted):
    model, feat, truth = fitted
    scored = model.score_rows(feat).merge(truth, on=[schema.SHIP_ID, schema.REPORT_DATE])
    dirty = scored[scored["true_speed_loss"] > 0.05]
    clean = scored[scored["true_speed_loss"] < 0.015]
    assert dirty["excess_foc"].mean() > clean["excess_foc"].mean() + 0.5


def test_save_load_roundtrip(fitted, tmp_path):
    model, feat, _ = fitted
    p = tmp_path / "m.json"
    model.save(p)
    loaded = CleanBaselineModel.load(p)
    a = model.predict_f_rel(feat.head(50))
    b = loaded.predict_f_rel(feat.head(50))
    np.testing.assert_allclose(a, b, rtol=1e-5)


def test_shap_contributions_sum_to_prediction(fitted):
    model, feat, _ = fitted
    sample = feat.head(50)
    contrib = model.contributions(sample)
    total = contrib.sum(axis=1).to_numpy()
    pred = model.predict_f_rel(sample)
    assert abs(total - pred).max() < 1e-3


@pytest.mark.slow
def test_leave_one_ship_out(pipeline_data):
    """LOSO：模型須泛化到未見過的船（相對化特徵的存在理由）。"""
    feat, truth = pipeline_data
    metrics = leave_one_ship_out(feat, truth)
    # mae_pp 對「基準相對真值」計（ISO 19030 語意，見 validation._speed_loss_metrics）
    assert (metrics["mae_pp"] < 1.5).all(), metrics.to_string()
    assert (metrics["corr"] > 0.7).all(), metrics.to_string()


@pytest.mark.slow
def test_time_blocked(pipeline_data):
    feat, truth = pipeline_data
    m = time_blocked(feat, truth, cutoff="2024-01-01")
    assert m["mae_pp"] < 2.5, m
    assert m["corr"] > 0.7, m
