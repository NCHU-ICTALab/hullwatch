# HullWatch

HullWatch 是一套船體能效監控與維護決策系統。系統從正午日報與水下維護事件建立每艘船的乾淨基準，估算船體髒污造成的 Speed Loss、額外油耗、碳排與成本，並提供維護排程、清洗投資報酬分析及 AI 顧問。

## 主要功能

- 船隊健康總覽與 Speed Loss 風險分級
- 單船歷史趨勢、維護事件及多模型預測比較
- PP、UWC、UWC+PP 與既定乾塢事件排程
- 清洗成本、回本天數與延後維護代價分析
- 多油種市場行情與燃油價格情境分析
- 正午日報 CSV 批次匯入
- XGBoost 模型包註冊、驗證、啟用與回復
- Amazon Bedrock AI 顧問及水下影像判讀
- Email／Discord 船舶通知訂閱
- React 響應式介面、鍵盤操作與圖表資料表 fallback

## 系統架構

```text
正午日報 + 維護事件
        │
        ▼
資料正規化與良好天氣篩選
        │
        ▼
乾淨基準模型（monotone XGBoost）
        ├── 殘差 → 額外油耗、成本與 CO₂
        └── 曲線反演 → Speed Loss
        │
        ▼
風險分級、趨勢外推與 180 天維護決策
        │
        ├── FastAPI API
        ├── React／Vite Dashboard
        └── Bedrock AI 顧問與檢索
```

後端與 AI 顧問共用同一個服務層，確保儀表板和顧問回答使用相同的船隊數字。模型驗證採跨船 Leave-One-Ship-Out 與時間分塊，避免隨機切分造成時間洩漏。

## 技術組成

- Python 3.10+
- FastAPI、pandas、scikit-learn、XGBoost、Optuna
- React 19、TypeScript、Vite、ECharts、Tailwind CSS
- Amazon Bedrock、Amazon SES、Amazon S3
- Docker

## 專案結構

```text
app/
├── api/          FastAPI 路由、船隊服務、通知與模型管理
├── llm/          Bedrock 顧問、工具與檢索
├── pipeline/     資料轉換、訓練、評分、預測與匯出
├── synth/        合成資料產生器
├── config.py     環境變數與系統設定
└── schema.py     輸入欄位映射
webapp/           React／Vite 前端
tests/            Python 測試
docs/             架構與功能文件
deploy/           容器啟動與部署相關檔案
scripts/          實驗、訓練與部署工具
data/             本機資料與模型產物，不納入 Git
```

## 安裝

先進入 repository 根目錄。在 Windows Git Bash 中執行：

```bash
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt

cd webapp
npm ci
cd ..
```

Linux 或 macOS 請將虛擬環境啟用指令改為：

```bash
source .venv/bin/activate
```

## 取得共享資料與模型

