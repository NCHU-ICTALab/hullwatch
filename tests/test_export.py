"""交件結果檔輸出器測試。"""

import pandas as pd
import pytest

from app.pipeline.export import export_submission
from app.pipeline.run import generate_and_save, run_pipeline
from app.synth.generator import GeneratorConfig


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("exp")
    raw, art = tmp / "raw", tmp / "artifacts"
    generate_and_save(raw, GeneratorConfig(n_ships=3, start="2021-01-01",
                                           end="2022-06-30", seed=9))
    run_pipeline(raw, art)
    return tmp, art


def test_export_submission_excel(artifacts):
    tmp, art = artifacts
    out = export_submission(art, tmp / "結果.xlsx")
    x = pd.ExcelFile(out)
    assert set(x.sheet_names) == {"每船摘要與清洗建議", "每日預測明細", "方法與驗證"}
    summary = pd.read_excel(x, "每船摘要與清洗建議")
    assert len(summary) == 3
    assert "清洗建議" in summary.columns
    detail = pd.read_excel(x, "每日預測明細")
    assert len(detail) > 500
    assert "Speed Loss (%)" in detail.columns
