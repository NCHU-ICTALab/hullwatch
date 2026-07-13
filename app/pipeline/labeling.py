"""肘點法髒污分級 — 學長論文的分級方法原樣平移。

對排序後的指標曲線做正規化，取「首尾連線的最大垂距點」為肘點，
以資料自身的形狀決定低/中/高邊界，取代人工經驗門檻。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LEVELS = ["low", "medium", "high"]


MIN_KNEE_STRENGTH = 0.15  # 正規化垂距低於此值視為「無膝點」


def elbow_cut(values: np.ndarray) -> tuple[float, float]:
    """回傳（肘點門檻值, 膝點強度）。

    做法（論文原法）：值排序 → x/y 正規化到 [0,1] → 計算各點到首尾連線的
    垂直距離 → 距離最大處即肘點。膝點強度 = 最大垂距（0~1）；分佈近乎均勻
    時曲線貼著弦線、強度趨近 0，代表資料本身沒有自然分界。
    """
    v = np.sort(np.asarray(values, dtype=float))
    if len(v) < 5 or np.isclose(v[-1], v[0]):
        return float(v[-1] if len(v) else 0.0), 0.0
    x = np.linspace(0.0, 1.0, len(v))
    y = (v - v[0]) / (v[-1] - v[0])
    # 首尾連線即 y = x，垂距 ∝ |y − x|
    dist = np.abs(y - x)
    k = int(np.argmax(dist))
    return float(v[k]), float(dist[k])


def fouling_levels(residuals: np.ndarray, min_upper: int = 20) -> tuple[float, float]:
    """兩段肘點求 (low/medium, medium/high) 門檻，弱膝點時退回分位數。

    負殘差是雜訊（船不會比乾淨更乾淨），先裁到 0。第一刀在全體找肘點、
    第二刀在其上子集重複；任一刀膝點強度不足（分佈平滑無自然分界）則
    改用正值分位數三分——資料有膝點就讓資料說話，沒有就誠實三分。
    """
    r = np.asarray(residuals, dtype=float)
    r = np.clip(r[np.isfinite(r)], 0.0, None)
    if len(r) == 0:
        return 0.0, 0.0
    cut1, strength1 = elbow_cut(r)
    if strength1 < MIN_KNEE_STRENGTH:
        return float(np.quantile(r, 1 / 3)), float(np.quantile(r, 2 / 3))
    upper = r[r > cut1]
    if len(upper) >= min_upper:
        cut2, strength2 = elbow_cut(upper)
        if strength2 < MIN_KNEE_STRENGTH or cut2 <= cut1:
            cut2 = float(np.quantile(upper, 0.5))
    else:
        cut2 = float(np.quantile(r, 0.95))
    return cut1, max(cut2, cut1)


def label(values: pd.Series, cuts: tuple[float, float]) -> pd.Series:
    """依門檻把數值貼上 low/medium/high 標籤。"""
    cut1, cut2 = cuts
    return pd.Series(
        np.select([values <= cut1, values <= cut2], ["low", "medium"], default="high"),
        index=values.index,
    )
