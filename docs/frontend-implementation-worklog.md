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
| 4. 工具功能 | 完成 | AI 顧問右側 push panel、水下判讀、頂部警報通知匣、五油種 ticker、依船舶訂閱與 SES/Discord 明確發送 |
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

## 2026-07-15 第四批：維護標記防碰撞與 AI 顧問側欄

- Speed Loss 維護事件改用與甘特圖一致的 greedy lane 概念：同日或 14 天內的 PP／UWC／UWI 會分配到不同高度，圖上只顯示縮寫，完整日期、事件類型與附註放在圖下方可展開清單。
- 「查看維護事件說明」清單改為自適應三欄；窄螢幕將附註換行到下一列，長字串允許斷行，避免日期、縮寫與說明互相覆蓋。
- AI 顧問由 modal 移至常駐右側 panel。桌面開啟時主內容保留並向左縮進 440px（push，不覆蓋）；900px 以下改為完整顧問畫面，可由關閉鍵返回原頁。
- AI 顧問新增當次瀏覽對話紀錄、目前船舶情境、`Ctrl/Cmd + I` 開關與 `Esc` 關閉；開啟後焦點移至輸入欄，關閉後回到觸發按鈕。
- 警報不再占用右側欄，改為頂部未讀徽章通知匣。critical 新警報仍可自動展開，點擊警報仍會標為已讀並前往對應船舶；Email／Discord 船舶訂閱維持在設定頁。
- a11y：面板與通知匣均有 `aria-expanded`／`aria-controls`、命名區域、可見焦點、Esc 行為、關閉後焦點復原；窄螢幕不以 overlay 遮蓋主內容。
- 驗證：Vitest **4 passed**；`npm run lint` 與 production build 通過。Vite 主 bundle code-splitting warning 仍在。
- 視覺 QA：再次依 in-app Browser 流程檢查，但可用 backend 列表為空；未使用其他瀏覽器工具繞過。需由使用者本機確認實際圖表標記間距、桌面 push 與 900px 以下切頁效果。

## 2026-07-15 第五批：P0 模型交接與主模型切換修復

- 「決策主模型」下拉選單原本用 `is_primary` 同時判斷選項與 disabled；registry 契約恰好只有一個 primary，導致選單永遠停用且只有一個 option。修正為列出 active／available／validated 模型，排除 candidate／rejected。
- 主模型切換不再只是 React 本地狀態；現在會呼叫 activate API、持久化唯一 active model、刷新模型與 schedule。API 失敗時回復原選擇並顯示錯誤。
- 內建 `linear-growth`、`physics-scenario`、`persistence` 均可被設為 active；已驗證的上傳模型仍沿用原驗證門檻。
- 新增 `scripts/export_p0_models.py`：匯出 `speed-loss-baseline` 與 `fuel102-ensemble`，產生 manifest、特徵順序、sample、SHA-256 與 tar.gz，並逐一載入驗證所有 XGBoost JSON。
- 真資料正式匯出結果：baseline 1 個模型；fuel ensemble 10 個成員＋1 個低速 fallback；壓縮交接包約 3.2MB，位於 gitignored `data/sagemaker-p0.tar.gz`。
- P0 模型目標與 SageMaker 推論契約記錄於 `docs/p0-sagemaker-handoff.md`。
- 最終驗證：Python **70 passed**；前端 Vitest **5 passed**；`npm run lint` 與 production build 通過。既有 Starlette/httpx deprecation 與 Vite bundle code-splitting warning 仍在。

## 2026-07-15 第六批：Fleet 篩選同步與上傳控制修復

- 點選 Fleet 狀態篩選時，Speed Loss 下限同步到該狀態船舶的最低值，向下對齊 0.5% 滑桿刻度；「全部」回到 0%。計算以完整船隊為準，避免篩選回饋導致資料消失。
- 正午日報、模型 JSON 與水下照片三個入口統一使用共用 UploadZone。原生 file input 保留鍵盤與螢幕閱讀器語意，但改為覆蓋式控制，不再以 intrinsic width 撐開 grid。
- 上傳區新增一致的選擇檔案按鈕視覺、目前檔名、ellipsis 截斷、`focus-within` 可見焦點，以及 grid 子項 `min-width: 0`。
- 驗證：前端 Vitest **6 passed**；`npm run lint` 與 production build 通過。in-app Browser 仍無可用 backend，實際視窗寬度下的視覺點擊待使用者本機確認。

## 2026-07-15 第七批：Private data repository 交接

