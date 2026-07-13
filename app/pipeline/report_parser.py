"""水下報告 → events.csv 解析骨架。

真實報告格式 7/14 才會知道，本模組先把「格式無關」的部分做好：
- PDF 文字抽取（pypdf；掃描檔需 OCR 時當天改接 Bedrock 多模態）
- 從自由文字辨識（日期, 事件類型, 船名）三元組
- 輸出/追加符合 schema 的 events.csv

當天只需要調整 ``EVENT_KEYWORDS`` 與（必要時）``DATE_PATTERNS``，
下游（事件對齊、儀表板、顧問）完全不動。

用法：
    python -m app.pipeline.report_parser 報告.pdf --ship-id YM-9001
    python -m app.pipeline.report_parser 報告資料夾/ --out data/raw/events.csv --append
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# 關鍵字 → canonical 事件類型（比賽當天照真實報告用語增補）
EVENT_KEYWORDS: list[tuple[str, str]] = [
    (r"水下清潔|船體清潔|hull\s*clean", "cleaning"),
    (r"螺槳拋光|螺旋槳拋光|propeller\s*polish", "propeller_polish"),
    (r"水下檢查|水下檢驗|underwater\s*inspect", "inspection"),
    (r"塢修|進塢|dry\s*-?\s*dock", "drydock"),
]

DATE_PATTERNS = [
    r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})",   # 2024-03-15 / 2024/3/15 / 2024年3月15日
    r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(\d{4})",
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

SHIP_ID_PATTERN = r"YM[-\s]?(\d{4})"


def extract_text(pdf_path: Path) -> str:
    """抽出 PDF 全文；抽不到字（掃描檔）時回空字串並警告。"""
    from pypdf import PdfReader

    text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf_path)).pages)
    if len(text.strip()) < 20:
        print(f"[warn] {pdf_path.name} 幾乎抽不到文字，可能是掃描檔——當天改走 Bedrock 多模態 OCR")
    return text


def _find_dates(text: str) -> list[pd.Timestamp]:
    out = []
    for m in re.finditer(DATE_PATTERNS[0], text):
        y, mo, d = (int(x) for x in m.groups())
        if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((m.start(), pd.Timestamp(y, mo, d)))
    for m in re.finditer(DATE_PATTERNS[1], text, re.IGNORECASE):
        d, mon, y = m.groups()
        out.append((m.start(), pd.Timestamp(int(y), _MONTHS[mon[:3].title()], int(d))))
    return out


def parse_events_from_text(text: str, ship_id: str | None = None,
                           source: str = "") -> pd.DataFrame:
    """從報告全文辨識事件列。

    策略：每個事件關鍵字出現處，取「距離最近的日期」配對；
    ship_id 未指定時嘗試從文中抓 YM-XXXX。結果一律需人工過目
    （CLI 會列印供確認），這是刻意設計——當天格式未知，寧可保守。
    """
    dates = _find_dates(text)
    if ship_id is None:
        m = re.search(SHIP_ID_PATTERN, text)
        ship_id = f"YM-{m.group(1)}" if m else None
    rows = []
    for pattern, etype in EVENT_KEYWORDS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if not dates:
                continue
            nearest = min(dates, key=lambda dp: abs(dp[0] - m.start()))
            rows.append({
                "ship_id": ship_id or "UNKNOWN",
                "event_date": nearest[1].strftime("%Y-%m-%d"),
                "event_type": etype,
                "notes": f"parsed from {source or 'text'}",
            })
    df = pd.DataFrame(rows, columns=["ship_id", "event_date", "event_type", "notes"])
    return df.drop_duplicates(subset=["ship_id", "event_date", "event_type"]).reset_index(drop=True)


def parse_path(path: Path, ship_id: str | None = None) -> pd.DataFrame:
    """解析單一 PDF 或整個資料夾。"""
    files = sorted(path.glob("*.pdf")) if path.is_dir() else [path]
    parts = [parse_events_from_text(extract_text(f), ship_id=ship_id, source=f.name)
             for f in files]
    return (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=["ship_id", "event_date", "event_type", "notes"]))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path, help="PDF 檔或資料夾")
    ap.add_argument("--ship-id", default=None)
    ap.add_argument("--out", type=Path, default=None, help="寫入 events.csv 路徑")
    ap.add_argument("--append", action="store_true", help="追加到既有 events.csv")
    args = ap.parse_args()

    df = parse_path(args.path, ship_id=args.ship_id)
    print(df.to_string(index=False) if len(df) else "（未辨識到任何事件——檢查 EVENT_KEYWORDS）")
    if args.out and len(df):
        if args.append and args.out.exists():
            df = pd.concat([pd.read_csv(args.out), df], ignore_index=True) \
                   .drop_duplicates(subset=["ship_id", "event_date", "event_type"])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False)
        print(f"[OK] 已寫入 {args.out}（{len(df)} 列）——請人工確認後再跑管線")
