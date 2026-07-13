"""水下報告文字解析的測試（PDF 抽取當天才有真檔案，先測格式無關的核心）。"""

from app.pipeline.report_parser import parse_events_from_text

SAMPLE_ZH = """
船名：YM-9021 定期水下檢查報告
檢查日期：2024年3月15日
發現船艏至舯段中度附著，建議安排水下清潔。
後續於 2024/04/02 完成船體清潔作業，並同日執行螺旋槳拋光。
"""

SAMPLE_EN = """
Vessel YM 9034 — Underwater inspection carried out on 12 Mar 2024.
Hull cleaning completed 28 March 2024 at Kaohsiung anchorage.
"""


def test_parse_chinese_report():
    df = parse_events_from_text(SAMPLE_ZH)
    assert set(df["event_type"]) == {"inspection", "cleaning", "propeller_polish"}
    assert (df["ship_id"] == "YM-9021").all()
    clean = df[df["event_type"] == "cleaning"].iloc[0]
    assert clean["event_date"] == "2024-04-02"


def test_parse_english_report():
    df = parse_events_from_text(SAMPLE_EN)
    assert "cleaning" in set(df["event_type"])
    assert (df["ship_id"] == "YM-9034").all()


def test_no_events_returns_empty():
    df = parse_events_from_text("這份文件與水下作業無關。2024-01-01")
    assert len(df) == 0