- 建立 private `NCHU-ICTALab/hullwatch-data` 作為資料團隊與 AWS 團隊直接 pull／push 的共同工作 repository；不採 submodule 或 snapshot 複製。
- 原始陽明格式資料、canonical raw、Dashboard artifacts、提交結果與 P0 SageMaker 模型包均由 Git LFS 追蹤；每次更新以 `MANIFEST.sha256` 驗證大小與 SHA-256。
- `fuel-market-cache.json` 與 `notification-subscriptions.json` 保持部署本地狀態，不進資料版控；憑證與 webhook 亦明確禁止提交。
- 主程式以 `HW_DATA_DIR=../hullwatch-data/data` 直接使用共用資料，README 與 EC2 手冊同步加入 clone、LFS、驗證、Docker bind mount 與更新流程。
- Fleet 狀態同步滑桿的語意問題另行記錄：目前 0%／1% 是該狀態現有船舶最低值，不是分級門檻；真正規則為 10% 清洗門檻與 60 天 watch window，修正呈現方式待使用者確認。

## 2026-07-15 第八批：Fleet 固定營運門檻

- 營運狀態改為固定規則：正常 `<5%`；密切留意 `5%–<10%`；立即處置 `≥10%`。未達 5% 但預估 60 天內達 10% 者仍提前列為密切留意。
- 狀態按鈕同步 Speed Loss 下限為：全部 0%、立即處置 10%、密切留意 5%、狀態正常 0%，不再依目前船舶最低值產生 0%／1% 等易誤解數字。
- 預測型 watch 船舶在同步 5% 時保留顯示；使用者手動把下限提高到 5.5% 以上後才依新門檻縮小結果。
- Fleet KPI 與篩選列補上可見門檻及 60 天預測例外說明；後端政策函式與前端 dashboard behavior 均新增邊界測試。
- `/api/fleet` 回傳 action、watch 與 watch window policy，前端完全以 API 數值呈現與同步；AWS 透過環境變數覆寫時不會產生前後端門檻漂移。
- 真實 artifacts 驗證為立即處置 3 艘、密切留意 4 艘、正常 8 艘；完整驗證 Python **71 passed**、Vitest **7 passed**，lint 與 production build 通過。

## 2026-07-15 第九批：內部客服 Wiki 與三段式 RWD

- AI 顧問回答改用 `react-markdown` + `remark-gfm` 安全渲染 CommonMark／GFM；支援標題、清單、強調、連結、程式碼與表格，不啟用 raw HTML。新增 server-render 測試確認常用語法與不安全連結不會成為可執行標記。
- Fleet Speed Loss 拉桿採 140ms settled filter，停止調整後一次套用船卡結果，並以 `aria-busy` 表示更新中；結果容器會量測並保留舊高度，再平滑縮到新高度。狀態按鈕則同步立即套用正確門檻，避免狀態與延遲下限短暫不一致。全頁固定 scrollbar gutter，減少結果變少時的水平位移。
- 「最近清潔動作」依資料集字典顯示中文名稱並保留 PP／UWI／UWC／DD 代碼；長名稱使用 compact metric 排版，避免卡片截字。
- RWD 明確分為桌面 `≥1200px`、平板 `768–1199px`、手機 `≤767px`。桌面 AI 顧問維持 440px push；平板改為 320–380px push 並把剩餘 Dashboard 簡化為單／雙欄；手機改為全寬顧問畫面，不 overlap 主內容。`≤480px` 的 KPI、頁首與控制列再收斂為單欄。
- 決策頁上方五個油價卡改為可鍵盤操作的 `aria-pressed` 按鈕，點擊後與下拉選單共用 `fuelGrade`，同步切換折線圖、資料表與來源。
- 「建議詳情 · 唯讀」移到甘特控制與圖表上方，從單船診斷跳轉後更快看到高亮建議。
- private `hullwatch-data` 新增 `wiki/internal-support-v1` 初始建置分支：`llm-wiki/raw/` 不可變來源快照、`wiki/index.md` 最小載入入口、追加式 `log.md`、Schema、安全規則、客服流程與首批七頁知識。新增 provider-neutral `scripts/wiki_context.py`，可 validate 或依問題輸出附 source 的 bounded Markdown context，且每次自動先注入 Schema 安全契約；第十批已依使用者決策直接 fast-forward 到 `main`，後續 Wiki 更新不要求 PR。
- 驗證：Wiki **2 個 adapter tests**、實際 query smoke、內部連結、秘密模式與 38 個共享檔 manifest 均通過；前端 Vitest **10 passed**、lint 與 production build 通過。本機 API／Vite 均回應 200。in-app Browser backend 列表仍為空，三 viewport 的肉眼截圖與點擊保留人工複驗。

