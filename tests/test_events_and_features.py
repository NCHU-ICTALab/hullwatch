"""事件對齊與相對化特徵的測試。"""

import pandas as pd
import pytest

from app import schema
from app.pipeline.events import align_events
from app.pipeline.features import build_features, clean_reference_stats
from app.synth.generator import GeneratorConfig, generate


@pytest.fixture(scope="module")
def aligned():
    data = generate(GeneratorConfig(n_ships=3, start="2021-01-01", end="2022-12-31", seed=7))
    noon = schema.normalize_noon_reports(data["noon_reports"])
    noon = schema.apply_quality_filter(noon, 4, 22)
    return align_events(noon, data["events"], baseline_window_days=45), data


def test_days_since_clean_resets_after_cleaning(aligned):
    df, data = aligned
    cleans = data["events"][data["events"]["event_type"] == "cleaning"]
    ev = cleans.iloc[0]
    ship = df[df[schema.SHIP_ID] == ev["ship_id"]]
    after = ship[ship[schema.REPORT_DATE] >= ev["event_date"]].head(10)
    assert (after["days_since_clean"] <= 40).all()


def test_days_since_clean_monotonic_between_events(aligned):
    df, _ = aligned
    ship = df[df[schema.SHIP_ID] == df[schema.SHIP_ID].iloc[0]]
    diffs = ship["days_since_clean"].diff().dropna()
    # 只能是增加（間隔天數）或大幅下降（重置）
    assert ((diffs > 0) | (diffs < -30)).all()


def test_pre_first_reset_is_not_baseline(aligned):
    df, data = aligned
    for ship_id, grp in df.groupby(schema.SHIP_ID):
        resets = data["events"][
            (data["events"]["ship_id"] == ship_id)
            & (data["events"]["event_type"].isin(schema.RESET_EVENTS))
        ]["event_date"]
        if len(resets) == 0:
            continue
        before_first = grp[grp[schema.REPORT_DATE] < resets.min()]
        assert not before_first["baseline_flag"].any()


def test_reference_stats_and_relative_features(aligned):
    df, _ = aligned
    refs = clean_reference_stats(df)
    assert (refs["n_baseline_rows"] >= 10).all(), "基準窗口樣本數不足"
    feat = build_features(df, refs)
    # 基準期的相對值應以 1 為中心
    base = feat[feat["baseline_flag"]]
    assert base["v_rel"].median() == pytest.approx(1.0, abs=0.05)
    assert base["f_rel"].median() == pytest.approx(1.0, abs=0.05)
    # 髒污晚期（距清洗久）f_rel 在同 v_rel 下應高於基準期 → 粗檢：整體相關
    late = feat[feat["days_since_clean"] > 150]
    if len(late) > 30:
        mid_v = feat["v_rel"].between(0.97, 1.03)
        assert feat[mid_v & (feat["days_since_clean"] > 150)]["f_rel"].mean() > \
               feat[mid_v & feat["baseline_flag"]]["f_rel"].mean()
