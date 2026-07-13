"""schema 正規化與合成產生器的物理正確性測試。"""

import numpy as np
import pandas as pd
import pytest

from app import schema
from app.synth.generator import GeneratorConfig, generate


@pytest.fixture(scope="module")
def small_dataset():
    return generate(GeneratorConfig(n_ships=3, start="2021-01-01", end="2022-12-31", seed=7))


def test_normalize_maps_raw_columns(small_dataset):
    df = schema.normalize_noon_reports(small_dataset["noon_reports"])
    for col in [schema.SHIP_ID, schema.REPORT_DATE, schema.DAILY_FOC, schema.AVG_SPEED]:
        assert col in df.columns
    # DailyFOC = consump / hours * 24（命題公式）
    row = df.iloc[0]
    expected = row[schema.ME_CONSUMP_VLSFO] / row[schema.HOURS_FULL_SPEED] * 24
    assert row[schema.DAILY_FOC] == pytest.approx(expected)


def test_normalize_rejects_missing_columns():
    with pytest.raises(ValueError, match="COLUMN_ALIASES"):
        schema.normalize_noon_reports(pd.DataFrame({"FOO": [1]}))


def test_normalize_passes_through_unknown_extra_columns(small_dataset):
    raw = small_dataset["noon_reports"].copy()
    raw["NEW_SENSOR"] = 1.0  # 比賽當天欄位增加的演練
    df = schema.normalize_noon_reports(raw)
    assert "NEW_SENSOR" in df.columns


def test_quality_filter(small_dataset):
    df = schema.normalize_noon_reports(small_dataset["noon_reports"])
    filtered = schema.apply_quality_filter(df, max_wind=4, min_hours=22)
    assert (filtered[schema.WIND_SCALE] <= 4).all()
    assert (filtered[schema.HOURS_FULL_SPEED] >= 22).all()
    assert 0 < len(filtered) < len(df)


def test_fouling_grows_and_resets_on_cleaning(small_dataset):
    """清洗事件前 s 應高於事件後——基準重置的物理核心。"""
    truth, events = small_dataset["truth"], small_dataset["events"]
    cleans = events[events["event_type"] == "cleaning"]
    assert len(cleans) > 0
    checked = 0
    for _, ev in cleans.iterrows():
        t = truth[truth["ship_id"] == ev["ship_id"]].set_index("report_date")["true_speed_loss"]
        before = t.loc[: ev["event_date"] - pd.Timedelta(days=1)].tail(5).mean()
        after = t.loc[ev["event_date"] + pd.Timedelta(days=2):].head(5).mean()
        if np.isnan(before) or np.isnan(after):
            continue
        assert after < before * 0.6, f"{ev['ship_id']} 清洗後 s 未明顯下降"
        checked += 1
    assert checked > 0


def test_fouled_ship_is_slower_at_same_fuel(small_dataset):
    """物理自洽：同船同油耗下，髒污期的航速應低於乾淨期。"""
    df = schema.normalize_noon_reports(small_dataset["noon_reports"])
    df = schema.apply_quality_filter(df, 4, 22)
    truth = small_dataset["truth"]
    m = df.merge(truth, on=["ship_id", "report_date"])
    ship = m[m["ship_id"] == m["ship_id"].iloc[0]]
    clean = ship[ship["true_speed_loss"] < 0.02]
    dirty = ship[ship["true_speed_loss"] > 0.06]
    if len(clean) > 10 and len(dirty) > 10:
        # 控制油耗區間比較航速
        lo, hi = dirty[schema.DAILY_FOC].quantile([0.3, 0.7])
        c = clean[clean[schema.DAILY_FOC].between(lo, hi)][schema.AVG_SPEED].mean()
        d = dirty[dirty[schema.DAILY_FOC].between(lo, hi)][schema.AVG_SPEED].mean()
        assert d < c


def test_reproducible():
    a = generate(GeneratorConfig(n_ships=1, end="2021-03-01", seed=1))
    b = generate(GeneratorConfig(n_ships=1, end="2021-03-01", seed=1))
    pd.testing.assert_frame_equal(a["noon_reports"], b["noon_reports"])