## 2026-07-15 第十批：全站維護中文化、圖表可讀性與可縮放顧問

- 維護動作統一透過資料集字典顯示官方中文名稱，不再附加 PP／UWC／UWI／DD 代碼；涵蓋診斷 KPI、延遲代價、Speed Loss 事件清單、30 日誌、建議詳情、甘特建議條、排程表與歷史維護表。
- Speed Loss 密集維護事件保留 14 天 greedy lane 分層，每層使用固定 32px 垂直位移；圖上改用 1、2、3…編號菱形且文字置中。完整日期、中文動作與附註放在可展開清單，tooltip 同步提供中文內容，避免資料尺度壓縮後事件仍互相遮擋。
- 甘特歷史事件改為無文字的菱形標記，完整中文名稱透過 title／aria-label 與下方事件資料表取得；建議動作條顯示中文並在空間不足時 ellipsis，避免長名稱覆蓋相鄰內容。
- 清洗日淨節省圖把坐標軸語意移到圖外的可讀說明區；圖內只保留含單位刻度、損益兩平線與置中的最佳點標記，grid 啟用 containLabel，避免軸名稱、說明與圖標互相重疊。
- AI 顧問右側 panel 可滑鼠／觸控拖曳調整寬度，拖曳命中區為 32px；亦可用左右方向鍵、Home／End 操作可存取 separator，雙擊回到 440px。桌機限制 360–720px、平板 300–480px、手機維持全寬畫面。
- RWD 改用 `.app-content` 的 container query，而不是只看 viewport 或堆疊 `.advisor-open` 特例；顧問調寬後，主內容依實際剩餘寬度自然切換桌機、平板與窄版布局，維持 push、不 overlap。
- 「前往清洗決策」增加上方間距，不再貼住延遲代價說明。
- private `hullwatch-data` 的 `wiki/internal-support-v1` 已 fast-forward 到 `main` 並直接 push（`18c6339 → ffb112c`）；本機 `llm-wiki/.obsidian/` 保持未追蹤，沒有上傳。
- 驗證：新增中文名稱、事件編號／32px 分層與顧問寬度邊界測試；前端 Vitest **11 passed**、oxlint 0 warnings／0 errors、TypeScript + Vite production build 通過，`git diff --check` 無空白錯誤。雙軸 review 發現並修正標記文字對比、資料值間距不等於像素間距及拖曳觸控範圍三項問題；Vite 既有 bundle size warning 仍在。
- 視覺 QA：依 in-app Browser 規範重新連線並讀取 troubleshooting，但可用 backend 仍為空；未以其他瀏覽器工具繞過。需由使用者本機確認三 viewport、側欄拖曳、ECharts tooltip 與實際字型渲染。

## 2026-07-15 第十一批：API 維護別名中文化與甘特事件名稱恢復

- 截圖回報「最近清潔動作」仍顯示 `propeller_polish`。最小回歸測試確認不是舊 build，而是前端字典只處理 PP／UWC／UWI／DD，API `last_event.type` 實際沿用 canonical `events.csv` 的 snake_case 值。
- 盤點真實 artifact 後確認 API 事件值為 `cleaning`、`drydock`、`inspection`、`propeller_polish`。中央維護模型現在先把這四種別名、原始競賽代碼，以及常見空白／連字號變體正規化成 canonical kind，再由單一 metadata 產生完整 UI 名稱與甘特短名稱；診斷 KPI、Speed Loss 清單、日誌與排程表共用同一轉換。UI 中文依使用者指定的資料集 README 字典；`CONTEXT.md` 的「水下船體清潔／進塢大修」保留為領域概念同義詞。
- 甘特歷史事件不再只顯示菱形，恢復為「船殼清洗／螺旋槳拋光／水下檢查／進塢大修」中文名稱。標籤保留 title 與完整 aria-label，視覺名稱使用 12px、可截斷的高對比文字。
- 為避免文字標籤重疊，`allocateEventLanes` 增加可選 clearance；Speed Loss 維持 14 天規則，甘特事件名稱使用 90 天 lane 重用間隔，軌道高度仍隨 lane 數自動增加。
- 驗證：新增 API alias 與甘特文字 lane 測試；前端 Vitest **13 passed**、oxlint 0 warnings／0 errors、TypeScript + Vite production build 通過。既有 bundle size warning 仍在。
