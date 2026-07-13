# WORKLOG — 賽前走路骨架建置

> 進度追蹤文件。計畫本體見 [../docs/prep-plan.md](../docs/prep-plan.md)。

## 2026-07-13

### 已完成

- ✅ **Bedrock 可用性實測（Learner Lab）**：結論=**賽前不可用**。
  IAM 白名單只放行已 EOL 下架的舊模型（claude-3-5-sonnet 2024 系列等），
  現役模型全 AccessDenied → ADR-0002 fallback 生效（stub 開發、當天切 bedrock）。
- ✅ 專案骨架 + venv + git init
- ✅ **schema 模組**（[app/schema.py](app/schema.py)）：欄位對應單點修改、DailyFOC 公式、品質篩選
- ✅ **合成正午報表產生器**：立方定律 + 結垢生長（季節/隨機游走）+ 清洗/拋光/檢查事件 + ground truth
- ✅ **事件對齊**：days_since_clean / baseline 窗口（= 船舶版刀次重置）
- ✅ **相對化特徵**：v_rel / f_rel / draft_rel（= 論文 Robust Scaling，LOSO 泛化的前提）
- ✅ **乾淨基準模型**：monotone XGBoost + 向量化二分反演（ADR-0001）
- ✅ **驗證**：LOSO 每船 MAE < 1.5pp、corr 0.83–0.98；時間分塊 corr 0.91–0.94
  - 方法論發現：基準期殘留髒污 s̄_b 使估計為「相對自身基準」值（ISO 19030 固有語意），
    已寫入 CONTEXT.md 與驗證指標（mae_pp 對相對真值、mae_abs_pp 供參考）
- ✅ **肘點法分級**：加膝點強度檢查，平滑分佈自動退回分位數（合成資料實測觸發過）
- ✅ **ROI 引擎**：立方定律超額成本、結垢率外推、180 天成本掃描、最佳清洗日、回本天數、CO₂
- ✅ **FastAPI 單體**：/api/health /fleet /ship/{id} /roi /advisor /inspect + serve 前端
- ✅ **前端**：prototype 改資料驅動（LIVE fetch ↔ MOCK 快照同形狀），Playwright 四頁驗證零 JS 錯誤
- ✅ **AI 顧問**：LangGraph agent（Bedrock，當天啟用）+ scripted fallback（已驗證，數字與儀表板同源）
- ✅ **檢索層**：本地 TF-IDF（可跑）↔ Bedrock KB（寫好未測，Learner Lab 不支援）
- ✅ **水下判讀**：Bedrock 多模態（當天啟用）+ stub（已驗證，與資料面交叉驗證）
- ✅ **Optuna**：跨船穩定目標（Mean+0.5·Std per-ship RMSE、時間尾段驗證），3-trial 煙霧測試過
- ✅ kb/ 種子語料（ISO 19030、清洗經濟學、命題摘要）
- ✅ 測試：**35 passed**
- ✅ Dockerfile + start.sh + EC2 部署手冊 + README（含當天 runbook）

- ✅ **兩軸 code review（Standards + Spec 平行子代理）＋修復**：
  - 修：advisor/inspect 前端 XSS（innerHTML 未跳脫，agent 模式下可被 prompt injection 利用）→ esc() 全面套用，Playwright 注入測試確認不觸發
  - 修：空序列 ship_detail/圖表 crash 防護；上傳 8MB 上限
  - 修：預測帶從「憑空常數」改為以該船近 12 週實際波動為底的啟發式（並標註非統計信賴區間）
  - 修：run/tuning 重複的資料準備段 → `prepare_features()` 共用；events.py searchsorted 重複段 → `_days_since_last()`
  - 補：`app/llm/rag_eval.py`（檢索評估黃金集，hit@k/MRR，本地與 Bedrock KB 通用）— 補齊 ADR-0002 承諾
  - 補：`run.py --loso` 旗標把 LOSO 寫進 summary.json
  - ADR-0002 措辭修正：HITL/Path Jail 不搬（工具集全唯讀，Q8 決策）
  - 記錄不修：mock 資料層與後端的經濟公式重複（MOCK 模式本來就是獨立快照）；CO₂ 統計屬合理加值（呼應命題 CII）
