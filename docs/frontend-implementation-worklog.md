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
| 4. 工具功能 | 部分完成 | AI 顧問、水下判讀、警報抽屜、五油種 ticker 可操作；SES/Discord 實際發送待做 |
| 5. a11y 與部署 | 部分完成 | 鍵盤、focus、表格 fallback、reduced motion、FastAPI mount 與 Docker build stage 完成；瀏覽器與 Docker daemon 待複驗 |
| 6. Review 與提交 | 進行中 | Python tests、前端 build/lint、雙軸 review 均通過並 commit |

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
- SES email／Discord webhook 實際發送與現場端到端驗證。
- 新的正式未來預測模型 artifact 尚未提供；目前公開的是誠實標示的線性結垢、物理情境與 persistence。
- 使用者可自訂 KPI 顯示／隱藏尚未實作。

## 待使用者確認

集中於本輪結束時列出；不阻擋可逆、規格內的實作。
