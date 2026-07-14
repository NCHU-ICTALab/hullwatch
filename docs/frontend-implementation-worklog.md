# HullWatch 前端重寫實作紀錄

> 開始：2026-07-14。需求來源：`frontend-redesign-spec.md`；視覺基準：
> `../design-variants/variant-b-bridge-ops.html`（B・艦橋儀控）。

## 交付原則

- 先完成可展示的垂直動線：警報 → Fleet → Diagnose → Decide → AI 顧問。
- 新 React 前端與舊 `frontend/index.html` 並存，完成整合驗證後才切換 FastAPI 靜態入口。
- 儀表板、AI 顧問與 ROI 共用 `FleetService` 數字，不在前端重複商務公式。
- API 與使用者流程是測試 seam；不測私有函式與 React 內部狀態。
- 不修改或提交競賽答案、真資料、AWS 憑證。

## 階段

| 階段 | 狀態 | 驗收條件 |
| --- | --- | --- |
| 0. 現況盤點與鷹架修復 | 完成 | React production build 通過，TypeScript 6 過時設定已移除 |
| 1. 新 API 公開契約 | 完成 | models、forecast、schedule、fuel-prices、log、noon-report、alerts 測試通過 |
| 2. Bridge Ops 殼層 | 完成 | Fleet／Diagnose／Decide 三段導航、主題與響應式版面完成 |
| 3. 真資料主動線 | 完成 | 15 船真 artifact 接線，loading／error／empty state 完整 |
| 4. 工具功能 | 完成 | AI 顧問、水下判讀、警報抽屜、五油種 ticker、依船舶訂閱與 SES/Discord 明確發送 |
| 5. a11y 與部署 | 部分完成 | 鍵盤、focus、表格 fallback、reduced motion、FastAPI mount 與 Docker build stage 完成；瀏覽器與 Docker daemon 待複驗 |
| 6. Review 與提交 | 完成 | Python 61 tests、前端 build/lint、雙軸 review 均通過並 commit |

## 已確認決策

- IA：① 總覽 Fleet → ② 診斷 Diagnose → ③ 決策 Decide。
- 視覺：儀器白、深青、琥珀、危險紅；2px 實邊、無陰影堆疊。
- 排程為唯讀系統建議，動作集合 `{PP, UWC, UWC+PP}`；DD 只顯示既定事件。
- 主模型驅動下游；比較模型只影響圖表。
- 歷史 Speed Loss 不受情境船速影響。
- 所有數值情境參數使用滑桿＋數字輸入雙控件。

## 實作中發現

- `webapp/` 已有 Vite／React／Tailwind v4／shadcn 起始設定，但 `App.tsx` 仍為預設頁。
- FastAPI 目前只有 fleet、ship、roi、advisor、inspect；規格要求的新 API 尚未實作。
- `fleet.csv`、`scored.csv` 與 `events.csv` 已足以衍生第一版排程、日誌與警報。
- 即時油價先以具來源與時間戳的安全 fallback 契約落地，再接低頻外部 fetch/cache。

## 2026-07-14 實作結果

- 新 API：`GET /api/models`、`GET /api/ship/{id}/forecast`、`GET /api/schedule`、
  `GET /api/fuel-prices`、`GET /api/ship/{id}/log`、`POST /api/noon-report`、
  `GET /api/alerts`、`POST /api/alerts/{id}/read`。
- 日報上傳採 server-process 內增量評分：載入 monotone XGBoost 並以曲線反演計算 Speed Loss，
  更新 current KPI、30 日誌與後續 API 查詢；不覆寫原始 artifact。UWI 不重置 clean baseline。
- React：B 版三段 IA、狀態篩選、SL 雙控件、單船 KPI、ECharts 多模型趨勢、
  歸因、延遲代價、30 日誌、日報上傳、甘特排程、ROI 曲線、五油種卡／ticker、
  AI 顧問、水下判讀、警報抽屜、深色主題。
- 可及性：skip link、可見 focus、狀態形狀＋文字、圖表資料表 fallback、aria-live、
  reduced-motion、鍵盤可操作原生控件。