- ✅ 測試：**37 passed**

- ✅ Docker 映像重建 + 容器煙霧測試（啟動自跑管線 → 30 秒內服務）
- ✅ 推上 GitHub：`NCHU-ICTALab/hullwatch`（從個人帳號轉移到組織後改 public）
- ✅ **EC2 部署演練成功**（Learner Lab us-east-1，`scripts/launch_demo_ec2.py` 一鍵：
  安全群組 8000 + AL2023 + user-data 自動 clone/build/run）。
  公網完整驗證：7 條檢查全 PASS + Playwright 零 JS 錯誤。
  - 💡 **踩坑記錄（當天別再犯）**：repo 為 private 時 EC2 匿名 clone 會靜默失敗，
    症狀是 console 顯示 `open Dockerfile: no such file or directory`。
    先 `curl -s -o /dev/null -w "%{http_code}" <repo url>` 確認 200 再開機器。
  - Learner Lab session 結束機器會停止、重啟後公網 IP 會變；demo 前需重跑
    `--status` 拿新 IP，或當天在比賽環境直接 `launch_demo_ec2.py`。

### 賽前加值（7/13 深夜）

- ✅ **油耗歸因瀑布**（評審問題 2 的視覺答案）：XGBoost 內建 TreeSHAP（pred_contribs，
  已對齊 early-stopping 的 best_iteration）→ 實測油耗 = 乾淨基準 + 航速 + 天候 + 吃水 + 髒污殘差，
  單船頁新卡片，近 7 日平均。物理亮點：髒污船跑得慢所以航速項為負，髒污殘差把它吃回去。
- ✅ **水下報告 PDF 解析骨架**（`app/pipeline/report_parser.py`）：pypdf 文字抽取 +
  中英日期/事件關鍵字辨識 → events.csv；當天只需增補 EVENT_KEYWORDS。解析結果設計為必經人工過目。
- ✅ **UX 修整**：顧問對話歷史保留（附加+捲動）、趨勢圖 hover 顯示日期/數值、
  單船頁返回鈕、ROI 滑桿標籤斷行修正。
- ✅ 測試 **42 passed**；Playwright 重驗零 JS 錯誤。
- ⚠️ EC2 演練機上的是舊版程式碼；如要展示新功能需重跑 `--teardown` + `launch_demo_ec2.py`。

### 比賽當天才能做（依賴當天資源）

- 真資料接入：改 `schema.COLUMN_ALIASES` → 重跑管線 → Optuna
- Bedrock：`HW_LLM_PROVIDER=bedrock` + 環境給的模型 ID；agent 模式首測
- Bedrock KB 建立與 `HW_RETRIEVER=bedrock_kb` 切換
- 水下報告 PDF 解析器（下游 events.csv schema 已固定）
- TabDDPM 擴增（P2：先看真資料類別分佈）

### 交件清單（7/16 14:30 前，對照命題「提案繳交內容」）

1. 團隊基本資料 —（隊友/簡報）
2. 提案大綱 —（隊友/簡報）
3. GitHub 網站連結 — ✅ <https://github.com/NCHU-ICTALab/hullwatch>
4. 完整提案簡報 —（隊友；架構圖 `docs/architecture.svg` 已備）
5. Live Demo 網址 — 當天以 `scripts/launch_demo_ec2.py` 部署後取得
6. Demo 錄製影片 — 7/15 錄（live demo 掛掉時的保險）
7. **預測完的結果檔案** — ✅ 輸出器已備：`python -m app.pipeline.export`
   → `預測結果_HullWatch.xlsx`（每船摘要與清洗建議 / 每日預測明細 / 方法與驗證）

### 需要使用者確認（集中）

- 見對話末尾回報
