# HullWatch 產品真實性稽核

> 稽核日期：2026-07-15
>
> 稽核範圍：目前工作區的原始資料、canonical raw、runtime artifacts、模型權重、API、Dashboard 計算與部署啟動流程。本文只描述「目前程式實際做什麼」，不把規劃文件當成已完成能力。

## 結論先行

HullWatch **已使用主辦提供的匿名真實資料集**產生目前工作區的 Dashboard artifacts，並已產出 102 格油耗預測檔；但 Dashboard 上多數數字不是感測器直接量測，而是模型或規則推導值。產品目前尚不能宣稱「完全符合命題」：油耗遮蔽預測已完成，Speed Loss 趨勢與事件對照也已完成；最大的缺口是「由同一套油耗模型執行 UWC／PP 反事實推論」尚未成為可執行流程，而船殼／螺旋槳歸因仍是全船隊事件前後效果比例的 proxy，不是逐船因果歸因模型。

另一項必須在簡報前處理的高風險是部署資料來源：目前 Docker image 不包含資料；如果容器沒有掛載 artifacts，啟動腳本會直接執行 `--synth` 產生合成資料。CloudFront 畫面雖顯示 S1–S23，與真實資料 artifacts 相符，但 API 沒有回傳資料版本或 provenance，僅憑目前程式無法證明線上部署載入的是哪一版資料。

## 1. 判讀標準與資料流

本文使用四種標籤：

- **觀測真實資料**：主辦提供、資料表中直接存在的匿名航行日報或養護紀錄；它是真實競賽資料，但不是即時船舶 telemetry。
- **模型／規則推導**：由觀測值經 XGBoost、統計量、立方律、平滑或門檻規則算出。
- **估算／proxy／情境假設**：有物理或事件資料依據，但不能當作直接量測或因果真值。
- **stub／mock／合成**：不依真實業務資料生成，僅供開發、fallback 或 demo 使用。

目前本地資料流如下：

> 下列 `data/` 與 `results_*` 證據檔存放在 private `hullwatch-data` 或本機 gitignored 目錄；公開 repo 只記錄路徑與稽核結論，不公開原始資料或模型權重。

1. 主辦資料 `vt_fd.csv`（21,282 列，含近重複列）與 `maintenance.csv`（77 列）。欄位與 102 個 `PREDICT` 的官方任務定義見 private data repo 的 `data/yangming-aws-summit-hackathon/README.md`。
2. [`ingest_yangming.py`](../app/pipeline/ingest_yangming.py) 去重、熱值換算、以 STW 為主航速，轉成 `data/raw/noon_reports.csv`（20,938 列）、`events.csv`（複合事件拆列後 115 列）與 102 列 targets；並明確刪除合成資料才有的 `truth.csv`。
3. 品質篩選只保留風級 ≤4、全速時數 ≥22、油耗可見且航速 >0 的資料；目前 `data/artifacts/summary.json` 顯示 15 艘、8,025 列被評分。
4. clean-baseline XGBoost、事件前後統計與經濟規則輸出 `scored.csv`、`fleet.csv`、`maintenance_effects.csv` 等 artifacts；API 只讀這些 artifacts，不會每次請求重訓。

