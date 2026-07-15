"""Safe model-package metadata and artifact storage.

Only data-only formats are accepted.  The adapter boundary intentionally leaves
room for a future ONNX runtime without ever accepting pickle/joblib objects.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import xgboost as xgb

SUPPORTED_TREND_FEATURES = {
    "week",
    "current_speed_loss_pct",
    "growth_pp_per_day",
    "scenario_speed_kn",
    "reference_speed_kn",
}
BUILTIN_MODEL_IDS = {"linear-growth", "physics-scenario", "persistence"}


class ModelArtifactAdapter(Protocol):
    """Data-only model runtime seam; ONNX can implement this contract later."""

    def validate(self, path: Path, feature_count: int) -> None: ...

    def load(self, path: Path): ...


class XGBoostJsonAdapter:
    def validate(self, path: Path, feature_count: int) -> None:
        booster = self.load(path)
        if booster.num_features() != feature_count:
            raise ValueError("模型特徵數量與 manifest.features 不一致")

    def load(self, path: Path) -> xgb.Booster:
        booster = xgb.Booster()
        try:
            booster.load_model(path)
        except xgb.core.XGBoostError as exc:
            raise ValueError("artifact 不是有效的 XGBoost JSON 模型") from exc
        return booster


MODEL_ADAPTERS: dict[str, ModelArtifactAdapter] = {"xgboost-json": XGBoostJsonAdapter()}
PLANNED_ADAPTERS = ["onnx"]


def manifest_template() -> dict:
    return {
        "id": "speed-loss-xgb-v2",
        "name": "Speed Loss 趨勢模型 v2",
        "version": "2.0.0",
        "model_format": "xgboost-json",
        "purpose": "speed_loss_trend",
        "target": "speed_loss_pct",
        "target_unit": "percent",
        "features": [
            "week",
            "current_speed_loss_pct",
            "growth_pp_per_day",
            "scenario_speed_kn",
            "reference_speed_kn",
        ],
        "feature_units": {
            "week": "week",
            "current_speed_loss_pct": "percent",
            "growth_pp_per_day": "percentage_point/day",
            "scenario_speed_kn": "knot",
            "reference_speed_kn": "knot",
        },
        "training_note": "輸出必須是每個未來 week 的絕對 Speed Loss 百分比。",
    }


class ModelPackageStore:
    def __init__(self, root: Path):
        self.root = root
        self.index_path = root / "registry.json"

    def list(self) -> list[dict]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8")).get("models", [])
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def active_id(self) -> str:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8")).get("active_model_id", "linear-growth")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return "linear-growth"

    def register(self, manifest_text: str, artifact: bytes) -> dict:
        try:
            manifest = json.loads(manifest_text)
        except json.JSONDecodeError as exc:
            raise ValueError("manifest 必須是有效 JSON") from exc
        if not isinstance(manifest, dict):
            raise ValueError("manifest 頂層必須是 JSON object")
        required = {"id", "name", "version", "model_format", "purpose", "target", "target_unit", "features", "feature_units"}
        missing = sorted(required - manifest.keys())
        if missing:
            raise ValueError(f"manifest 缺少欄位：{', '.join(missing)}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", str(manifest["id"])):
            raise ValueError("模型 id 只能使用小寫英數與連字號")
        if manifest["model_format"] not in MODEL_ADAPTERS:
            raise ValueError("第一版只接受 xgboost-json；ONNX adapter 已預留但尚未啟用")
        if manifest["purpose"] != "speed_loss_trend" or manifest["target"] != "speed_loss_pct":
            raise ValueError("模型用途必須是 speed_loss_trend，輸出必須是 speed_loss_pct")
        features = manifest["features"]
        if not isinstance(features, list) or not features or len(features) != len(set(features)):
            raise ValueError("features 必須是非空且不重複的陣列")
        unknown = sorted(set(features) - SUPPORTED_TREND_FEATURES)
        if unknown:
            raise ValueError(f"不支援的趨勢特徵：{', '.join(unknown)}")
        if not isinstance(manifest["feature_units"], dict):
            raise ValueError("feature_units 必須是 JSON object")
        if set(features) - set(manifest["feature_units"]):
            raise ValueError("每個 feature 都必須宣告單位")
        if len(artifact) > 20 * 1024 * 1024:
            raise ValueError("模型檔案不得超過 20MB")

        existing_ids = {item["id"] for item in self.list()}
        if manifest["id"] in BUILTIN_MODEL_IDS or manifest["id"] in existing_ids:
            raise ValueError("模型 id 與版本必須不可變；請使用新的 id 上傳新版本")

        self.root.mkdir(parents=True, exist_ok=True)
        temporary_path = self.root / f".upload-{uuid4().hex}.json"
        temporary_path.write_bytes(artifact)
        adapter = MODEL_ADAPTERS[manifest["model_format"]]
        try:
            adapter.validate(temporary_path, len(features))
        except ValueError:
            temporary_path.unlink(missing_ok=True)
            raise
        model_dir = self.root / manifest["id"]
        model_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = model_dir / "model.json"
        temporary_path.replace(artifact_path)

        record = {
            **manifest,
            "description": manifest.get("description", "使用者上傳的 Speed Loss 趨勢候選模型。"),
            "validation_mape": None,
            "needs_speed": "scenario_speed_kn" in features,
            "status": "candidate",
            "validation": None,
            "is_primary": False,
            "artifact_path": str(artifact_path.relative_to(self.root)),
        }
        records = self.list()
        records.append(record)
        self._save(records, self.active_id())
        return record

    def update_validation(self, model_id: str, validation: dict) -> dict:
        records = self.list()
        record = next((item for item in records if item["id"] == model_id), None)
        if record is None:
            raise KeyError(model_id)
        record["validation"] = validation
        record["status"] = "validated" if validation["passed"] else "rejected"
        self._save(records, self.active_id())
        return record

    def activate(self, model_id: str) -> dict:
        records = self.list()
        record = next((item for item in records if item["id"] == model_id), None)
        if record is None:
            raise KeyError(model_id)
        if record.get("status") != "validated":
            raise ValueError("候選模型尚未通過共同驗證集")
        for item in records:
            item["is_primary"] = item["id"] == model_id
        self._save(records, model_id)
        return record

    def activate_builtin(self, model_id: str) -> None:
        records = self.list()
        for item in records:
            item["is_primary"] = False
        self._save(records, model_id)

    def restore(self) -> dict:
        records = self.list()
        for item in records:
            item["is_primary"] = False
        self._save(records, "linear-growth")
        return {"active_model_id": "linear-growth"}

    def load_booster(self, model_id: str) -> tuple[xgb.Booster, dict]:
        record = next((item for item in self.list() if item["id"] == model_id), None)
        if record is None:
            raise KeyError(model_id)
        booster = MODEL_ADAPTERS[record["model_format"]].load(self.root / record["artifact_path"])
        return booster, record

    def _save(self, records: list[dict], active_id: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({
            "active_model_id": active_id,
            "models": records,
            "supported_formats": ["xgboost-json"],
            "planned_formats": PLANNED_ADAPTERS,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
