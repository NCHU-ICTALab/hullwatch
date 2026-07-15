# HullWatch Email／Discord 通知交付說明（規則 v2，2026-07-15 起）

## 訂閱模型

- 設定頁選單「**預警訂閱**」（原「電子報訂閱」）可建立多筆 Email 或 Discord 訂閱。
- 每筆訂閱分兩類（`kind`）：
  - **每日摘要（digest）**：訂閱船舶的狀態摘要。產品定位為每日固定時間發送；目前實作在
    **上傳新正午日報時觸發**（單筆上傳即時寄、CSV 匯入整批合併一封，寄送失敗不擋上傳），
    另可在訂閱列表手動「寄送目前摘要」。正式排程見〈後續代辦〉。
  - **預警（alert）**：只在該船更新後 Speed Loss 超過留意門檻
    （`HW_WATCH_THRESHOLD`，預設 5%）才寄；雲端自動巡檢鏈（EventBridge
    watcher／notifier，每 30 分鐘）也只寄給 alert 類訂閱。
- **訂閱建立當下即寄「訂閱確認」通知**（含訂閱船舶目前狀態）。
- 每筆訂閱必須選至少一艘船；通知只包含該筆訂閱選取的船。
- Email 地址在 API 回應與畫面中遮罩；Discord webhook 於**訂閱時自填**（遮罩顯示、不回傳完整值）。
- 訂閱資料存 artifact 目錄的 `notification-subscriptions.json`（`data/` 已由 Git 忽略）；
  正式站由 EC2 cron 每 15 分鐘同步上 S3，供警報鏈 Lambda 讀取（與 app 解耦）。

目前專案沒有登入或權限管理，因此這是比賽 demo 的單一工作區訂閱簿，不宣稱是正式多租戶帳號系統。

## Email 寄送路徑（中繼優先）

1. **主路徑＝主辦方 SQS 寄信中繼**：設定 `HW_EMAIL_QUEUE_URL` 後，app 將 SESv2 SendEmail
   payload 丟進主辦方 queue，由對方 Lambda＋SES 寄出（From＝`HW_EMAIL_QUEUE_FROM`，預設
   `HullWatch <events@awsug.net>`；ReplyTo＝`HW_SES_FROM_EMAIL`）——**任何收件者免 SES 驗證**。
2. 中繼未設時退**直寄 SES**：sandbox 下寄件者與收件者都要先驗證；未驗證收件者會收到自動驗證引導。
3. **Discord**：逐訂閱推播到自填 webhook；`HW_DISCORD_WEBHOOK_URL` 僅作系統頻道 fallback（選配）。

## Bash 啟動設定

```bash
export HW_EMAIL_QUEUE_URL='https://sqs.us-east-1.amazonaws.com/905418031238/emailQueue'  # 主辦方中繼（主路徑）
export HW_SES_FROM_EMAIL='team-mailbox@example.com'   # ReplyTo；中繼未設時的 SES 直寄寄件者
export HW_SES_REGION='us-east-1'
# export HW_DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'   # 選配：系統頻道 fallback

cd hullwatch
.venv/bin/python -m uvicorn app.api.main:app --reload --port 8000
```

環境變數變更後要重啟 FastAPI。正式站對應設定在機上 `/opt/hullwatch.env`
（見 cloud repo《手動部署完整步驟》§6–7、§10）。

## 現場驗證

1. 開啟「工具 → 設定 → 預警訂閱」，建立 Email 訂閱（選類型與 1–2 艘船）→ 當下收到「訂閱確認」信。
2. 於診斷頁上傳一筆正午日報 → digest 訂閱收到該船摘要；若該船 Speed Loss > 留意門檻，alert 訂閱收到預警。
3. 建立 Discord 訂閱（貼自己頻道的 webhook）→ 同步收到推播。
4. 確認各通知只含各自選取的船，且 API／畫面沒有顯示完整 email 或 webhook。

## 後續代辦

- 每日固定時間摘要：EventBridge Scheduler 第二條排程規則（目前由上傳日報觸發），並加入時區、寄送頻率與取消訂閱連結。
- 即時 critical alert 的重試、dead-letter queue 與 delivery log（雲端巡檢鏈已有跨輪去重）。
- 正式部署前接 Cognito／SSO 與 RBAC，讓使用者只能管理自己的訂閱。
- 本地 JSON store 換成 DynamoDB（表 `hullwatch-subscriptions` 已建、未接線），加上 email 驗證與資料保留政策。
- app 端摘要信 HTML 化（雲端巡檢警報信已是 HTML 排版；app 摘要目前刻意使用可靠的純文字）。
