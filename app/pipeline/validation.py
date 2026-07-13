"""驗證框架：Leave-One-Ship-Out + 時間分塊（research.md 文獻 3-① 的警告）。

隨機交叉驗證在時序自相關資料上會洩漏，一律禁用。
合成資料階段以 ground truth（真實 s）評估 speed loss 還原精度；
比賽當天無 ground truth，改看：乾淨期外推 R²、清洗事件前後的 speed loss 落差。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app import schema
from app.pipeline.baseline import CleanBaselineModel, smooth_speed_loss


def _speed_loss_metrics(scored: pd.DataFrame, truth: pd.DataFrame) -> dict:
    """主指標對「基準相對真值」評估。

    基準期殘留髒污 s̄_b 使估計值整段平移為 (s − s̄_b)——這是 ISO 19030 基準法的
    固有語意（Speed Loss 相對參考期，非相對理想乾淨船體），故 mae_pp 以
    相對真值計；絕對真值誤差保留為 mae_abs_pp 供參考。
    """
    m = smooth_speed_loss(scored).merge(truth, on=[schema.SHIP_ID, schema.REPORT_DATE])
    m = m.dropna(subset=["speed_loss_smooth"])
    # 每船基準期的平均真實髒污 = 該船 Speed Loss 的零點
    offsets = (m[m["baseline_flag"]].groupby(schema.SHIP_ID)["true_speed_loss"].mean()
               .rename("s_offset"))
    m = m.merge(offsets, left_on=schema.SHIP_ID, right_index=True, how="left")
    m["s_offset"] = m["s_offset"].fillna(0.0)
    true_rel = (m["true_speed_loss"] - m["s_offset"]) * 100
    err_rel = m["speed_loss_smooth"] - true_rel
    err_abs = m["speed_loss_smooth"] - m["true_speed_loss"] * 100
    corr = float(np.corrcoef(m["speed_loss_smooth"], true_rel)[0, 1])
    return {"mae_pp": float(err_rel.abs().mean()), "mae_abs_pp": float(err_abs.abs().mean()),
            "bias_pp": float(err_rel.mean()), "corr": corr, "n": len(m)}


def leave_one_ship_out(feat: pd.DataFrame, truth: pd.DataFrame,
                       params: dict | None = None) -> pd.DataFrame:
    """每次抽掉一艘船訓練、在該船整段時序上評估 speed loss 還原。

    Returns:
        per-ship metrics（mae_pp / bias_pp / corr / n）。
    """
    rows = []
    for ship_id in feat[schema.SHIP_ID].unique():
        train = feat[(feat[schema.SHIP_ID] != ship_id)]
        test = feat[feat[schema.SHIP_ID] == ship_id]
        model = CleanBaselineModel(params).fit(train)
        scored = model.score_rows(test)
        rows.append({"ship_id": ship_id, **_speed_loss_metrics(scored, truth)})
    return pd.DataFrame(rows)


def time_blocked(feat: pd.DataFrame, truth: pd.DataFrame, cutoff: str,
                 params: dict | None = None) -> dict:
    """時間分塊：cutoff 之前訓練基準，之後整段評估（模擬部署後的未來資料）。"""
    cutoff_ts = pd.Timestamp(cutoff)
    train = feat[feat[schema.REPORT_DATE] < cutoff_ts]
    test = feat[feat[schema.REPORT_DATE] >= cutoff_ts]
    model = CleanBaselineModel(params).fit(train)
    scored = model.score_rows(test)
    return _speed_loss_metrics(scored, truth)
