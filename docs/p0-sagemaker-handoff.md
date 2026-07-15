# P0 SageMaker 模型交接

## 交付範圍

P0 僅包含兩個 production model package。Dashboard 的 Persistence、Linear Growth 與 Physics Scenario 是趨勢比較方法，不包含在 SageMaker P0 權重中。

| Package | 直接預測目標 | 系統用途 |
| --- | --- | --- |
| `speed-loss-baseline` | 乾淨狀態相對油耗 `f_rel` | 計算 expected/excess FOC，經單調曲線反演得到目前 Speed Loss |
| `fuel102-ensemble` | 24 小時正規化的 VLSFO 當量 `daily_foc` | 預測遮蔽格的全速主機燃油消耗量 |

`speed-loss-baseline` 不直接學習 Speed Loss。這項限制維持 ADR-0001 的單一油耗方向模型與 monotone inversion 語意。

## 產生交接包

先完成正式資料 pipeline：

```bash
python -m app.pipeline.ingest_yangming data/yangming-aws-summit-hackathon
python -m app.pipeline.run
python scripts/export_p0_models.py
```

預設輸出：

```text
data/sagemaker-p0/
├── bundle-manifest.json
├── README.md
├── speed-loss-baseline/
│   ├── manifest.json
│   ├── model.json
│   ├── clean_refs.csv
│   └── sample.json
└── fuel102-ensemble/
    ├── manifest.json
    ├── anomaly-fallback.json
    ├── sample.json
    └── members/
        ├── a_same_day-seed-*.json
        └── c_pre_post_anchor-seed-*.json
```

另產生可交付的 `data/sagemaker-p0.tar.gz`。整個 `data/` 由 Git 忽略，壓縮檔必須透過私人 S3、受控雲端硬碟或其他私密管道交接。

## 推論契約

### Speed Loss baseline

模型輸入依序為 `v_rel`、`wind`、`draft_rel`，直接輸出 `f_rel`。服務端必須同時載入 `clean_refs.csv`，以每船的 `v_ref`、`f_ref` 與 `draft_ref` 正規化輸入。Speed Loss 必須使用 `app/pipeline/baseline.py` 的單調曲線反演，不可將 XGBoost 的 `f_rel` 直接標成 Speed Loss。

### Fuel 102 ensemble

一般航行資料同時送入 `A_same_day` 與 `C_pre_post_anchor` 兩組特徵的 10 個模型，回應取中位數。`STW <= 5 knots` 時改用 `anomaly-fallback.json`。模型輸出是 VLSFO 當量的 24 小時油耗率，最後必須依實際全速時數與燃料 LCV 換算成 MT：

```text
predicted_value = daily_foc × hours_full_speed / 24 × LCV_VLSFO / LCV_fuel
```

完整特徵順序、LCV、模型成員、訓練參數、驗證結果與 checksum 均在各 package 的 `manifest.json`。

## SageMaker 封裝注意事項

- 交接包是模型與契約，不是已完成的 SageMaker container。
- 前處理必須與 manifest 的 `source_modules` 一致。
- 10 個 ensemble 成員應放在同一個 endpoint，由 inference handler 聚合中位數，不建立 10 個 endpoints。
- 模型、乾淨基準參照與輸入資料可能包含衍生船隊資訊，只能放在 private bucket。
- 部署前以 `sample.json` 做 smoke test，並檢查回應為有限值且欄位單位正確。
