"""全域設定。所有可能在比賽當天變動的參數集中此處，一律可用環境變數覆寫。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("HW_DATA_DIR", BASE_DIR / "data"))
ARTIFACT_DIR = DATA_DIR / "artifacts"
KB_DIR = Path(os.environ.get("HW_KB_DIR", BASE_DIR / "kb"))
REACT_DIST_DIR = BASE_DIR / "webapp" / "dist"
LEGACY_FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_DIR = Path(os.environ.get(
    "HW_FRONTEND_DIR",
    REACT_DIST_DIR if (REACT_DIST_DIR / "index.html").exists() else LEGACY_FRONTEND_DIR,
))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _s(name: str, default: str) -> str:
    return os.environ.get(name, default)


# --- 資料篩選（命題規格） ---
GOOD_WEATHER_MAX_WIND = _f("HW_MAX_WIND", 4)          # Beaufort ≤ 4
MIN_FULL_SPEED_HOURS = _f("HW_MIN_HOURS", 22)          # 全速 ≥ 22h

# --- 乾淨基準 ---
BASELINE_WINDOW_DAYS = int(_f("HW_BASELINE_DAYS", 45))  # 清洗/塢修後視為乾淨的天數
BASELINE_MIN_ROWS = int(_f("HW_BASELINE_MIN_ROWS", 10))  # 少於此筆數的基準窗口不可用

# --- 經濟參數（prototype 預設值，當天可改） ---
VLSFO_PRICE_USD = _f("HW_FUEL_PRICE", 600.0)           # USD / 噸
CLEANING_COST_USD = _f("HW_CLEAN_COST", 20000.0)       # 單次水下清潔
PP_COST_USD = _f("HW_PP_COST", 8000.0)
UWC_COST_USD = _f("HW_UWC_COST", CLEANING_COST_USD)
COMBINED_CLEAN_COST_USD = _f("HW_COMBINED_CLEAN_COST", 25000.0)
CLEANING_THRESHOLD_PCT = _f("HW_THRESHOLD", 10.0)      # Speed Loss 清洗門檻 %
WATCH_WINDOW_DAYS = int(_f("HW_WATCH_WINDOW", 60))     # 幾天內會越門檻列為「留意」
SMOOTH_WINDOW_DAYS = int(_f("HW_SMOOTH_WINDOW", 14))   # Speed Loss 滾動平滑窗口（真資料噪音大）
ROI_HORIZON_DAYS = int(_f("HW_ROI_HORIZON", 180))
CO2_PER_TON_FUEL = _f("HW_CO2_FACTOR", 3.114)          # 噸 CO₂ / 噸 VLSFO

# --- 反演搜尋範圍（節） ---
SPEED_SEARCH_LO = 6.0
SPEED_SEARCH_HI = 28.0

# --- LLM ---
LLM_PROVIDER = _s("HW_LLM_PROVIDER", "stub")           # stub | bedrock
BEDROCK_MODEL_ID = _s("HW_BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
BEDROCK_REGION = _s("HW_BEDROCK_REGION", "us-east-1")
RETRIEVER = _s("HW_RETRIEVER", "local")                # local | bedrock_kb
BEDROCK_KB_ID = _s("HW_BEDROCK_KB_ID", "")

# --- notifications (empty means configured channel is disabled) ---
SES_FROM_EMAIL = _s("HW_SES_FROM_EMAIL", "")
SES_REGION = _s("HW_SES_REGION", "us-east-1")
DISCORD_WEBHOOK_URL = _s("HW_DISCORD_WEBHOOK_URL", "")

# --- fuel market ---
FUEL_LIVE_ENABLED = _s("HW_FUEL_LIVE_ENABLED", "1").lower() not in {"0", "false", "no"}
FUEL_REFRESH_HOURS = int(_f("HW_FUEL_REFRESH_HOURS", 6))
FUEL_STALE_HOURS = int(_f("HW_FUEL_STALE_HOURS", 24))
FUEL_HTTP_TIMEOUT_SECONDS = _f("HW_FUEL_HTTP_TIMEOUT", 8.0)