- 部署：FastAPI 優先 serve `webapp/dist`，無 build 時回退舊前端；Docker 新增 Node build stage。
- 驗證：`pytest` 51 passed；`npm run build`、`npm run lint` 通過；HTTP smoke root/assets/API 全 200。
- ECharts 採 core tree-shaking，主 bundle gzip 由約 442KB 降至約 251KB。
- in-app Browser 當下沒有可用 backend，尚未完成真實點擊／截圖與 console 驗收。
- Dockerfile 已有 Node build stage，`launch_demo_ec2.py` 原本的 `docker build` 會自動包含 React；
  本機 Docker Desktop daemon 未啟動，因此尚未完成映像 build。
- 雙軸 review：3 個硬違規（模型誠實標示、日報正式評分路徑、前端重複商務規則）均已修正；
  剩餘技術債是 `FleetService` 與 `App.tsx` 過大，待競賽關鍵路徑穩定後拆模組。

## 尚未完成的高風險 must

- 真燃油資料鏈：Ship & Bunker 低頻快取、USDA 歷史、Yahoo fallback，以及真實船別五油種配比。
- SES email／Discord 發送程式已完成；尚待真憑證現場端到端驗證與排程觸發。
- 新的正式未來預測模型 artifact 尚未提供；目前公開的是誠實標示的線性結垢、物理情境與 persistence。
- 使用者可自訂 KPI 顯示／隱藏尚未實作。

## 2026-07-15 第二批：資料治理與互動改善

使用 `grilling` 逐題確認後開始實作。已鎖定的驗收決策：

- 行情每 6 小時更新；超過 24 小時標示延遲，抓取失敗沿用最後成功快取，沒有快取時不得捏造行情或歷史。
- ROI 預設基準為 Singapore VLSFO；市場行情與使用者輸入的決策情境價分開呈現。
- 油價跑馬燈移到 Fleet 首頁內容頂部，其他頁面不顯示；全站正文、表格與圖表標籤放大。
- 警報改為右側可拖曳寬度的 Sidebar；一般警報不打斷操作，新的 critical 警報首次自動展開。
- 正午日報移到「設定 → 資料匯入」，提供批次 CSV 範本、逐列驗證、部分成功與同船同日覆蓋。
- 模型管理移到設定頁。第一版接受 XGBoost JSON + manifest，模型先成為候選；同一驗證集 MAE 不得惡化超過 5%，通過後仍需手動啟用。預留 ONNX adapter，不接受 pickle/joblib。
- 本階段不實作登入與角色權限；正式部署須接上身分驗證的限制只記於技術文件。
- 甘特圖預設過去 90 天至未來 180 天，可水平查看、縮放與回到今天；預設依 ship ID 升冪，下拉可切換船名、風險、每日超額成本、Speed Loss。
- 「前往清洗決策」切換到 Decide、選中船舶、捲動至詳情、短暫高亮並移動鍵盤焦點。

| 工作項目 | 狀態 | 驗收證據 |
| --- | --- | --- |
| 真實油價 provider、快取與 stale 狀態 | 完成 | parser/API 測試；網路 smoke 取得 Singapore VLSFO 692.5 USD/mt（2026-07-13），來源日逾 24h 時正確標 stale |
| 批次日報 CSV 範本與匯入 | 完成 | template／部分成功／覆蓋測試通過 |
| 模型包註冊與設定頁 | 完成 | 真 XGBoost JSON 上傳測試；共同驗證集、啟用門檻、restore API 與分類設定 UI |
| 甘特時間操作與排序 | 完成 | 90 天歷史＋180 天未來、橫向移動、縮放、今天定位與五種排序 |
| 警報 Sidebar、決策 focus、字級 | 完成（視覺待複驗） | production build/lint 通過；in-app Browser 無可用 backend，未能點擊／截圖 |
| HANDOFF 與最終驗證 | 完成 | 61 tests、build/lint、雙軸 review；根目錄 HANDOFF 已更新 |

### 第二批實作結果

