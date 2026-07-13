# HullWatch — 陽明海運船體能效監控與清洗決策系統

AWS 百工百業瘋 AI 黑客松（陽明海運命題）參賽作品。
從每日正午報表與水下報告，量化「因船體髒污導致的 Speed Loss」，
並將其翻譯成清洗排程與金錢決策，補上船岸人力斷層。

> 方法論改編自學長之工業刀具磨耗研究（基準法 + Delta 特徵 + XGBoost/Optuna +
> 肘點法分級 + LLM 診斷管線），已獲原作者同意使用；引用格式見簡報。

## 系統架構

```text
noon_reports.csv ─┐
events.csv ───────┤  schema.py（欄位對應，當天唯一要改的檔）
                  ▼
  良好天氣篩選 → 水下事件對齊 → 相對化特徵（乾淨基準 Robust Scaling）
                  ▼
  乾淨基準模型：monotone XGBoost F(v_rel, wind, draft_rel)
     ├─ 殘差 → 超額油耗（→ 錢、CO₂）
     └─ 曲線反演 → Speed Loss %（ISO 19030 語意：相對自身基準期）
                  ▼
  肘點法分級（弱膝點自動退回分位數）＋ 結垢率外推 ＋ 180 天成本掃描
                  ▼
  FastAPI 單體（/api/fleet /ship /roi /advisor /inspect）＋ 單檔前端
                  ▼
  AI 顧問：LangGraph 工具迴圈（唯讀工具）
     ├─ LLM 層：Bedrock Claude（正式）↔ scripted 決定性回答（fallback）
     └─ 檢索層：Bedrock KB（正式）↔ 本地 TF-IDF（fallback）
```

驗證：Leave-One-Ship-Out ＋ 時間分塊（嚴禁隨機 CV）。
合成資料上以 ground truth 驗收：speed loss 還原 MAE < 1.5pp、corr > 0.9。

## 快速開始

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -r requirements.txt
python -m app.pipeline.run --synth               # 合成資料 + 訓練 + artifacts
uvicorn app.api.main:app --port 8000             # http://localhost:8000
pytest                                            # 全套測試
```

Docker：`docker build -t hullwatch . && docker run -p 8000:8000 hullwatch`
（EC2 部署見 [deploy/deploy-ec2.md](deploy/deploy-ec2.md)）

## 比賽當天 runbook

1. 真資料 `noon_reports.csv` / `events.csv` 放進 `data/raw/`
   （水下報告若是 PDF：解析成 events.csv 的四欄 schema 即可）。
2. 欄位名不同 → 只改 [app/schema.py](app/schema.py) 的 `COLUMN_ALIASES`。
3. `python -m app.pipeline.run` 重訓 + 產 artifacts。
4. `python -m app.pipeline.tuning --trials 50`（Optuna，跨船穩定目標）。
5. Bedrock 環境變數：`HW_LLM_PROVIDER=bedrock`、`HW_BEDROCK_MODEL=<模型ID>`、
   `HW_BEDROCK_REGION=<區域>`；KB 可用時 `HW_RETRIEVER=bedrock_kb HW_BEDROCK_KB_ID=<id>`。
6. 「高髒污樣本稀少」時再評估 TabDDPM 擴增（P2，管線掛載點在特徵層之後）。
7. **交件結果檔**（繳交項目 7）：`python -m app.pipeline.export`
   → `data/submission/預測結果_HullWatch.xlsx`（每船摘要、每日明細、方法與驗證三張表）。
8. 水下報告 PDF → `python -m app.pipeline.report_parser <pdf> --out data/raw/events.csv`
   （先人工過目輸出再跑管線；掃描檔改走 Bedrock 多模態）。

## 已知限制（誠實申報 = 評審「未盡之處」）

- 正午報表航速多為 SOG，洋流污染 Speed Loss；正規 ISO 19030 需 STW。
- 一天一筆 → 需 7 天平滑，偵測延遲約一週；15 分鐘級資料可大幅改善。
- Speed Loss 是「相對自身最近基準期」的值：清洗不完全時基準有殘留髒污。
- 清洗成本/油價為可調假設；缺吃水/縱傾/海流欄位時歸因粒度受限。
