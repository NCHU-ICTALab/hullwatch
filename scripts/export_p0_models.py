"""Export the two production P0 model packages for SageMaker handoff.

The output is intentionally written below ``data/`` so model weights and
derived fleet references remain outside Git. The bundle contains model
artifacts and inference contracts; infrastructure packaging is left to the
SageMaker deployment project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, schema
from app.pipeline.baseline import CleanBaselineModel
from app.pipeline.features import MODEL_FEATURES, MONOTONE_CONSTRAINTS
from app.pipeline.ingest_yangming import LCV, LCV_VLSFO
from app.pipeline.predict102 import (
    FEATURES,
    PARAMS,
    TARGET,
    _fit_anomaly_fallback,
    _load_tuned_params,
    _trainable,
    build_dataset,
)
from scripts.run_experiments import ANCHOR_POST, ANCHOR_PRE, add_anchor_features, make_model

P0_MODEL_IDS = {"speed-loss-baseline", "fuel102-ensemble"}
ENSEMBLE_SEEDS = [42, 7, 2024, 555, 31337]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def _load_xgboost_artifact(path: Path) -> None:
    booster = xgb.Booster()
    booster.load_model(path)


def validate_p0_bundle(root: Path) -> dict:
    """Validate the public handoff contract and load every XGBoost artifact."""
    root = Path(root)
    bundle_path = root / "bundle-manifest.json"
    if not bundle_path.exists():
        raise ValueError("缺少 bundle-manifest.json")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    package_rows = bundle.get("packages", [])
    package_ids = {item.get("model_id") for item in package_rows}
    missing = P0_MODEL_IDS - package_ids
    if missing:
        raise ValueError(f"缺少 P0 model package: {', '.join(sorted(missing))}")

    inventory = {}
    for row in package_rows:
        model_id = row["model_id"]
        package_dir = root / row["path"]
        manifest_path = package_dir / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"{model_id} 缺少 manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = manifest.get("artifacts", [])
        if not artifacts:
            raise ValueError(f"{model_id} 沒有模型 artifact")
        for artifact in artifacts:
            artifact_path = package_dir / artifact["path"]
            if not artifact_path.exists():
                raise ValueError(f"{model_id} 缺少 {artifact['path']}")
            if artifact.get("sha256") != _sha256(artifact_path):
                raise ValueError(f"{model_id} checksum 不符: {artifact['path']}")
            if artifact.get("kind") == "xgboost-model":
                _load_xgboost_artifact(artifact_path)
        inventory[model_id] = len([
            artifact for artifact in artifacts
            if artifact.get("kind") == "xgboost-model"
        ])

    if inventory.get("speed-loss-baseline") != 1:
        raise ValueError("speed-loss-baseline 必須包含 1 個 XGBoost model")
    if inventory.get("fuel102-ensemble") != 11:
        raise ValueError("fuel102-ensemble 必須包含 10 個 ensemble members 與 1 個 anomaly fallback")
    return {"valid": True, "model_inventory": inventory}


def _export_baseline(package_dir: Path) -> dict:
    package_dir.mkdir(parents=True)
    source_model = config.ARTIFACT_DIR / "baseline_model.json"
    source_refs = config.ARTIFACT_DIR / "clean_refs.csv"
    if not source_model.exists() or not source_refs.exists():
        raise FileNotFoundError("請先執行 python -m app.pipeline.run 產生 baseline artifacts")

    model_path = package_dir / "model.json"
    refs_path = package_dir / "clean_refs.csv"
    shutil.copy2(source_model, model_path)
    shutil.copy2(source_refs, refs_path)

    model = CleanBaselineModel.load(model_path)
    request = {
        "avg_speed": 15.0,
        "daily_foc": 31.0,
        "wind_scale": 2.0,
        "mean_draft": 10.0,
        "v_ref": 16.0,
        "f_ref": 28.0,
        "draft_ref": 10.0,
    }
    row = pd.DataFrame([{
        schema.AVG_SPEED: request["avg_speed"],
        schema.DAILY_FOC: request["daily_foc"],
        "v_rel": request["avg_speed"] / request["v_ref"],
        "f_rel": request["daily_foc"] / request["f_ref"],
        "wind": request["wind_scale"],
        "draft_rel": request["mean_draft"] / request["draft_ref"],
        "v_ref": request["v_ref"],
        "f_ref": request["f_ref"],
    }])
    scored = model.score_rows(row).iloc[0]
    _write_json(package_dir / "sample.json", {
        "request": request,
        "response": {
            "expected_foc": round(float(scored["expected_foc"]), 6),
            "excess_foc": round(float(scored["excess_foc"]), 6),
            "speed_loss_pct": round(float(scored["speed_loss_pct"]), 6),
        },
    })

    artifacts = [
        {"path": model_path.name, "kind": "xgboost-model", "sha256": _sha256(model_path)},
        {"path": refs_path.name, "kind": "fleet-reference-data", "sha256": _sha256(refs_path)},
    ]
    manifest = {
        "schema_version": 1,
        "model_id": "speed-loss-baseline",
        "display_name": "HullWatch Clean-Baseline XGBoost",
        "stage": "production",
        "task": "Predict clean-state relative fuel consumption and derive current Speed Loss by monotone curve inversion",
        "direct_target": "f_rel",
        "derived_outputs": ["expected_foc", "excess_foc", "speed_loss_pct"],
        "features": MODEL_FEATURES,
        "monotone_constraints": list(MONOTONE_CONSTRAINTS),
        "reference_data": "clean_refs.csv",
        "sample_contract": "sample.json",
        "source_modules": ["app/pipeline/baseline.py", "app/pipeline/features.py"],
        "runtime": {"python": ">=3.10", "xgboost": version("xgboost")},
        "artifacts": artifacts,
    }
    _write_json(package_dir / "manifest.json", manifest)
    return {"model_id": manifest["model_id"], "path": package_dir.name}


def _finite_median(frame: pd.DataFrame, feature: str) -> float:
    value = float(frame[feature].median())
    if not np.isfinite(value):
        return 0.0
    return value


def _validation_summary() -> dict:
    candidates = [Path("results_local/report.json"), Path("results_ec2/report.json")]
    for path in candidates:
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        if report.get("final_ensemble_mape_pct") is not None:
            return {
                "method": "maintenance-window masked validation",
                "micro_mape_pct": report["final_ensemble_mape_pct"],
                "source": str(path).replace("\\", "/"),
            }
    return {"method": "maintenance-window masked validation", "micro_mape_pct": None}


def _export_fuel_ensemble(package_dir: Path) -> dict:
    package_dir.mkdir(parents=True)
    members_dir = package_dir / "members"
    members_dir.mkdir()

    frame, _targets = build_dataset()
    frame = add_anchor_features(frame)
    train = _trainable(frame)
    same_day = FEATURES + [column for column in frame.columns if column.startswith("ship_S")]
    feature_sets = {
        "A_same_day": same_day,
        "C_pre_post_anchor": same_day + ANCHOR_PRE + ANCHOR_POST,
    }
    sample_features = {
        name: {feature: _finite_median(train, feature) for feature in features}
        for name, features in feature_sets.items()
    }

    artifacts = []
    members = []
    sample_predictions = []
    for feature_set, features in feature_sets.items():
        sample_frame = pd.DataFrame([sample_features[feature_set]], columns=features)
        for seed in ENSEMBLE_SEEDS:
            model = make_model("xgb", seed=seed)
            model.fit(train[features], train[TARGET])
            filename = f"{feature_set.lower()}-seed-{seed}.json"
            model_path = members_dir / filename
            model.save_model(model_path)
            prediction = float(model.predict(sample_frame)[0])
            sample_predictions.append(prediction)
            artifact_path = f"members/{filename}"
            artifacts.append({
                "path": artifact_path,
                "kind": "xgboost-model",
                "sha256": _sha256(model_path),
            })
            members.append({
                "artifact": artifact_path,
                "feature_set": feature_set,
                "features": features,
                "seed": seed,
            })

    fallback = _fit_anomaly_fallback(frame)
    fallback_path = package_dir / "anomaly-fallback.json"
    fallback.save_model(fallback_path)
    artifacts.append({
        "path": fallback_path.name,
        "kind": "xgboost-model",
        "role": "stw-at-or-below-5-knots",
        "sha256": _sha256(fallback_path),
    })

    median_daily_foc = float(np.median(sample_predictions))
    _write_json(package_dir / "sample.json", {
        "request": {
            "feature_sets": sample_features,
            "hours_full_speed": 24.0,
            "fuel": "VLSFO",
        },
        "response": {
            "member_daily_foc_predictions": [round(value, 6) for value in sample_predictions],
            "median_daily_foc_vlsfo_equivalent": round(median_daily_foc, 6),
            "predicted_value_mt": round(median_daily_foc * (LCV_VLSFO / LCV["VLSFO"]), 6),
        },
    })

    tuned = {**PARAMS, **_load_tuned_params()}
    tuned.pop("early_stopping_rounds", None)
    manifest = {
        "schema_version": 1,
        "model_id": "fuel102-ensemble",
        "display_name": "HullWatch 102-Cell Fuel XGBoost Ensemble",
        "stage": "production",
        "task": "Predict masked full-speed main-engine fuel consumption",
        "direct_target": "daily_foc_vlsfo_equivalent_24h",
        "output": "fuel_consumption_mt_for_reported_full_speed_hours",
        "aggregation": "median",
        "member_count": len(members),
        "members": members,
        "anomaly_fallback": {
            "artifact": fallback_path.name,
            "condition": "stw <= 5 knots",
            "features": ["me_rpm", "rpm3"],
        },
        "fuel_lcv_mj_per_kg": LCV,
        "vlsfo_lcv_mj_per_kg": LCV_VLSFO,
        "training_config": tuned,
        "validation": _validation_summary(),
        "sample_contract": "sample.json",
        "source_modules": ["app/pipeline/predict102.py", "scripts/run_experiments.py"],
        "runtime": {"python": ">=3.10", "xgboost": version("xgboost")},
        "artifacts": artifacts,
    }
    _write_json(package_dir / "manifest.json", manifest)
    return {"model_id": manifest["model_id"], "path": package_dir.name}


def export_p0_bundle(output_dir: Path) -> tuple[Path, Path, dict]:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"輸出目錄已存在，請改用新路徑或先人工確認後移除：{output_dir}")
    output_dir.mkdir(parents=True)

    packages = [
        _export_baseline(output_dir / "speed-loss-baseline"),
        _export_fuel_ensemble(output_dir / "fuel102-ensemble"),
    ]
    _write_json(output_dir / "bundle-manifest.json", {
        "schema_version": 1,
        "bundle_id": "hullwatch-p0-models",
        "purpose": "SageMaker model handoff",
        "packages": packages,
        "security": "Contains derived fleet model artifacts; keep in private storage.",
    })
    (output_dir / "README.md").write_text(
        "# HullWatch P0 model handoff\n\n"
        "This private bundle contains two production packages:\n\n"
        "- `speed-loss-baseline`: clean-state fuel model plus Speed Loss inversion contract.\n"
        "- `fuel102-ensemble`: ten XGBoost members plus the low-STW anomaly fallback.\n\n"
        "Each package has a `manifest.json`, checksummed model artifacts and `sample.json`. "
        "The SageMaker image must reproduce the preprocessing named in `source_modules`; "
        "model JSON files alone are not a complete inference service.\n",
        encoding="utf-8",
    )
    validation = validate_p0_bundle(output_dir)

    archive_path = output_dir.with_suffix(".tar.gz")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(output_dir, arcname=output_dir.name)
    return output_dir, archive_path, validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=config.DATA_DIR / "sagemaker-p0",
        help="Private output directory (default: data/sagemaker-p0)",
    )
    args = parser.parse_args()
    root, archive, result = export_p0_bundle(args.out)
    print(json.dumps({
        "output_dir": str(root),
        "archive": str(archive),
        **result,
    }, ensure_ascii=False, indent=2))