ISO 官方對 ISO 19030-1 的公開摘要是：它定義船殼與螺旋槳效能變化的原則與 performance indicators，且目標是「同一艘船與其自身隨時間比較」；因此本文只稱目前方法為 **ISO 19030 對齊／inspired**，不稱為完整或認證合規。[ISO 19030-1 官方頁面](https://www.iso.org/standard/63774.html)

## 2. Dashboard／API 真實性清單

| 功能或欄位 | 分類 | 現在實際來源 | 不能宣稱的事 |
|---|---|---|---|
| 船舶 S1–S23、航速、風級、吃水、全速時數、可見油耗 | 觀測真實資料 | 主辦提供的匿名 `vt_fd.csv`；不是串接中的即時船隊系統 | 不能稱即時 AIS／船端 telemetry；資料最後日期依船不同，最晚到 2025-12-31 |
| PP／UWC／UWI／DD 日期與檢查欄位 | 觀測真實資料 | 主辦 `maintenance.csv` | 事件日期是真的資料列；事件「效果」不是直接量測真值 |
| `daily_foc` | 推導 | 各燃料質量先依 LCV 折成 VLSFO 當量，再除全速時數乘 24 | 不是所有日子的原始 VLSFO 實際消耗；多燃料時是熱值當量 |
| `expected_foc`、`excess_foc` | 模型推導 | clean-baseline XGBoost 預測乾淨狀態油耗；實測減預期為 residual | residual 不保證全是髒污，也可能含模型漏項、量測誤差與未建模海況 |
| Speed Loss 趨勢 | 模型推導 | clean-baseline 曲線反演後，再做 14 日 rolling median；值裁在 -10%～35%，船隊摘要負值再顯示為 0 | 不是直接量測；也不是完整 ISO 19030 default method 的認證結果 |
| 「目前 Speed Loss」 | 模型推導 | 每船最後一筆合格資料的平滑 Speed Loss | 「目前」是資料集最後有效日，不是今天 |
| 16 週預測中線 | 估算／proxy | 當前值 + 最近 120 天線性斜率；斜率小於 0 強制為 0 | 不是時間序列 ML 預測，也沒有校準過的機率意義 |
| 預測帶 | 啟發式 | 最近 12 週標準差裁在 0.3～2.0，再隨週數線性放大 | 不是信賴區間或 prediction interval；原始碼也明確如此註記 |
| `physics-scenario` | 啟發式 | 線性結垢增量乘「情境航速／參考航速」 | 名稱含 physics，但它不是經物理方程校準的船舶模型；目前 registry 顯示它是 active model |
| Persistence v0 | 比較基準 | 未來維持當前 Speed Loss | 不是學習模型 |
| 髒污 low／medium／high | 規則推導 | Speed Loss 分布肘點法；無清楚膝點時退回三分位 | 不是專家標註的真實髒污等級 |
| 正常／留意／處置 | 規則推導 | 5% 留意、10% 處置，或預估 60 天內達門檻 | 不是分類模型或陽明已核准的營運 SOP |
| 每日／每月超額成本 | 情境估算 | Speed Loss + 立方律 + 乾淨基準油耗 + 設定油價 | 不是帳務實際成本，也不是直接採用行情頁即時油價 |
| 每月超額碳排 | 情境估算 | 超額燃油 × 3.114 tCO₂/t-fuel | 不是 CII，也不是 well-to-wake GHG；所有燃料共用同一係數 |
| 船殼／螺旋槳色帶 | proxy | 全船隊 UWC 與純 PP 事件前後 60 日效果中位數比；所有船共用同一比例 | 不是逐船、逐日的 hull/propeller 因果拆解 |
| 油耗歸因瀑布 | 模型推導 + residual proxy | TreeSHAP 拆 clean-baseline 預測成基準、航速、風、吃水；剩餘 residual 標為船體髒污 | SHAP 是模型歸因，不等同物理或因果歸因；「船體髒污」其實是未解釋 residual |
| 養護事件成效 | 觀測後統計 | 事件前後各 60 天的平滑 Speed Loss 中位數差，兩側至少各 5 筆 | 沒有 matched control、信賴區間或因果調整，不能說事件一定造成該差值 |
| 最佳清洗日、回本、排程 | 情境規則 | 180 日掃描、線性結垢、立方律、固定清洗成本、清洗後殘留 0.5% SL | 不是最佳化求解器對真實港期、船塢容量、航線與合約限制的可執行排程 |
| 油價跑馬燈／圖 | 混合 | 優先抓 Ship & Bunker；失敗後用 USDA，再用 Brent 質量 proxy；之後可用 cache | ULSFO 是 LSMGO proxy；Brent 是估算；無來源時 effective price 是人工情境價 |
| 日報上傳 | 使用者輸入 + 即時推導 | 只改當前 FastAPI process 記憶體中的 scored/fleet 狀態 | 不是持久化資料庫；重啟即消失，且只用既有基準模型推論 |
| 警報 | 規則 | 讀上述狀態門檻，in-app read state 也只在記憶體 | 不是異常偵測 ML |
| Email／Discord | 真實整合但需設定 | SES 或 Discord webhook；沒設定會回 `not_configured` | UI 有訂閱功能不等於部署已可送達，須以 send response 驗證 |
| AI 顧問 | 可真 LLM，也可 stub | `HW_LLM_PROVIDER=bedrock` 才用 Bedrock；預設 `stub`，Bedrock 失敗也會退回 scripted template；scripted 已用 10 題 API 回歸測試覆蓋 demo 意圖 | 不能只看聊天畫面就宣稱 Bedrock 正在回答；應顯示／截錄 API 的 `mode`；scripted 命中不等於 LLM 品質 |
| Wiki 檢索 | 可真 Bedrock KB，也可本地 | 預設是本地 TF-IDF；有 KB ID 才使用 Bedrock Knowledge Bases；private Wiki 另有 10 題 provider-neutral 來源／必要詞 hit 測試 | 本地或 lexical 10/10 不是 Bedrock 生成答案的人工語意評分 |

### 部署 fallback：目前最需要公開標示的風險

[`Dockerfile`](../Dockerfile) 沒有 `COPY data`；[`deploy/start.sh`](../deploy/start.sh) 在 `data/artifacts/fleet.csv` 不存在時會執行 `python -m app.pipeline.run --synth`。因此：

- 正確掛載私有 `hullwatch-data/data` 時，可服務真實競賽資料 artifacts。
- 沒有掛載資料時，不會報錯阻止 demo，而會產生 YM-xxxx 船名的合成資料。
- 若 artifacts 路徑存在但缺檔，FastAPI 會在啟動時失敗或回 503；前端沒有內建假資料 fallback。
- 現在 `/api/health` 只說 `artifacts_loaded`，沒有 dataset version、manifest SHA、`real|synthetic` 或 artifact build time。簡報前至少應人工核對 `/api/fleet` 為 S1–S23、`n_ships=15`、資料最後日期與私有 data repo 版本一致，並保留部署 log 作證。

## 3. 每月超額成本：精確公式、單位與限制

目前首頁不是用 `scored.csv` 的最新 `excess_foc` 直接加總，而是從每船的 Speed Loss 套立方律。公式定義於 [`roi.py`](../app/pipeline/roi.py#L26)，彙總定義於 [`service.py`](../app/api/service.py#L594)。

對船 $i$：

\[
s_i=\operatorname{clip}(SpeedLoss_i/100,0,0.35)
\]

\[
ExcessFuel_i=f_{ref,i}\left(\frac{1}{(1-s_i)^3}-1\right)
\quad [\mathrm{metric\ tonnes/day}]
\]

\[
DailyExcessCost_i=ExcessFuel_i\times P
\quad [\mathrm{USD/day}]
\]

\[
MonthlyExcessCost=30\times\sum_i DailyExcessCost_i
\quad [\mathrm{USD/30\ days}]
\]

輸入：

- $SpeedLoss_i$：該船最後一筆平滑 Speed Loss；模型推導值。
- $f_{ref,i}$：該船乾淨基準期 `daily_foc` 中位數，單位為 VLSFO 當量 MT/day。
- $P$：管線建 artifact 時的 `HW_FUEL_PRICE`，預設 **600 USD/mt**；定義見 [`config.py`](../app/config.py#L37)。
- 30：固定 30 日情境，不是當月實際天數。

目前工作區 `fleet.csv` 的每日超額成本總和是 **US$143,950/day**，所以首頁公式得到 **US$4,318,500/30 days**。這是模型情境值，不是發票或會計值。

重要限制：

1. 立方律是假定「維持同航速」時功率／燃油隨航速三次方的簡化，不是 clean-baseline XGBoost 直接預測的最新 excess fuel。
2. Speed Loss 超過 35% 時成本公式仍以 35% 計，避免發散；因此極端船被封頂。
3. `fleet.csv` 已把成本以「重建 artifacts 當時」的油價算死；行情 API 的 `effective_price` 不會自動重算首頁成本。
4. API 計算月燃油時，會用「執行當下」的 `HW_FUEL_PRICE` 去除已存的月成本。若部署後只改環境變數、沒有重建 artifacts，成本與燃油／碳排可能使用不同油價而互相不一致。
5. S21、S22、S9 沒有足夠可見的 post-clean 基準，目前 `f_ref` 來自「吃水相近、FOC/V³ 最低的最佳效率日」偽基準；詳見 private artifact `data/artifacts/clean_refs.csv` 與 [`features.py`](../app/pipeline/features.py#L53)。

## 4. 每月超額碳排：精確公式、單位與限制

目前 API 先用月成本除以 VLSFO 情境油價，還原月超額燃油，再乘單一係數：

\[
MonthlyExcessFuel=MonthlyExcessCost/P
\quad [\mathrm{t\ fuel/30\ days}]
\]

\[
MonthlyExcessCO_2=MonthlyExcessFuel\times 3.114
\quad [\mathrm{tCO_2/30\ days}]
\]

若成本與除數使用的是同一個 $P$，它等價於：

\[
30\times\sum_i ExcessFuel_i\times 3.114
\]

目前工作區數字為 **7,197.5 t-fuel/30 days** 與 **22,413.0 tCO₂/30 days**。

3.114 的單位是 **tCO₂ / t fuel**。IMO 的 GreenVoyage2050 官方工具也明確說明，它在該工具中假設船用 HFO，採 3,114 kg CO₂/tonne fuel，且結果應視為 indicative 而非精確值。[IMO GreenVoyage2050 Fleet and CO₂ Calculator](https://greenvoyage2050.imo.org/fleet-and-co2-calculator/)

因此 Dashboard 的正確簡報說法是「以 IMO HFO 3.114 係數估算的 tank-to-wake CO₂ 情境」，而不是：

- 實際 MRV／DCS 碳排；
- CII（CII 還需要運輸工作量等分母）；
- well-to-wake GHG；
- 對 HSHFO、VLSFO、LSMGO、ULSFO、BIO_HSFO 都精確相同的燃料別排放量。

## 5. 船殼／螺旋槳 Speed Loss 歸因現在怎麼算

### 5.1 先估全船隊共同的螺旋槳比例

1. 對每個事件，取事件前 60 天與後 60 天的 `speed_loss_smooth` 中位數；兩側至少各 5 筆。
2. 定義 `delta_pp = post_median - pre_median`；負值代表改善。
3. 船殼樣本取 `UWC`、`UWC+PP`、canonical `cleaning`；螺旋槳樣本只取純 `PP`、canonical `propeller_polish`。
4. $UWCdrop=\max(-median(\Delta_{UWC}),0.1)$。
5. $PPdrop=\max(-median(\Delta_{PP}),0)$。
6. $prop\_share=clip(PPdrop/(PPdrop+UWCdrop),0,0.6)$。

程式見 [`run.py`](../app/pipeline/run.py#L25) 與 [`estimate_prop_share`](../app/pipeline/run.py#L59)。目前 artifacts 有 12 個 UWC 類效果樣本，中位改善 2.03 pp；11 個純 PP 樣本，中位改善 1.08 pp，因此：

- **螺旋槳比例 = 1.08 / (1.08 + 2.03) = 34.7%**；
- **船殼比例 = 65.3%**；
- `summary.json` 實際儲存 `prop_share: 0.347`。

### 5.2 再把每一艘船的當前 Speed Loss 依同一比例切開

\[
PropellerSL_i=CurrentSL_i\times0.347
\]

\[
HullSL_i=CurrentSL_i\times0.653
\]

成本頁也用相同比例拆 `excess_cost_per_day`。這裡沒有再跑逐船模型。

### 5.3 真實性判斷

這是 **事件效果估計形成的 fleet-level proxy**，不是固定寫死的 65:35，也不是船殼／螺旋槳的真實標籤模型；若重建 artifacts，比例會隨事件資料改變。它比完全固定比例有資料依據，但仍有下列重大限制：

- 所有船、所有日期共用同一比例，忽略船型、塗層、航線與個別檢查狀態。
- `UWC+PP` 的全部改善目前歸到 UWC 樣本；`UWI+PP` 不進純 PP 樣本，分類會偏。
- 事件前後窗口可能包含航速、季節、航線、吃水、量測與其他維護變化，沒有 causal control。
- 事件效果表中有不少「事件後變差」的正 delta，顯示噪音與混雜很大；目前沒有不確定度或最低可信樣本門檻。
- UWI 不開重置窗口，這一點正確反映「純檢查無物理介入」；但歸因比例也沒有把 UWI 當正式 placebo/control 納入估計。

簡報安全說法：**「依全船隊清洗與純拋光事件前後 Speed Loss 的中位改善比例，估計目前約 65% 船殼、35% 螺槳；這是事件型 proxy，若取得軸功率、扭矩與更密集量測即可升級成逐船歸因。」**

## 6. 現有模型完整清單

### 6.1 正式交付模型

| 模型 | 用途 | 訓練資料／target | 主要特徵 | artifact | 驗證與現況 |
|---|---|---|---|---|---|
| Fuel 102 XGBoost ensemble | 預測 S21–S23 共 102 個被遮蔽主機全速燃料消耗 | 主辦真實資料的可見良好天氣／全速列；target 是 VLSFO 熱值當量、24h 正規化 `daily_foc` | STW、RPM、prop RPM、slip、洋流 proxy、吃水、排水量、貨量、風浪水溫水深、距清洗／拋光／塢修日、全速時數、船型、RPM³、STW³、ship one-hot；C 組另加前後可見 anchor | private `data/sagemaker-p0/fuel102-ensemble/`；10 個 XGBoost JSON + 1 個低 STW fallback | 2 種資訊集 × 5 seeds，輸出取 median；maintenance-window 模擬遮蔽的 final ensemble MAPE 4.011%，但只有 41 個可驗證列，**不是主辦 102 hidden truth 的官方分數** |
| Low-STW anomaly fallback | 102 格中 STW ≤5 kn 的異常／漂航列 | 同型可見異常列；target 同上 | `me_rpm`, `rpm3` | `anomaly-fallback.json` | 專用 XGBoost；manifest 沒有獨立 held-out 指標 |
| Clean-baseline XGBoost | 預測乾淨狀態相對油耗，再導出 expected FOC、excess FOC、Speed Loss | 主辦真實資料中 post-clean 45 日基準；不足船用 best-decile 偽基準；target `f_rel=daily_foc/f_ref` | `v_rel`, `wind`, `draft_rel`；對 `v_rel` 單調遞增約束 | private `data/artifacts/baseline_model.json`、`data/sagemaker-p0/speed-loss-baseline/` | 真實資料沒有 `truth.csv`，目前 `data/artifacts/summary.json` 的 `validation` 是空物件；所以沒有可引用的真實 Speed Loss MAE／相關係數 |

102 ensemble 的 manifest 明列 10 個成員、median aggregation 與 4.011% 模擬遮蔽 MAPE，見 private `data/sagemaker-p0/fuel102-ensemble/manifest.json`。最終 102 列提交檔位於 private `data/submission/predictions_final.csv`。這套油耗 ensemble **目前沒有接到 Dashboard API 的模型下拉選單**；Dashboard 下拉選的是 Speed Loss 趨勢模型，不是 102 油耗提交模型。

### 6.2 Dashboard 內建「趨勢模型」實際上是什麼

| ID | 本質 | 訓練／公式 | 是否可稱 ML 模型 |
|---|---|---|---|
| `linear-growth` | 統計外推 | 對最近最多 120 日的平滑 Speed Loss 做一階 `polyfit`，負斜率改為 0；16 週線性外推 | 否，屬規則式線性趨勢 |
| `physics-scenario` | 啟發式情境線 | `linear-growth` 的增量再乘情境航速／參考航速 | 否，不宜直接稱物理模型 |
| `persistence` | baseline | 全期維持當前值，帶寬 ±0.35 pp | 否，屬比較基準 |
| 使用者上傳 XGBoost | 可選的未來模型 | 特徵契約為 week、current SL、growth、scenario/reference speed；以歷史每 7 日對照做 MAE gate | 目前 registry `models: []`，沒有任何已上傳模型；不是現況能力 |

目前 private `data/artifacts/model-packages/registry.json` 的 active model 是 `physics-scenario`。風險與 ROI 的「主模型」只會改變第一週斜率；其餘立方律、成本、清洗後 0.5% 與事件比例仍是規則。

### 6.3 曾做過但不是 production artifact 的實驗模型

本機 `results_local/report.json` 與 `results_ec2/report.json` 記錄了 102 格任務的候選比較：

- `k × RPM³` physics baseline；
- Ridge regression；
- Random Forest；
- XGBoost；
- LightGBM；
- pooled one-hot 與 W1/W2 分組；
- A 同日特徵、B 前 anchor、C 前後 anchor 資訊集。

它們是選模實驗，不應在簡報列成「線上同時運行的多模型」。production handoff 只有 XGBoost ensemble 與 clean-baseline XGBoost。

### 6.4 不是模型的項目

- 風險狀態：5%／10%／60 日門檻規則。
- 髒污分級：肘點／分位數規則。
- ROI：立方律 + 線性成長 + 成本掃描。
- 船殼／螺旋槳比例：事件前後中位數 proxy。
- 警報：狀態門檻規則。
- AI 顧問 scripted mode：意圖關鍵字 + 固定模板；目前 10 題 demo API 測試可防止答非所問，但它仍不是 LLM。

## 7. 與命題兩大產出的逐條比對

命題來源為工作區 `docs/航運物流組 - 陽明海運.md`、private data repo 的 `data/yangming-aws-summit-hackathon/README.md`，以及本 repo 的 [`competition-plan.md`](competition-plan.md)。狀態定義：**Fully**＝已有可執行產出；**Partial**＝有功能但方法或驗證不足；**Missing**＝目前沒有可執行流程。

### 產出一：油耗預測模型

| 命題要求 | 狀態 | 證據與缺口 |
|---|---|---|
| 預測被遮蔽的主機全速燃料消耗 | **Fully（產出已完成）** | 102 列提交檔已產生，燃料種類與小時數已換算回提交格式 |
| 使用只在遮蔽區仍可見的特徵推論 | **Fully** | 102 模型排除 HORSE_POWER、LOAD、SFOC 等 H 類隱藏欄；使用 A 類與事件衍生特徵 |
| 從歷史學到效能隨時間變化及養護後恢復 | **Partial** | 模型含距清洗／拋光／塢修天數與 anchor；但沒有顯式、可檢查的事件反應曲線或逐事件效應模型 |
| 理解各類養護／作業事件的影響 | **Partial** | UWC/DD reset、PP partial、UWI 不 reset，能避免把純檢查當清洗；但 `UWI+PP` 被拆為 polish+inspection，模型沒有事件類別 causal effect，仍可能只學到相關性 |
| 同一套模型反事實推論 UWC 或 PP 節能效益 | **Missing** | 沒有 endpoint／函式把同一筆條件分別設成 no-action、UWC、PP 後送入 production 102 ensemble 比較。現在 ROI 使用另一套 clean-baseline + 立方律 + fleet prop-share 規則，不能說是「同一套油耗模型的反事實」 |
| 油耗正確性驗證 | **Partial** | 模擬 maintenance-window final ensemble MAPE 4.011%，樣本 41 列；主辦 102 格真值未知，尚無官方分數 |

### 產出二：Speed Loss Dashboard

| 命題要求 | 狀態 | 證據與缺口 |
|---|---|---|
| 顯示 Speed Loss 隨時間趨勢 | **Fully（產品功能）／Partial（量測嚴謹度）** | 五年時序、平滑、事件、16 週外推皆可互動；但 Speed Loss 是 clean-fuel curve inversion，不是完整 ISO 19030 default measurement pipeline，且真資料無 Speed Loss ground truth |
| 顯示船體髒污造成多少 Speed Loss，並分船殼 vs 螺旋槳 | **Partial** | 已顯示兩段比例；但目前是 fleet-wide 65.3/34.7 event-effect proxy，非逐船模型、無不確定度、非因果歸因 |
| Speed Loss 與水下清潔事件比對 | **Fully（呈現）／Partial（因果判讀）** | 趨勢圖、甘特圖與 60 日事件前後表已對齊；但前後差只是 observational comparison |
| 辨識純 UWI 不應帶來改善 | **Fully（事件語意）／Partial（統計驗證）** | UWI 映射為 inspection，不開 clean/polish reset，因此不會被程式當清洗；但尚未把 UWI placebo 效果正式納入模型驗證報告 |

### 整體判斷

產品已覆蓋命題的主要 demo 動線，但 **還不能說完全 hit**。最誠實的定位是：

- **油耗客觀產出：已交付 102 格預測；正確性需等官方評分。**
- **Dashboard 專家產出：趨勢與事件對照完整，歸因為可解釋 proxy。**
- **主要技術缺口：production 102 ensemble 的 UWC／PP 反事實推論尚未實作；Speed Loss 無真值驗證；hull/prop attribution 尚非逐船 causal model。**

## 8. 簡報可直接採用的安全說法

可以說：

- 「使用主辦提供的 15 艘匿名真實航行與養護資料；Dashboard 顯示的是模型推導的效能指標，不是即時感測值。」
- 「102 格油耗採 10 個 XGBoost 成員的中位 ensemble；維護窗口模擬驗證 MAPE 4.011%，這不是 hidden test 官方成績。」
- 「Speed Loss 採同船相對乾淨基準與單調油耗曲線反演，方法與 ISO 19030 的同船歷時比較精神對齊。」
- 「船殼／螺旋槳目前依事件前後中位改善估為 65.3%／34.7%，屬 fleet-level proxy。」
- 「超額成本與碳排是 30 日情境估算，可由油價與成本假設調整，不是財務或法遵報表。」

暫時不要說：

- 「完全符合 ISO 19030」或「ISO 認證 Speed Loss」。
- 「AI 已精準判斷每艘船 65% 是船殼、35% 是螺槳」。
- 「每月實際損失 US$4.32M／實際碳排 22,413 噸」。
- 「同一套模型已模擬 UWC 與 PP 的反事實節能」；目前尚未成立。
- 「Dashboard 同時比較 RF、LightGBM、XGBoost 多個正式模型」；RF／LightGBM 是離線選模實驗。
- 「AI 顧問就是 Bedrock」；除非 `/api/health` 與回答 `mode` 都證明是 agent mode。

## 9. 簡報前最小驗真清單

1. 記錄私有 data repo 的 tag／commit 與 `MANIFEST.sha256`。
2. 留存部署啟動 log，確認沒有出現 `--synth`；核對 `/api/fleet` 為 S1–S23。
3. 截錄 `/api/health` 的 `llm_provider`、`retriever`、`advisor_mode`。
4. 截錄 `/api/fuel-prices` 的 `market_status`、`source`、`as_of`、`estimated`，不要只截價格。
5. 在簡報註腳固定列出：油價、30 日、3.114、清洗後 0.5% SL、清洗成本與 artifact 版本。
6. 將 4.011% 明確標為「maintenance-window simulated validation」，不要寫成 competition test MAPE。
7. 若來不及補反事實模型，將 ROI 定位為「clean-baseline + physics-informed scenario engine」，不要冒充 102 ensemble 的 counterfactual。
