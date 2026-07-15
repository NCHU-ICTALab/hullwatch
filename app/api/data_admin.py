"""資料重置：把站台資料層拉回「原始資料集」的乾淨狀態。

網站的日報上傳（單筆／CSV）只改記憶體中的 FleetService 狀態，累積多了資料會髒；
重置流程從原始資料集來源重走一次完整資料路徑，然後熱替換服務（不重啟容器）：

    來源（HW_RESET_DATASET_URI：s3://bucket/prefix/ 或本地目錄）
      → data/raw/（canonical）→ run_pipeline 重建 artifacts
      → 換上新的 FleetService ＋ Advisor

來源格式自動偵測：
- 陽明官方資料集（vt_fd.csv ＋ maintenance.csv）→ 走 ingest_yangming
- canonical raw（noon_reports.csv ＋ events.csv）→ 直接複製
訂閱、上傳模型、油價快取是 artifacts 目錄下的獨立檔案，重置不動它們。

重建耗時約 1–3 分鐘，超過 CloudFront 60s origin timeout，因此 API 採
「啟動即回 202 ＋ 背景執行緒 ＋ 狀態輪詢」；失敗時保留舊服務繼續供應。
"""

from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import config

CANONICAL_FILES = ("noon_reports.csv", "events.csv", "predict_targets.csv", "truth.csv")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _download_s3_prefix(uri: str, cache_dir: Path) -> Path:
    """s3://bucket/prefix/ 下的 *.csv 全下載到 cache_dir（攤平檔名）。"""
    import boto3  # 延遲載入：本地目錄來源不需要 AWS SDK

    bucket, _, prefix = uri.removeprefix("s3://").partition("/")
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True)
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(".csv"):
                continue
            s3.download_file(bucket, key, str(cache_dir / Path(key).name))
            count += 1
    if count == 0:
        raise ValueError(f"S3 來源沒有任何 CSV：{uri}")
    return cache_dir


def _stage_raw(src_dir: Path, raw_dir: Path) -> str:
    """來源目錄 → canonical data/raw/（覆蓋既有內容，清掉殘留檔）。"""
    if (src_dir / "vt_fd.csv").exists():
        from app.pipeline.ingest_yangming import ingest

        stats = ingest(src_dir, raw_dir)
        return (f"官方資料集轉檔：{stats['noon_rows']} 筆日報 / "
                f"{stats['events']} 事件 / {stats['targets']} 預測格")
    if (src_dir / "noon_reports.csv").exists():
        if src_dir.resolve() != raw_dir.resolve():
            raw_dir.mkdir(parents=True, exist_ok=True)
            for name in CANONICAL_FILES:
                target = raw_dir / name
                target.unlink(missing_ok=True)
                if (src_dir / name).exists():
                    shutil.copy(src_dir / name, target)
        return "canonical raw 已就位"
    raise ValueError(f"來源缺 vt_fd.csv 或 noon_reports.csv：{src_dir}")


def resolve_source() -> str:
    """重置來源：環境變數優先，否則退回本地資料集目錄，再退回現有 raw。"""
    if config.RESET_DATASET_URI:
        return config.RESET_DATASET_URI
    local = config.DATA_DIR / "yangming-aws-summit-hackathon"
    if local.exists():
        return str(local)
    return str(config.DATA_DIR / "raw")


class DataResetService:
    """單一進行中重置的狀態機（idle → running → done | error）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "state": "idle", "step": None, "source": None,
            "started_at": None, "finished_at": None, "error": None, "summary": None,
        }

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kv) -> None:
        with self._lock:
            self._state.update(kv)

    def start(self, app) -> dict:
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("資料重置正在進行中，請等它完成")
            source = resolve_source()
            self._state.update(state="running", step="準備來源", source=source,
                               started_at=_now(), finished_at=None, error=None, summary=None)
        threading.Thread(target=self._run, args=(app, source), daemon=True).start()
        return self.status()

    def _run(self, app, source: str) -> None:
        from app.llm.advisor import Advisor
        from app.llm.provider import get_chat_model
        from app.llm.retrieval import get_retriever
        from app.api.service import FleetService
        from app.pipeline.run import run_pipeline

        try:
            raw_dir = config.DATA_DIR / "raw"
            if source.startswith("s3://"):
                self._set(step="從 S3 下載原始資料集")
                src_dir = _download_s3_prefix(source, config.DATA_DIR / "dataset-cache")
            else:
                src_dir = Path(source)
                if not src_dir.exists():
                    raise ValueError(f"本地資料集來源不存在：{src_dir}")

            self._set(step="轉出 canonical raw")
            staged = _stage_raw(src_dir, raw_dir)

            self._set(step="重建管線 artifacts（約 1–2 分鐘）")
            summary = run_pipeline(raw_dir, config.ARTIFACT_DIR)

            self._set(step="重載服務")
            service = FleetService()
            advisor = Advisor(service, get_retriever(), get_chat_model())
            app.state.service = service
            app.state.advisor = advisor

            self._set(state="done", step=staged, finished_at=_now(),
                      summary={"n_ships": summary["n_ships"],
                               "n_rows_scored": summary["n_rows_scored"]})
        except Exception as exc:  # noqa: BLE001 —— 失敗保留舊服務，錯誤進狀態回報
            self._set(state="error", step=None, finished_at=_now(), error=str(exc))