- `FuelMarketService`：Ship & Bunker Singapore 公開頁解析、USDA SODA 獨立降級、Yahoo Brent 期貨質量等值 proxy 最後保底、6 小時 JSON 快取、來源／抓取任一逾 24 小時即 stale，以及 unavailable 誠實狀態；Yahoo 值一律標 EST/proxy，不冒充港口現貨。
- 批次日報：`GET /api/noon-report/template`、`POST /api/noon-report/file`；CSV 逐列驗證、部分成功、同船同日 idempotent overwrite。
- 模型治理：manifest 範本、XGBoost JSON 安全載入、固定趨勢特徵契約、歷史共同驗證集 MAE（候選不得比現行惡化逾 5%）、輸出 finite/range 檢查、手動 activate/restore；ONNX 只預留 adapter 邊界，尚未啟用。
- 設定介面分為資料匯入、模型管理、資料來源、電子報訂閱與介面。登入／角色權限尚未實作；正式部署必須在模型與資料寫入 API 前接身分驗證與授權。
- 油價跑馬燈移到 Fleet 標題下方，只由明確按鈕暫停（系統 reduced-motion 時靜止）；警報 Sidebar 可滑鼠拖曳或鍵盤方向鍵縮放；甘特圖可縮放、前後捲動、回到今天並按 ID／名稱／風險／成本／SL 排序。
- 未使用 Playwright 或其他瀏覽器替代工具：依 in-app Browser 技能規範，當 backend 列表為空時只記錄待複驗。
- 最終驗證：`pytest -q` 61 passed（另有 1 則既有 Starlette/httpx deprecation warning）；`npm run build`、`npm run lint` 通過。Vite 仍提示主 bundle 大於 500kB（gzip 約 256kB），列為後續 code splitting 技術債。
- 雙軸 review：4 項硬問題（active model 未驅動下游、候選模型可覆寫、上傳無 bounded read、壞快取可能 500）與後續 manifest 型別 500 均已修正；本批規格缺口複核後補上 Yahoo proxy、模型變更刷新 schedule、critical 再發判定。剩餘 judgement call 是 `App.tsx` 約 680 行的 Divergent Change，競賽穩定後拆 page/feature。

## 待使用者確認

集中於本輪結束時列出；不阻擋可逆、規格內的實作。

## 2026-07-15 第三批：互動修復與通知訂閱

- ticker：移除 focus／hover 隱性暫停；reduced-motion 保持靜止但不再產生橫向捲軸，使用者仍可用按鈕暫停。
- 甘特：同時間附近的維護事件以 greedy lane 分層，軌道高度隨 lane 數增加；選船同步更新上層 ship id，ROI 與下方資訊會重新查詢。
- 燃油趨勢：API 新增 `history_by_grade`，畫面可切換 VLSFO、LSMGO、HSHFO、ULSFO、BIO_HSFO；估算／proxy 序列在來源欄明確標示。
- ROI：保留後端 180 天逐日 What-if 計算，主圖改畫「相較永不清洗的平均每日淨節省」，避免總成本尺度把曲率壓扁；表格同時保留原始成本。
- Speed Loss 圖：黃色菱形直接標註 PP／UWC／UWI，並新增事件說明與可展開清單。
- 日報範本：改為明確 fetch/blob 下載，提供螢幕閱讀器可讀的成功／錯誤狀態。
- 通知：新增 JSON-backed 多筆訂閱、Email 遮罩、系統單一 Discord webhook、每筆船舶選擇、SES／Discord 實際摘要發送與測試替身。部署與排程待辦見 `notification-delivery.md`。
- a11y：原生 fieldset/legend、email 型別、checkbox label、aria-live、鍵盤可操作按鈕與圖表資料表 fallback。
- 驗證：Python **68 passed**；前端 Vitest **3 passed**；`npm run lint` 與 production build 通過。Vite 主 bundle約 788KB（gzip 259KB），code-splitting warning 仍在。
- 雙軸審查修正：移除直接比對原始碼字串的測試，改測 exported dashboard behavior；DD 一併分 lane、甘特 fallback 補維護／乾塢表格、油價 footer 跟隨所選油種、ROI 使用推薦動作成本與 SL 回復幅度（不再把 PP/UWC 都當完整清洗）、切船期間清空舊 ROI；sparkline 補完整隱藏資料表；通知 store 補結構驗證與寫入鎖。複審無 hard violation。
- 視覺 QA：in-app Browser backend 列表仍為空；未以其他瀏覽器工具繞過，保留人工點擊與截圖待辦。
