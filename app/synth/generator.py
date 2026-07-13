"""合成正午報表產生器。

物理模型（每船每日）：
- 乾淨油耗曲線：F0(V) = k·V³（立方定律），k 由船的設計點 (design_speed, design_foc) 決定。
- 結垢狀態 s(t)：自上次重置事件起以船別結垢率累積（含海水溫度季節項與隨機游走），
  水下清潔/塢修近乎歸零，螺槳拋光部分削減。
- 觀測：船長設定指令航速 V_cmd 對應功率 → 油耗 F = F0(V_cmd)·(1+天候係數+雜訊)，
  實際達成航速 V_obs = V_cmd·(1−s)。故 ground truth speed loss ≈ s。

輸出三張表：noon_reports（含刻意的原始欄名，模擬真實資料）、underwater_events、truth（每日真實 s）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

SHIP_NAMES = [
    "YM Triumph", "YM Wellspring", "YM Warmth", "YM Together", "YM Wish",
    "YM Width", "YM Wind", "YM Trophy", "YM Unison", "YM Truth",
    "YM Tiptop", "YM Throne", "YM Tutorial", "YM Cultivation", "YM Continuity",
]


@dataclass
class GeneratorConfig:
    n_ships: int = 15
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    seed: int = 42
    # 結垢率範圍：每 30 天增加的 speed loss（小數）
    fouling_rate_30d: tuple[float, float] = (0.003, 0.016)
    clean_interval_days: tuple[int, int] = (150, 380)   # 清洗排程區間
    polish_every_n_cleans: int = 2                       # 每 N 次清洗夾一次螺槳拋光
    inspection_lead_days: int = 20                       # 清洗前幾天做檢查
    weather_noise_sd: float = 0.025                      # 油耗觀測雜訊
    design_speed_range: tuple[float, float] = (14.5, 17.5)
    design_foc_range: tuple[float, float] = (24.0, 34.0)
    port_day_prob: float = 0.18                          # 非全速日比例（會被篩掉）
    extra_columns: dict[str, object] = field(default_factory=dict)  # 當天新欄位演練用


def _seasonal_wind(rng: np.random.Generator, dates: pd.DatetimeIndex) -> np.ndarray:
    """冬季風大、夏季風小的 Beaufort 風級（0–9）。"""
    doy = dates.dayofyear.to_numpy()
    base = 3.2 + 1.4 * np.cos(2 * np.pi * (doy - 15) / 365.25)  # 冬高夏低
    wind = rng.normal(base, 1.5)
    return np.clip(np.round(wind), 0, 9).astype(int)


def generate(cfg: GeneratorConfig | None = None) -> dict[str, pd.DataFrame]:
    """產生合成資料集。

    Returns:
        dict：``noon_reports``（原始欄名）、``events``、``truth``。
    """
    cfg = cfg or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)
    dates = pd.date_range(cfg.start, cfg.end, freq="D")

    noon_rows: list[dict] = []
    event_rows: list[dict] = []
    truth_rows: list[dict] = []

    for i in range(cfg.n_ships):
        ship_id = f"YM-{9001 + i * 7}"
        name = SHIP_NAMES[i % len(SHIP_NAMES)]
        design_v = rng.uniform(*cfg.design_speed_range)
        design_f = rng.uniform(*cfg.design_foc_range)
        k = design_f / design_v**3
        rate_30d = rng.uniform(*cfg.fouling_rate_30d)
        draft_laden = rng.uniform(11.0, 13.5)

        wind = _seasonal_wind(rng, dates)
        s = float(rng.uniform(0, 0.02))  # 起始輕微髒污
        next_clean_in = int(rng.integers(*cfg.clean_interval_days))
        cleans_done = 0
        # 為讓「目前狀態」多樣化，最後一次清洗時間錯開：部分船在資料尾端逼近門檻
        for d_idx, date in enumerate(dates):
            # --- 事件排程 ---
            if next_clean_in == cfg.inspection_lead_days:
                lvl = "heavy" if s > 0.06 else ("moderate" if s > 0.03 else "light")
                event_rows.append({"ship_id": ship_id, "event_date": date,
                                   "event_type": "inspection",
                                   "notes": f"fouling {lvl}, coverage ~{min(95, int(s * 900))}%"})
            if next_clean_in <= 0:
                cleans_done += 1
                if cleans_done % cfg.polish_every_n_cleans == 0:
                    event_rows.append({"ship_id": ship_id, "event_date": date,
                                       "event_type": "propeller_polish", "notes": "routine polish"})
                    s *= float(rng.uniform(0.60, 0.80))  # 拋光部分削減
                else:
                    event_rows.append({"ship_id": ship_id, "event_date": date,
                                       "event_type": "cleaning", "notes": "hull cleaning"})
                    s *= float(rng.uniform(0.03, 0.12))  # 清洗近乎歸零
                next_clean_in = int(rng.integers(*cfg.clean_interval_days))
            next_clean_in -= 1

            # --- 結垢生長：夏季（水溫高）長得快 + 隨機游走 ---
            season = 1.0 + 0.5 * np.cos(2 * np.pi * (date.dayofyear - 200) / 365.25)
            s += rate_30d / 30.0 * season + float(rng.normal(0, 0.0006))
            s = float(np.clip(s, 0.0, 0.20))

            truth_rows.append({"ship_id": ship_id, "report_date": date, "true_speed_loss": s})

            # --- 正午報表觀測 ---
            is_port_day = rng.random() < cfg.port_day_prob
            hours = float(rng.uniform(4, 21)) if is_port_day else float(rng.uniform(22.5, 24.0))
            v_cmd = float(rng.normal(design_v, 0.7))
            ballast = rng.random() < 0.25
            draft = draft_laden * (0.62 if ballast else 1.0) + float(rng.normal(0, 0.15))
            draft_factor = 0.88 if ballast else 1.0  # 壓載吃水淺、阻力小
            wind_factor = 1.0 + 0.006 * float(wind[d_idx]) ** 1.6
            foc_day = k * v_cmd**3 * draft_factor * wind_factor * (1.0 + float(rng.normal(0, cfg.weather_noise_sd)))
            v_obs = v_cmd * (1.0 - s) * (1.0 + float(rng.normal(0, 0.004)))
            consump = foc_day / 24.0 * hours

            row = {
                "SHIP_ID": ship_id,
                "SHIP_NAME": name,
                "REPORT_DATE": date.strftime("%Y-%m-%d"),
                "WIND_SCALE": int(wind[d_idx]),
                "HOURS_FULL_SPEED": round(hours, 1),
                "ME_FULLSPEED_CONSUMP_VLSFO": round(consump, 2),
                "AVG_SPEED": round(v_obs, 2),
                "MEAN_DRAFT": round(draft, 2),
            }
            row.update(cfg.extra_columns)
            noon_rows.append(row)

    return {
        "noon_reports": pd.DataFrame(noon_rows),
        "events": pd.DataFrame(event_rows),
        "truth": pd.DataFrame(truth_rows),
    }
