"""交件用「預測結果檔案」輸出器（提案繳交內容第 7 項）。

把內部 artifacts 整理成評審可直接閱讀的 Excel（中文欄名、三張工作表）：
- 每船摘要：當前 Speed Loss、髒污等級、清洗建議、經濟指標
- 每日預測明細：實測/預期油耗、超額油耗、Speed Loss（含平滑）
- 方法與驗證：模型設定、驗證指標、假設參數

用法（比賽當天跑完管線後）：
    python -m app.pipeline.export -o data/submission/預測結果_HullWatch.xlsx
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app import config, schema

LEVEL_ZH = {"low": "低", "medium": "中", "high": "高"}

SUMMARY_COLS = {
    "ship_id": "船舶代號",
    "ship_name": "船名",
    "current_speed_loss_pct": "當前 Speed Loss (%)",
    "fouling_level": "髒污等級",
    "days_since_clean": "距上次清洗 (天)",
    "growth_pp_per_day": "結垢率 (pp/天)",
    "days_to_threshold": "預估越過門檻 (天)",
    "excess_cost_per_day": "每日超額成本 (USD)",
    "f_ref": "乾淨基準油耗 (噸/天)",
    "last_date": "資料截止日",
}

DETAIL_COLS = {
    schema.SHIP_ID: "船舶代號",
    schema.REPORT_DATE: "日期",
    schema.AVG_SPEED: "實測航速 (節)",
    schema.DAILY_FOC: "實測 DailyFOC (噸)",
    "expected_foc": "預期油耗_乾淨基準 (噸)",
    "excess_foc": "超額油耗 (噸)",
    "speed_loss_pct": "Speed Loss (%)",
    "speed_loss_smooth": "Speed Loss_7日平滑 (%)",
    "days_since_clean": "距上次清洗 (天)",
}


def export_submission(artifact_dir: Path | None = None, out_path: Path | None = None) -> Path:
    """輸出交件 Excel。回傳實際寫入路徑。"""
    d = Path(artifact_dir or config.ARTIFACT_DIR)
    out = Path(out_path or (config.DATA_DIR / "submission" / "預測結果_HullWatch.xlsx"))
    out.parent.mkdir(parents=True, exist_ok=True)

    fleet = pd.read_csv(d / "fleet.csv")
    scored = pd.read_csv(d / "scored.csv", parse_dates=[schema.REPORT_DATE])
    summary = json.loads((d / "summary.json").read_text())

    fsheet = fleet[list(SUMMARY_COLS)].rename(columns=SUMMARY_COLS)
    fsheet["髒污等級"] = fsheet["髒污等級"].map(LEVEL_ZH).fillna(fsheet["髒污等級"])
    fsheet["清洗建議"] = fleet.apply(
        lambda r: "立即安排清洗" if (pd.notna(r.days_to_threshold) and r.days_to_threshold == 0)
        else (f"{int(r.days_to_threshold)} 天內安排" if pd.notna(r.days_to_threshold)
              and r.days_to_threshold <= config.WATCH_WINDOW_DAYS else "持續監測"), axis=1)

    dsheet = scored[list(DETAIL_COLS)].rename(columns=DETAIL_COLS).round(3)
    dsheet["日期"] = dsheet["日期"].dt.strftime("%Y-%m-%d")

    meta_rows = [
        ("模型", "乾淨基準 XGBoost（油耗對航速單調遞增約束）+ 曲線反演求 Speed Loss"),
        ("Speed Loss 語意", "相對該船最近一次清洗後基準期（ISO 19030 基準法）"),
        ("資料篩選", f"風力 ≤ {config.GOOD_WEATHER_MAX_WIND} 級且全速 ≥ {config.MIN_FULL_SPEED_HOURS} 小時"),
        ("乾淨基準窗口", f"清洗/塢修後 {config.BASELINE_WINDOW_DAYS} 天"),
        ("清洗門檻", f"Speed Loss {config.CLEANING_THRESHOLD_PCT}%"),
        ("經濟假設", f"VLSFO {config.VLSFO_PRICE_USD} USD/噸；單次清潔 {config.CLEANING_COST_USD} USD"),
        ("驗證方式", "Leave-One-Ship-Out + 時間分塊（無隨機交叉驗證）"),
    ]
    for k, v in (summary.get("validation") or {}).items():
        meta_rows.append((f"驗證指標 {k}", round(v, 4) if isinstance(v, float) else v))
    msheet = pd.DataFrame(meta_rows, columns=["項目", "說明"])

    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        fsheet.to_excel(xw, sheet_name="每船摘要與清洗建議", index=False)
        dsheet.to_excel(xw, sheet_name="每日預測明細", index=False)
        msheet.to_excel(xw, sheet_name="方法與驗證", index=False)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", type=Path, default=None)
    args = ap.parse_args()
    p = export_submission(out_path=args.out)
    print(f"[OK] 交件結果檔已輸出: {p}")
