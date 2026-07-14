# HullWatch Email／Discord 通知交付說明

## 本批已完成

- 設定頁新增「電子報訂閱」，可建立多筆 Email 或 Discord 訂閱。
- 每筆訂閱必須選擇至少一艘船；摘要只包含該筆訂閱選取的船。
- Email 收件地址在 API 回應與畫面中會遮罩；Discord webhook 只存在後端環境變數，不回傳前端。
- 「寄送目前摘要」會實際呼叫 Amazon SES 或系統 Discord webhook。
- 訂閱資料儲存在 artifact 目錄的 `notification-subscriptions.json`；`data/` 已由 Git 忽略。

目前專案沒有登入或權限管理，因此這是比賽 demo 的單一工作區訂閱簿，不宣稱是正式多租戶帳號系統。

## Bash 啟動設定

```bash
export HW_SES_FROM_EMAIL='verified-sender@example.com'
export HW_SES_REGION='us-east-1'
export HW_DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'

cd hullwatch
.venv/Scripts/python.exe -m uvicorn app.api.main:app --reload --port 8000
```

Amazon SES 若仍在 sandbox，寄件者與收件者都必須先驗證。環境變數變更後要重啟 FastAPI。

## 現場驗證

1. 開啟「工具 → 設定 → 電子報訂閱」。
2. 建立 Email 訂閱，選 1–2 艘船，按「寄送目前摘要」。
3. 建立 Discord 訂閱，選不同船舶，按「寄送目前摘要」。
4. 確認兩個通道只收到各自選取的船，且 API／畫面沒有顯示完整 email 或 webhook。

## 後續代辦

- 以 EventBridge Scheduler／排程工作每日觸發摘要，並加入時區、寄送頻率與取消訂閱連結。
- 為即時 critical alert 增加事件去重、重試、dead-letter queue 與 delivery log。
- 正式部署前接 Cognito／SSO 與 RBAC，讓使用者只能管理自己的訂閱。
- 將本地 JSON store 換成 DynamoDB，並加上 email 驗證與資料保留政策。
- 製作 HTML email 與 Discord embed；目前刻意使用可靠的純文字摘要。