正式資料、pipeline artifacts 與模型權重存放在 private
[`NCHU-ICTALab/hullwatch-data`](https://github.com/NCHU-ICTALab/hullwatch-data) repository。
將兩個 repositories clone 在同一層，並安裝 Git LFS：

```bash
cd ..
git clone https://github.com/NCHU-ICTALab/hullwatch-data.git
cd hullwatch-data
git lfs install
git lfs pull
python scripts/update_manifest.py --check
```

Windows Git Bash 使用以下環境變數，讓後端與 pipeline 直接讀寫共享資料目錄：

```bash
cd ../hullwatch
export HW_DATA_DIR="$(cd ../hullwatch-data/data && pwd -W)"
```

Linux 或 macOS 將 `pwd -W` 改為 `pwd`。`fuel-market-cache.json`、
`notification-subscriptions.json` 等執行期狀態不會在 data repo 內同步。

內部客服知識庫位於 data repo 的 `llm-wiki/`。`raw/` 保存不可變的
權威文件快照，`wiki/` 保存可由 LLM 直接載入的繁體中文知識頁；更新採
獨立 branch／Pull Request 人工審查，不應讓 LLM 直接索引原始航行資料、
模型權重、通知收件資料或任何憑證。

## 快速啟動

使用 private data repo 時，先設定資料目錄：

```bash
export HW_DATA_DIR="$(cd ../hullwatch-data/data && pwd -W)"
```

沒有共享資料存取權時，才使用 `python -m app.pipeline.run --synth` 產生本機示範資料。

開發模式需要兩個終端機。

後端：

```bash
source .venv/Scripts/activate
export HW_DATA_DIR="$(cd ../hullwatch-data/data && pwd -W)"
uvicorn app.api.main:app --reload --port 8777
```

前端：

```bash
cd webapp
npm run dev
```

瀏覽器開啟 `http://localhost:5173`。Vite 會將 `/api` 代理到 `http://127.0.0.1:8777`。

## 準備正式資料

HullWatch pipeline 使用以下 canonical 輸入：

```text
data/raw/
├── noon_reports.csv
└── events.csv
```

欄位名稱不同時，可在 [`app/schema.py`](app/schema.py) 的 `COLUMN_ALIASES` 增加映射。水下報告也可以先轉換為事件 CSV：

```bash
python -m app.pipeline.report_parser <報告檔或資料夾> \
  --out data/raw/events.csv
```

輸入檔準備完成後執行：

```bash
python -m app.pipeline.run
```

若要附帶較慢的 Leave-One-Ship-Out 驗證：

```bash
python -m app.pipeline.run --loso
```

`--synth` 僅用於本機示範與測試，不應用於正式資料處理。

## 使用陽明格式資料

專案包含 `vt_fd.csv` 與 `maintenance.csv` 的格式轉接器。資料目錄應包含：

```text
data/yangming-aws-summit-hackathon/
├── vt_fd.csv
└── maintenance.csv
```

執行轉換與 pipeline：

```bash
python -m app.pipeline.ingest_yangming data/yangming-aws-summit-hackathon
python -m app.pipeline.run
```

轉接器會先產生 `data/raw/noon_reports.csv`、`events.csv` 與 `predict_targets.csv`，再由主 pipeline 建立執行期 artifacts。

## Artifacts

Pipeline 的輸出位於 `data/artifacts/`，主要包括：

```text
data/artifacts/
├── baseline_model.json
├── clean_refs.csv
├── events.csv
├── fleet.csv
├── maintenance_effects.csv
├── scored.csv
└── summary.json
```

API 啟動時會從 `HW_DATA_DIR/artifacts` 載入這些檔案。`HW_DATA_DIR` 未設定時預設為 repository 下的 `data/`。

主程式 repository 的 `data/` 已由 `.gitignore` 排除。團隊共用的原始資料、衍生資料與模型產物提交至 private `hullwatch-data`，不要提交到 public 程式碼 repository。憑證與通知收件資料不可提交到任一 repository。

### 匯出 P0 SageMaker 交接包

在正式資料 pipeline 完成後執行：

```bash
python scripts/export_p0_models.py
```

指令會在私有的 `data/sagemaker-p0/` 產生兩個 production package，並建立
`data/sagemaker-p0.tar.gz`：

- `speed-loss-baseline`：1 個 monotone XGBoost、每船乾淨基準參照、特徵契約與範例輸入輸出。
- `fuel102-ensemble`：2 組特徵 × 5 seeds 的 10 個 XGBoost、1 個低速異常 fallback、median 聚合契約與 LCV 換算。

匯出器會驗證每個 SHA-256 並實際載入所有 XGBoost JSON。輸出目錄已存在時會停止，避免覆蓋既有交接包；可用 `--out` 指定新的私有路徑。詳細契約見 [`docs/p0-sagemaker-handoff.md`](docs/p0-sagemaker-handoff.md)。

## 建立並執行正式前端

```bash
cd webapp
npm run build
cd ..

uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```

FastAPI 會直接提供 `webapp/dist/`，瀏覽器開啟 `http://localhost:8000`。

## Docker

先 clone private data repo 並執行 `git lfs pull`，再建立 image 並掛載其資料目錄：

```bash
docker build -t hullwatch .
docker run --rm -p 8000:8000 \
  --mount type=bind,source="$(cd ../hullwatch-data/data && pwd)",target=/srv/hullwatch/data \
  hullwatch
```

燃油行情快取、通知訂閱與上傳模型會寫入 artifacts 目錄，因此掛載目錄必須允許容器寫入。

若容器內沒有 artifacts，啟動腳本會建立合成資料，方便快速預覽；正式環境應一律掛載已產生的 artifacts。

## 環境變數

| 變數 | 用途 | 預設值 |
| --- | --- | --- |
| `HW_DATA_DIR` | 資料與 artifacts 根目錄 | `<repo>/data` |
| `HW_THRESHOLD` | Speed Loss 立即處置／清洗門檻 | `10` |
| `HW_WATCH_THRESHOLD` | Speed Loss 固定密切留意門檻 | `5` |
| `HW_WATCH_WINDOW` | 預估幾天內達清洗門檻也列入密切留意 | `60` |
| `HW_LLM_PROVIDER` | AI 顧問模式：`stub` 或 `bedrock` | `stub` |
| `HW_BEDROCK_MODEL` | Bedrock 模型 ID | Claude Sonnet 預設模型 |
| `HW_BEDROCK_REGION` | Bedrock AWS Region | `us-east-1` |
| `HW_RETRIEVER` | 檢索模式：`local` 或 `bedrock_kb` | `local` |
| `HW_BEDROCK_KB_ID` | Bedrock Knowledge Base ID | 空值 |
| `HW_FUEL_LIVE_ENABLED` | 是否讀取外部燃油行情 | `1` |
| `HW_RESET_DATASET_URI` | 「資料設定→資料重置」的原始資料來源（`s3://bucket/prefix/` 或本地目錄；空值＝自動偵測） | 空值 |
| `HW_SES_FROM_EMAIL` | SES 寄件地址；空值表示停用 | 空值 |
| `HW_SES_REGION` | SES AWS Region | `us-east-1` |
| `HW_DISCORD_WEBHOOK_URL` | Discord webhook；空值表示停用 | 空值 |
| `PORT` | Docker 內 Uvicorn 監聽埠 | `8000` |

所有 AWS 憑證應由執行環境的 IAM Role、AWS profile 或 secret manager 提供，不應寫入原始碼或提交到 Git。

通知通道設定與排程方式請參考 [`docs/notification-delivery.md`](docs/notification-delivery.md)。

## API

主要端點包括：

- `GET /api/health`
- `GET /api/fleet`
- `GET /api/ship/{ship_id}`
- `GET /api/ship/{ship_id}/forecast`
- `GET /api/schedule`
- `GET /api/roi`
- `GET /api/fuel-prices`
- `GET /api/alerts`
- `POST /api/noon-report/file`
- `POST /api/data/reset`（清空站台資料並從原始資料集重建；`GET /api/data/reset/status` 輪詢進度）
- `POST /api/advisor`
- `POST /api/inspect`

服務啟動後可透過 `/docs` 查看 FastAPI 自動產生的完整 OpenAPI 文件。

## 測試與品質檢查

Python：

```bash
pytest -q
```

前端：

```bash
cd webapp
npm test
npm run lint
npm run build
```

## 部署資料

Docker image 不包含正式資料。AWS 執行環境可使用唯讀 GitHub machine account、GitHub App 或細粒度 token clone private `hullwatch-data`，執行 `git lfs pull` 與 manifest 驗證後，再把 `data/` 掛載至容器的 `/srv/hullwatch/data`。不要把 token 寫進 image、repository 或 shell script。

若資料量或更新頻率超過 Git LFS 適合的範圍，可再將部署來源改為 private S3；資料版本與 checksum 契約維持相同。

只提供 Dashboard 與 API 時不需要部署原始資料目錄；`data/raw/` 僅在重新訓練或重建 artifacts 時使用。S3 bucket 應保持 private，並限制為服務執行角色所需的最小權限。

EC2 部署的基礎說明請參考 [`deploy/deploy-ec2.md`](deploy/deploy-ec2.md)。

## 已知限制

- Speed Loss 是相對每艘船最近乾淨基準期的估計值；清洗不完全可能使基準殘留髒污。
- 日報粒度會限制偵測速度；更高頻率的航行資料可以降低延遲。
- 缺少 STW、海流、吃水或縱傾等欄位時，能效歸因精度會下降。
- 清洗成本、油價與碳排係數是可調情境參數，不代表固定市場報價。
