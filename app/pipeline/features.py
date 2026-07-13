"""特徵工程 — 學長論文「Robust Scaling 相對化」的船舶版。

核心手法：以每艘船自己的乾淨基準統計把觀測相對化（v_rel, f_rel, draft_rel），
使全隊共用一個模型且可對未見過的船泛化（Leave-One-Ship-Out 的前提）。
新船加入不需重訓，只需算出它的乾淨基準統計。
"""

from __future__ import annotations

import pandas as pd

from app import schema

MODEL_FEATURES = ["v_rel", "wind", "draft_rel"]
MONOTONE_CONSTRAINTS = (1, 0, 0)  # 油耗隨航速單調遞增（ADR-0001 反演前提）
TARGET = "f_rel"


def clean_reference_stats(aligned_filtered: pd.DataFrame) -> pd.DataFrame:
    """計算每艘船的乾淨基準統計（V/F/draft 參考值）。

    Args:
        aligned_filtered: 已對齊事件、已套品質篩選的 canonical 正午報表。

    Returns:
        index=ship_id 的 DataFrame，欄位 v_ref / f_ref / draft_ref / n_baseline_rows。
    """
    base = aligned_filtered[aligned_filtered["baseline_flag"]]
    stats = base.groupby(schema.SHIP_ID).agg(
        v_ref=(schema.AVG_SPEED, "median"),
        f_ref=(schema.DAILY_FOC, "median"),
        draft_ref=(schema.MEAN_DRAFT, "median"),
        n_baseline_rows=(schema.DAILY_FOC, "size"),
    )
    return stats


def build_features(aligned_filtered: pd.DataFrame, refs: pd.DataFrame) -> pd.DataFrame:
    """把觀測相對化為模型特徵。缺 draft 欄時 draft_rel 固定為 1。

    Returns:
        原表加上 v_rel / f_rel / draft_rel / wind / foc_per_v3。
        沒有基準統計的船（不在 refs）會被剔除。
    """
    df = aligned_filtered.merge(refs, left_on=schema.SHIP_ID, right_index=True, how="inner").copy()
    df["v_rel"] = df[schema.AVG_SPEED] / df["v_ref"]
    df["f_rel"] = df[schema.DAILY_FOC] / df["f_ref"]
    if schema.MEAN_DRAFT in df.columns and df["draft_ref"].notna().all():
        df["draft_rel"] = df[schema.MEAN_DRAFT] / df["draft_ref"]
    else:
        df["draft_rel"] = 1.0
    df["wind"] = df[schema.WIND_SCALE].astype(float)
    df["foc_per_v3"] = df[schema.DAILY_FOC] / df[schema.AVG_SPEED] ** 3
    return df


def add_rolling_stats(df: pd.DataFrame, col: str, windows: tuple[int, ...] = (7, 14, 30)) -> pd.DataFrame:
    """對指定欄位加上每船 7/14/30 天窗口統計（mean/std/slope）——論文時域特徵表的搬運。"""
    df = df.sort_values([schema.SHIP_ID, schema.REPORT_DATE]).copy()
    for w in windows:
        g = df.groupby(schema.SHIP_ID)[col]
        df[f"{col}_mean_{w}d"] = g.transform(lambda s: s.rolling(w, min_periods=3).mean())
        df[f"{col}_std_{w}d"] = g.transform(lambda s: s.rolling(w, min_periods=3).std())
        df[f"{col}_slope_{w}d"] = g.transform(
            lambda s: s.rolling(w, min_periods=3).mean().diff(w // 2) / (w / 2)
        )
    return df
