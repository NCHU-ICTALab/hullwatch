# LLM Wiki 航運背景研究筆記（第一層）

> 研究日期：2026-07-15
>
> 用途：供 HullWatch 內部客服 Wiki 後續編寫，不是法規遵循手冊或船舶操作指令。
> 來源原則：只採專案原始碼／主辦資料說明，以及 IMO、ISO、EMSA、政府、船級社或設備製造商的一手公開資料。本文以轉述為主，避免大量複製受版權保護內容。

## 1. 正午日報欄位：客服需要先分清楚什麼

### 1.1 STW 與 SOG

- **STW（Speed Through Water，對水航速）**：船相對周圍水體的速度。在導航設備語境中，STW 通常來自 water log。
- **SOG（Speed Over Ground，對地航速）**：船相對地面的速度，通常來自 GNSS。
- 海流的 set/drift 會進入 SOG，卻不會以同樣方式進入 STW。因此同一時間的 STW 與 SOG 不必相等，不能把兩欄互相當成重複值。英國 Maritime and Coastguard Agency 的雷達穩定模式指引明確區分：sea-stabilised 使用 water-log STW，ground-stabilised 使用 GNSS SOG。[UK MCA：MGN 379](https://www.gov.uk/government/publications/mgn-379-mf-amendment-1-use-of-electronic-navigational-aids/mgn-379-mf-amendment-1-use-of-electronic-navigational-aids)
- 主辦資料 README 也明確說兩欄是獨立來源，且可能因洋流／潮流而顯著不同；兩欄在本資料集填充率皆為 100%。來源位於 private data repo 的 `data/yangming-aws-summit-hackathon/README.md`。

HullWatch 的實作：

- [`ingest_yangming.py`](../app/pipeline/ingest_yangming.py) 把 `SPEED_THROUGH_WATER` 當作 canonical `avg_speed`，只有 STW 缺值時才退回 SOG。
- `current_proxy = DIFF_STW_SOG_SLIP` 保留為洋流代理特徵，但它不是直接量測的洋流速度向量。
- 因此客服應說「模型以 STW 為主要效能航速」，不要說「Dashboard 顯示的是 GPS 航速」。

### 1.2 吃水與 trim

- **艏吃水／艉吃水／舯吃水**描述船體浸入水中的深度；trim 是艏艉吃水差所代表的縱向姿態。
- 吃水與 trim 會改變船體阻力和所需推進功率；IMO GreenVoyage2050 將 trim/draft optimization 說明為降低阻力、推進功率、燃油與排放的營運措施，但效果會依船型、載況與操作條件而變。[IMO GreenVoyage2050：Trim and draft optimization](https://greenvoyage2050.imo.org/technology/trim-and-draft-optimization/)
- HullWatch 的 `mean_draft` 優先取 `MID_DRAFT`；缺值才使用艏艉平均。clean-baseline 模型使用相對吃水 `draft_rel = mean_draft / draft_ref`，沒有直接建模 trim。因此客服不能說系統已完成「最佳 trim 最佳化」。[特徵工程](../app/pipeline/features.py)

### 1.3 風級與風速

- `WIND_SCALE` 是 Beaufort force，不是單純把連續風速換一個單位。NOAA 的官方 Beaufort 表中，4 級是 moderate breeze，對應約 11–16 knots；尺度同時連結風況與海面現象。[NOAA Weather Prediction Center：Beaufort Wind Scale](https://www.wpc.ncep.noaa.gov/html/beaufort.shtml)
- 命題把良好天氣定義為 `WIND_SCALE ≤ 4`；所有 102 個 `PREDICT` 日也符合此條件。來源為 private data repo 的資料集 README「placeholder 說明」。
- HullWatch clean-baseline 與 102 模型的訓練篩選沿用此門檻，但資料仍可能有浪、湧浪、洋流、淺水、吃水或量測噪音。`風級 ≤4` 不等於「海況完全相同」或「已排除一切環境影響」。

### 1.4 全速時數

- `HOURS_FULL_SPEED` 是當日處於題目所定義「主機全速航行」狀態的時數；預測日要求至少 22 小時。它不是航程總時間，也不表示引擎一直處於 100% MCR。來源為 private data repo 的資料集 README。
- 主辦要求預測的是 `ME_FULLSPEED_CONSUMP_*`：全速時段內該燃料的主機消耗量。HullWatch 102 模型內部先正規化成 24 小時率：

\[
DailyFOC_{24h}=\frac{MEFullSpeedConsumption}{HoursFullSpeed}\times24
\]

  預測後再乘 `hours/24` 還原成該日全速時段的提交值。公式見 [`predict102.py`](../app/pipeline/predict102.py)。
- 因此客服不能把提交值解釋為「全船整日總油耗」；它不含輔機、鍋爐與非全速時段。

## 2. 航速、功率與油耗：立方關係能說到哪裡

### 2.1 基本近似

在低 Froude number、幾何和環境條件相近時，可用：

\[
Resistance\propto V^2,\qquad Power=Resistance\times V\propto V^3
\]

MAN Energy Solutions 的船舶推進技術手冊把這個近似稱為 propeller law，但同一份一手資料也明確提醒：三次方只是低 Froude number 的假設；波浪阻力重要時，指數可能接近四次方甚至更高，而且不同船型與航速的 exponent 不同。[MAN Energy Solutions：Basic principles of ship propulsion](https://www.man-es.com/docs/default-source/document-sync-archive/basic-principles-of-ship-propulsion-eng.pdf?sfvrsn=48fc05b5_7)

若另外假設特定操作區間內 SFOC 近似不變，燃油率才可隨功率近似呈三次關係。這是情境估算，不是普遍定律。

### 2.2 HullWatch 如何使用

HullWatch 的 ROI 引擎假設 Speed Loss 為 $s$ 時，若要維持同一航速：

\[
ExcessFuel=f_{ref}\left(\frac{1}{(1-s)^3}-1\right)
\]

其中 $s$ 在程式內裁到 0～35%，`f_ref` 是該船乾淨基準的 24h VLSFO-equivalent FOC。實作見 [`roi.py`](../app/pipeline/roi.py)。

客服正確說法：

- 「這是 speed–power cubic approximation 下的超額燃油情境。」
- 「適合把 Speed Loss 翻成可比較的成本量級。」

客服不可說：

- 「船速下降 10%，任何船都一定多燒固定百分比燃油。」
- 「立方律已精確控制所有海況、吃水、螺槳、引擎效率與航速區間。」
- 「ROI 數字就是帳務實際燃油成本。」

### 2.3 主要限制

- 實船的 speed–power exponent 隨船型、航速、Froude number、吃水、trim、海況與污損狀態改變。[MAN propulsion guide](https://www.man-es.com/docs/default-source/document-sync-archive/basic-principles-of-ship-propulsion-eng.pdf?sfvrsn=48fc05b5_7)
- 引擎 SFOC 會隨負載與調校改變，燃油不一定與軸功率完全線性。
- 風、浪、流、淺水和操舵都會改變同一 SOG/STW 下所需功率。IMO 的效能技術資料也提醒，環境與營運變數會為 in-service performance measurement 帶來噪音。[IMO GreenVoyage2050：Air lubrication applicability](https://greenvoyage2050.imo.org/technology/air-cavity-lubrication/)
- 對單船高精度決策，應優先使用該船校準的 speed–power／FOC curves 或 shaft power、torque、RPM 等資料，而非只有固定三次方。

## 3. FOC、SFOC、LCV 與 VLSFO equivalent

### 3.1 FOC 與本專案不同油耗欄位

**FOC（Fuel Oil Consumption）**泛指燃油消耗，但問答時一定要先確認範圍與時間基準：

- `TOTAL_CONSUMP`：資料 README 定義為當日總油耗，含輔機／鍋爐。
- `ME_CONSUMPTION`：主機油耗合計。
- `ME_FULLSPEED_CONSUMP_<fuel>`：主機在全速時段使用的特定燃料質量。
- HullWatch canonical `daily_foc`：全速時段燃料先折為 VLSFO energy-equivalent，再正規化成 24h，單位 MT/day。

欄位定義的權威來源是 private data repo 的 `data/yangming-aws-summit-hackathon/README.md`，而不是一般航運文章。

### 3.2 SFOC

**SFOC（Specific Fuel Oil Consumption）**表示單位輸出能量所需的燃油質量，常見單位 `g/kWh`。英國 MCA 的輪機考試綱要也使用 `g/kWh` 或 `kg/kWh`，並把每日燃油估算建立在 SFC、功率與運轉時數之上。[UK MCA：Chief Engineer syllabus](https://www.gov.uk/government/publications/mca-small-vessel-competency-examination-syllabuses/chief-engineer-statutory-and-operational-requirements-written-examination-syllabus)

在本競賽資料中，SFOC 屬於 H 類主機性能欄位，預測窗口內會被隱藏，所以 production 102 模型刻意不使用它。客服不可說 102 預測是直接以已知 SFOC × horsepower 算出。

### 3.3 LCV

**LCV（Lower Calorific Value，低位熱值）**是單位質量燃料燃燒後可釋放的能量；IMO data compendium 的定義單位是 MJ/kg。[IMO Compendium：Fuel Lower Calorific Value](https://imocompendium.imo.org/public/IMO-Compendium/Current/DS/Ship%20Emissions%20Report/d11.htm)

本資料集契約採：

| 燃料 | LCV (MJ/kg) |
|---|---:|
| HSHFO | 40.2 |
| VLSFO | 40.2 |
| ULSFO | 41.2 |
| LSMGO | 42.7 |
| BIO_HSFO | 39.4（近似，摻配比會變） |

資料集契約來源為 private data repo 的資料集 README「燃料熱值對照」。IMO 2023 LCA Guidelines 的 default pathways 亦列出 HFO(VLSFO) 40.2 MJ/kg、light fuel 41.2 MJ/kg、MDO/MGO 42.7 MJ/kg，但實務仍須依實際 fuel pathway 與文件判讀。[IMO MEPC.376(80), Appendix 2](https://wwwcdn.imo.org/localresources/en/OurWork/Environment/Documents/MEPC.376%2880%29.pdf)

### 3.4 VLSFO equivalent

跨燃料加總時，HullWatch 以能量等值折算：

\[
VLSFOEquivalentMass=\frac{\sum_j Mass_j\times LCV_j}{40.2}
\]

實作位於 [`ingest_yangming.py`](../app/pipeline/ingest_yangming.py)。這讓不同燃料日可放在共同能量基準比較，但它：

- 不是實際加注的 VLSFO 質量；
- 不等於價格等值；
- 不自動代表相同 CO₂、硫、生命週期 GHG 或引擎效率；
- BIO_HSFO 的 39.4 是資料集近似值，不能外推到任意 bio blend。

另外，VLSFO 是硫含量／市場分類，不代表唯一化學組成。IMO 的 LCA default table就區分 HFO(VLSFO) 與 LFO(VLSFO) pathway；客服回答外部船用油規格時，不可把本資料集 40.2 當成所有 VLSFO 的固定檢驗值。[IMO MEPC.376(80)](https://wwwcdn.imo.org/localresources/en/OurWork/Environment/Documents/MEPC.376%2880%29.pdf)

## 4. PP、UWC、UWI、DD 與船體髒污

### 4.1 Biofouling 為什麼影響效能

IMO 將 biofouling 說明為微生物、植物、藻類與動物在船舶水下表面的不希望累積。它會增加船體阻力，進而增加燃油成本與空氣污染／GHG 排放；適當管理可改善 hydrodynamic performance。[IMO：Biofouling](https://www.imo.org/en/ourwork/environment/pages/biofouling.aspx)

這個物理關係支持 HullWatch 監測 hull/propeller performance，但不能單靠高油耗就斷言一定是生物污損；粗糙度、塗層、螺槳空蝕、海況、引擎與量測誤差都可能造成相似訊號。

### 4.2 本資料集事件的確切語意

事件名稱以 private data repo 的資料集 README「養護類型說明」為準：

| 代碼 | 專案中文 | 物理語意與客服重點 |
|---|---|---|
| PP | 螺旋槳拋光 | 改善螺旋槳表面粗糙／附著，目標是減少 propeller efficiency loss；IMO GreenVoyage2050 也指出效果依船型、操作與水域而異，不能套固定節省率。[IMO：Propeller polishing](https://greenvoyage2050.imo.org/technology/propeller-polishing/) |
| UWC | 船殼清洗 | 移除水下船體 biofouling；可能降低阻力，但清洗方式、塗層損傷、生物廢棄物捕集與港口規定都要考慮。[IMO：Biofouling](https://www.imo.org/en/ourwork/environment/pages/biofouling.aspx) |
| UWI | 水下檢查 | **只有拍照／檢查，無物理介入**；不應假設事件後效能恢復 |
| DD | 進塢 | 本資料集定義為全面塗裝 + 機械保養；可能同時改變船殼、螺槳、塗層與機械狀態，因此不能把全部改善只歸給船殼 |
| UWI+PP | 檢查 + 螺旋槳拋光 | 有 PP 物理介入；不是純 UWI |
| UWC+PP | 船殼清洗 + 螺旋槳拋光 | 同時介入 hull 與 propeller，不能單獨識別兩者效果 |

### 4.3 HullWatch 的事件處理

- UWC／DD 開啟「乾淨基準」reset；PP 只重置 `days_since_polish`；UWI 不 reset。事件對齊程式見 [`events.py`](../app/pipeline/events.py)與 [`schema.py`](../app/schema.py)。
- 因此系統在語意上已正確區分「檢查」與「介入」。但事件後看到 Speed Loss 下降仍只是 observational association，不是事件的因果證明。
- 現有 hull/propeller 色帶是全船隊事件前後中位改善比例 proxy，不是影像判讀或逐船 causal model。詳細限制見[產品真實性稽核](product-truth-audit.md#5-船殼螺旋槳-speed-loss-歸因現在怎麼算)。

### 4.4 清洗的環境與營運邊界

IMO 指出 in-water cleaning 雖能移除 biofouling，也可能損傷 anti-fouling coating、縮短塗層壽命，並釋放有害廢棄物或入侵物種。因此客服只能提供效能分析，不應替船東判斷某港是否允許清洗、應採哪種工法或是否符合當地環保規定。[IMO：Biofouling and in-water cleaning](https://www.imo.org/en/ourwork/environment/pages/biofouling.aspx)

## 5. CO₂ factor、CII、IMO DCS 與 EU MRV

### 5.1 3.114 CO₂ factor

HullWatch 使用：

\[
CO_2=FuelMass\times3.114
\]

3.114 的單位是 `tCO₂ / t fuel`。IMO MEPC.376(80) 的 HFO(VLSFO) default pathway列出 LCV 40.2 MJ/kg 與 `Cf CO₂ = 3.114 gCO₂/g fuel`，質量比換算後就是同一數值。[IMO MEPC.376(80), Appendix 2](https://wwwcdn.imo.org/localresources/en/OurWork/Environment/Documents/MEPC.376%2880%29.pdf)

回答邊界：

- 這是 fuel-to-exhaust 的 CO₂ mass conversion，不是完整 well-to-wake GHG。
- IMO 將 well-to-wake 分為 Well-to-Tank 與 Tank-to-Wake；上游生產與運輸排放不能只靠 3.114 得出。[IMO：Lifecycle GHG framework](https://www.imo.org/en/ourwork/environment/pages/lifecycle-ghg---carbon-intensity-guidelines.aspx)
- HullWatch 把 VLSFO-equivalent 超額燃油一律乘 3.114，是競賽情境估算；不能當成各燃料 pathway 的法規申報值。

### 5.2 CII

- CII 是船舶的**年度營運碳強度**，把 CO₂ 排放連結到運輸工作量。IMO G1 Guidelines 說明 operational CII 是每 transport work 的平均 CO₂ 指標；不同指標可用實際貨量或容量 proxy。[IMO MEPC.352(78)](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.352%2878%29.pdf)
- IMO 現行公開 FAQ 說明 CII 適用於 5,000 GT 以上船舶，年度 attained CII 要與 required CII 比較並評為 A–E；D 連續三年或 E 一年需有 corrective action plan。[IMO：EEXI and CII FAQ](https://www.imo.org/en/mediacentre/hottopics/pages/eexi-cii-faq.aspx)
- HullWatch 的「每月超額 CO₂」只有超額燃油 × factor，沒有全年燃油、容量／貨量、距離、reference line、reduction factor、correction factors 或 verification，**所以不是 CII，也不能推算 A–E rating**。

### 5.3 IMO DCS

- IMO DCS 要求 5,000 GT 以上船舶自 2019 年起蒐集各燃料種類的消耗和其他指定資料，按年度向 flag State 報告並經查核；自 2023 年起 DCS 資料用於計算 CII。[IMO：Data Collection System](https://www.imo.org/en/ourwork/environment/pages/data-collection-system.aspx)
- 正午日報可作營運資料來源之一，但一份 HullWatch 上傳 CSV 或 Dashboard artifact 不等同已完成 DCS annual report、Administration verification 或 Statement of Compliance。

### 5.4 EU MRV

- EU MRV 是 EU/EEA 範圍的監測、報告與驗證制度。EMSA 的現行 FAQ 說明 Regulation (EU) 2015/757 對符合範圍的船舶、EEA 相關航程與 GHG 報告有特定適用條件，並要求 monitoring plan 與 accredited verifier。[EMSA：MRV FAQ](https://emsa.europa.eu/reducing-emissions/mrv-changes/faq-mrv-changes.html)
- 2024 起制度範圍已擴及 CH₄ 與 N₂O；適用船型／噸位與 ETS 相關要求也有更新，不能只靠舊的「5,000 GT、CO₂」一句話回答所有情境。[EMSA：MRV Regulation changes](https://www.emsa.europa.eu/reducing-emissions/mrv-changes.html)
- HullWatch 目前不是 THETIS-MRV reporting／verification 系統，也沒有 verifier workflow。客服不應把 Dashboard 排放估算稱為 EU MRV verified emissions。

## 6. ISO 19030 與 HullWatch 的說法邊界

ISO 19030-1 公開摘要說明，其目標是量測 hull and propeller performance 的變化，並比較同一艘船相對自身隨時間的表現；公開摘要也註明一般原則適用於 conventional fixed-pitch propeller 的船舶。[ISO 19030-1 官方摘要](https://www.iso.org/standard/63774.html)

HullWatch 採同船乾淨基準、STW、良好天氣篩選、事件對齊與 Speed Loss 時序，與這種「同船歷時比較」精神一致；但目前沒有證據證明已逐項實作 ISO 19030-2 default method 的所有量測、資料品質、環境修正與計算要求。

客服可以說：

- 「方法依 ISO 19030 的同船相對基準精神設計。」
- 「這是 ISO 19030-aligned／inspired 的分析。」

客服不可說：

- 「系統通過 ISO 認證。」
- 「此數字是完整依 ISO 19030-2 計算的法定值。」
- 「任何船型都適用相同方法且具有相同精度。」

## 7. 內部客服回答邊界

### 7.1 可以直接回答

- 畫面欄位、單位、資料日期、模型版本、資料來源與計算公式。
- 為何 STW 和 SOG 不同、為何用風級與全速時數篩選。
- 本資料集事件代碼與是否有物理介入。
- Dashboard 的 Speed Loss、成本、碳排與 hull/prop proxy 是如何計算。
- 已知限制、不確定性和下一步需要的資料。

### 7.2 必須加限定語

- 模型數字：使用「估計／推導／情境」而非「實際／證實」。
- 養護事件：使用「事件後觀察到」而非「事件造成」。
- 船殼／螺槳比例：使用「fleet-level proxy」而非「逐船診斷真值」。
- 油價：附 port、grade、source、as-of、market status 與 `estimated`。
- 法規：附適用年份與官方來源，提醒船旗、船型、GT、航程及規則版本會影響答案。

### 7.3 必須轉人工／專業人員

- 是否立即清洗、採何種清洗工法、是否能在某港進行水下清洗。
- 航行安全、最低推進功率、引擎負載、塗層損傷或空蝕的操作判斷。
- CII rating、DCS/MRV/ETS 合規結論或申報簽署。
- 燃油品質是否符合 ISO 8217、BDN、供油合約或引擎 OEM 規格。
- 任何會改變船期、進塢、財務承諾或法規責任的正式決策。

### 7.4 建議的安全回答格式

1. **先回答定義**：指出欄位／指標是觀測、模型推導或情境假設。
2. **再給專案數字**：連同單位、資料日期、船舶、燃料和時間基準。
3. **說明算法**：一至兩句公式或模型來源。
4. **主動揭露限制**：列最重要的一項混雜或缺失資料。
5. **附一手來源**：repo source 或 IMO／ISO／EMSA 官方頁。
6. **涉及安全／合規／作業時轉人工**。

範例：

> HullWatch 顯示的每月超額 CO₂ 是「模型估計的超額 VLSFO-equivalent fuel × 3.114 × 30 日」，單位為 tCO₂/30 days；它不是 CII 或 MRV verified emissions。若要判定 CII，還需要全年燃油、距離、容量／運輸工作量、適用 reference line 和查核流程。來源：HullWatch ROI 程式與 IMO CII Guidelines。

## 8. 建議轉成 Wiki 頁面的主題切分

這份研究筆記後續適合拆成以下第一層頁面，但本次不直接修改 `hullwatch-data/llm-wiki`：

1. `noon-report-navigation-fields.md`：STW／SOG、draft／trim、wind、full-speed hours。
2. `fuel-and-energy-basics.md`：FOC／SFOC／LCV／VLSFO equivalent。
3. `speed-power-and-roi.md`：立方近似、HullWatch 公式與限制。
4. `maintenance-and-biofouling.md`：PP／UWC／UWI／DD、事件語意與清洗邊界。
5. `shipping-carbon-compliance-primer.md`：CO₂ factor、CII、DCS、MRV，以及「HullWatch 不等於法規申報」。
6. `support-escalation-boundaries.md`：可回答、需限定、必須轉人工。

## 9. 一手來源索引

### 專案與資料

- 主辦資料集 README：private `hullwatch-data/data/yangming-aws-summit-hackathon/README.md`
- [資料轉換：ingest_yangming.py](../app/pipeline/ingest_yangming.py)
- [102 格油耗模型：predict102.py](../app/pipeline/predict102.py)
- [事件語意與對齊：schema.py](../app/schema.py)、[events.py](../app/pipeline/events.py)
- [Clean-baseline 特徵：features.py](../app/pipeline/features.py)
- [ROI 公式：roi.py](../app/pipeline/roi.py)
- [產品真實性稽核](product-truth-audit.md)

### 官方／第一方外部資料

- [ISO 19030-1 官方摘要](https://www.iso.org/standard/63774.html)
- [UK MCA：STW／SOG 導航設備定義](https://www.gov.uk/government/publications/mgn-379-mf-amendment-1-use-of-electronic-navigational-aids/mgn-379-mf-amendment-1-use-of-electronic-navigational-aids)
- [NOAA：Beaufort Wind Scale](https://www.wpc.ncep.noaa.gov/html/beaufort.shtml)
- [MAN Energy Solutions：Basic principles of ship propulsion](https://www.man-es.com/docs/default-source/document-sync-archive/basic-principles-of-ship-propulsion-eng.pdf?sfvrsn=48fc05b5_7)
- [IMO GreenVoyage2050：Trim and draft optimization](https://greenvoyage2050.imo.org/technology/trim-and-draft-optimization/)
- [IMO：Biofouling](https://www.imo.org/en/ourwork/environment/pages/biofouling.aspx)
- [IMO GreenVoyage2050：Propeller polishing](https://greenvoyage2050.imo.org/technology/propeller-polishing/)
- [IMO MEPC.376(80)：Marine Fuel LCA Guidelines](https://wwwcdn.imo.org/localresources/en/OurWork/Environment/Documents/MEPC.376%2880%29.pdf)
- [IMO MEPC.352(78)：CII Guidelines, G1](https://wwwcdn.imo.org/localresources/en/KnowledgeCentre/IndexofIMOResolutions/MEPCDocuments/MEPC.352%2878%29.pdf)
- [IMO：EEXI and CII FAQ](https://www.imo.org/en/mediacentre/hottopics/pages/eexi-cii-faq.aspx)
- [IMO：Data Collection System](https://www.imo.org/en/ourwork/environment/pages/data-collection-system.aspx)
- [EMSA：MRV FAQ](https://emsa.europa.eu/reducing-emissions/mrv-changes/faq-mrv-changes.html)
- [EMSA：MRV Regulation changes](https://www.emsa.europa.eu/reducing-emissions/mrv-changes.html)
