"""水下事件對齊 — 船舶版的「刀次重置」。

把水下報告事件對齊到正午報表時間軸，產出：
- ``days_since_clean``：距上次完全重置事件（清洗/塢修）天數
- ``days_since_polish``：距上次螺槳拋光天數
- ``baseline_flag``：是否落在乾淨基準窗口內
- ``baseline_id``：基準窗口編號（每次重置事件開一個新窗口）

事件來源當天可能是 PDF 報告；解析器只需產出符合 schema 的事件表（ship_id,
event_date, event_type, notes），本模組即可原樣運作。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app import schema


def align_events(noon: pd.DataFrame, events: pd.DataFrame, baseline_window_days: int) -> pd.DataFrame:
    """將事件表對齊到 canonical 正午報表。

    Args:
        noon: canonical 正午報表（``schema.normalize_noon_reports`` 的輸出）。
        events: 事件表（canonical 欄名）。
        baseline_window_days: 重置事件後視為乾淨基準的天數。

    Returns:
        noon 加上 days_since_clean / days_since_polish / baseline_flag / baseline_id。
        資料起點到第一次重置事件之間：days_since_clean 為自資料起點天數，
        baseline_flag 一律 False（無法保證乾淨）。
    """
    events = events.copy()
    events[schema.EVENT_DATE] = pd.to_datetime(events[schema.EVENT_DATE])
    out_parts: list[pd.DataFrame] = []

    for ship_id, grp in noon.groupby(schema.SHIP_ID, sort=False):
        grp = grp.sort_values(schema.REPORT_DATE).copy()
        ev = events[events[schema.EVENT_SHIP_ID] == ship_id]
        resets = ev[ev[schema.EVENT_TYPE].isin(schema.RESET_EVENTS)][schema.EVENT_DATE].sort_values()
        polishes = ev[ev[schema.EVENT_TYPE].isin(schema.PARTIAL_RESET_EVENTS)][schema.EVENT_DATE].sort_values()

        dates = grp[schema.REPORT_DATE]
        start = dates.iloc[0]

        # 距上次重置：searchsorted 找每個日期之前最近的 reset
        reset_arr = resets.to_numpy()
        idx = np.searchsorted(reset_arr, dates.to_numpy(), side="right") - 1
        last_reset = np.where(idx >= 0, reset_arr[np.clip(idx, 0, None)], np.datetime64("NaT"))
        dsc = (dates.to_numpy() - last_reset) / np.timedelta64(1, "D")
        no_reset_yet = idx < 0
        dsc_fallback = (dates.to_numpy() - np.datetime64(start)) / np.timedelta64(1, "D")
        grp["days_since_clean"] = np.where(no_reset_yet, dsc_fallback, dsc)
        grp["baseline_flag"] = (~no_reset_yet) & (grp["days_since_clean"] <= baseline_window_days)
        grp["baseline_id"] = np.where(no_reset_yet, -1, idx)

        polish_arr = polishes.to_numpy()
        pidx = np.searchsorted(polish_arr, dates.to_numpy(), side="right") - 1
        last_polish = np.where(pidx >= 0, polish_arr[np.clip(pidx, 0, None)], np.datetime64("NaT"))
        dsp = (dates.to_numpy() - last_polish) / np.timedelta64(1, "D")
        grp["days_since_polish"] = np.where(pidx < 0, np.nan, dsp)

        out_parts.append(grp)

    return pd.concat(out_parts, ignore_index=True)
