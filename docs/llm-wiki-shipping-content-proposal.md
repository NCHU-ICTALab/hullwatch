# LLM Wiki 航運知識擴充提案

> 狀態：待討論、尚未寫入 `hullwatch-data/llm-wiki/wiki/`
>
> 目標讀者：HullWatch 內部客服與展示人員
>
> 原則：先讓客服能正確解釋產品，再補足回答問題所需的航運背景；不把 Wiki 當成即時行情、法規或航行決策系統。

## 建議範圍

建議採「產品客服 + 第一層航運背景」：客服可以說明畫面、公式、資料限制與常見術語，也能解釋為何船體髒污、螺旋槳狀態、航速及天候會影響油耗；但不提供船長級操作指示、港口許可判定或法律意見。

知識分為三層：

1. **產品真相（最高優先）**：目前程式實際資料來源、公式、模型、限制、操作流程與已知問題。
2. **穩定的航運背景**：支援產品解釋所需的船舶效能、燃油、維護、排放與日報欄位知識。
3. **會快速變動的外部資訊**：燃油即時價格、港口規則、法規適用狀態與市場行情不固化成答案；客服應即時查詢具日期的權威來源，或轉交領域人員。

## 第一批建議頁面

| 建議路徑 | 回答的問題 | 內容邊界 |
|---|---|---|
| `concepts/noon-report-and-navigation-fields.md` | STW、SOG、吃水、風浪、航行時數是什麼？ | 說明欄位、單位與常見資料品質問題，不教航行操作 |
| `concepts/propulsion-fuel-and-efficiency.md` | 航速為何影響油耗？FOC、SFOC、LCV 與 VLSFO-equivalent 是什麼？ | 解釋立方律是近似情境，不宣稱所有船況皆成立 |
| `concepts/emissions-and-regulatory-metrics.md` | tCO2、排放係數、CII、DCS、MRV 有何差別？ | 明確標示計算邊界、資料日期；不提供法律適用判定 |
| `concepts/hull-fouling-and-cleaning.md` | 船殼髒污、PP、UWC、UWI、DD 的差別與可能效果？ | 區分檢查與實體介入；不承諾個別港口允許清洗 |
| `faq/cost-carbon-and-attribution.md` | 每月超額成本、碳排與船殼／螺旋槳歸因怎麼算？ | 直接引用現行公式，清楚標示估算、proxy 與單位 |
| `faq/models-and-counterfactuals.md` | 有哪些模型？哪個輸出油耗、Speed Loss、ROI？ | 區分 production ML、Dashboard 規則與離線實驗 |
| `workflows/answering-shipping-domain-questions.md` | 客服可以回答到哪裡？何時轉交？ | 提供查證、引用、拒答與升級流程 |

第一批也應同步修訂既有 `faq/models.md` 與 `limitations/current-limitations.md`，避免新舊說法互相矛盾。產品數字與模型現況以 [`product-truth-audit.md`](product-truth-audit.md) 為基準。

## 每頁最低欄位

延續目前 Karpathy-style Wiki 的 Markdown-first 作法，每頁至少要有：

- `scope`：這頁能回答與不能回答什麼。
- `answer`：客服可直接使用的簡明說法。
- `details`：公式、單位、名詞與必要例子。
- `product_behavior`：HullWatch 目前實際如何使用該概念。
- `limitations`：估算、proxy、資料時效與不可推論事項。
- `sources`：來源名稱、URL／repo 路徑、發布或擷取日期。
- `valid_as_of`：涉及外部規則或數值時必填。
- `escalate_when`：什麼情況必須轉交資料科學、船務或法遵人員。

## 來源與更新規則

- 產品行為只以程式碼、artifacts、測試與產品稽核文件為準；規劃文件不能證明功能已完成。
- 航運與排放優先使用 IMO、ISO 公開摘要、主管機關、港口管理機關、船級社或資料提供者的第一方文件。
- ISO 付費標準只記錄可公開引用的摘要與連結，不複製受限全文。
- 每個外部來源保留發布日期或擷取日期；易變內容需有 `valid_as_of` 和重新查證期限。
- 不將未確認授權的整頁網頁複製進 private repo；只保存必要摘要、來源指標與取得證據。
- 內容更新可直接推 `main`，但至少要通過 Markdown/schema 檢查、來源完整性檢查與秘密掃描。現有 `SCHEMA.md` 若仍要求 PR，需先改成與這項決策一致。

## 不應靜態寫死的內容

- 即時燃油價格、匯率、運價與市場預測。
- 各港口當下的水下清洗許可、環保限制與作業窗口。
- 最新法規是否適用於特定船舶、航線或公司。
- 航行安全、避碰、操船或機艙控制指示。
- 未經驗證的個別船舶維護效益承諾。
- 客戶機密、憑證、通知設定、原始航行資料與模型權重內容。

## 建議客服回答格式

1. 先直接回答問題。
2. 說明 HullWatch 畫面目前如何計算或呈現。
3. 標示「觀測值、模型推導、情境估算或合成資料」。
4. 涉及易變資訊時附 `valid_as_of` 與來源。
5. 不能可靠回答時，說明缺少什麼資料並轉交適當角色。

## 待使用者確認

1. 是否採用建議的「產品客服 + 第一層航運背景」範圍，而不是建立完整航運百科？
2. 航運知識第一批是否按上表七頁實作？
3. 是否正式把 Wiki 治理改成「允許直接推 main，但必須通過自動檢查」，取代現有 PR 規則？
4. 客服是否允許回答 CII／DCS／MRV 的概念比較，但遇到特定船舶適用性時一律轉交法遵／船務？
