# EC2 部署手冊（Learner Lab 演練 / 比賽正式環境通用）

## 0. 前置

- Learner Lab：Start Lab 後從 AWS Details 取 CLI 憑證（每 session 輪換，別寫死）。
- 比賽環境：依主辦提供的方式取得 EC2 存取權。

## 1. 開 EC2

- AMI：Amazon Linux 2023，機型 t3.medium 以上（XGBoost 訓練吃記憶體）。
- Security Group：開 22（SSH）與 8000（HTTP，或 80 映射）。
- Learner Lab 注意：只能用現成的 `LabRole` / `LabInstanceProfile`，別嘗試建 IAM。
  掛上 LabInstanceProfile 後，容器內的 boto3 會自動透過 instance metadata 取得憑證。

## 2. 裝 Docker 並部署

```bash
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user && newgrp docker

# 上傳程式碼（scp 或 git clone）
scp -r hullwatch ec2-user@<IP>:~/    # 或 git clone <repo>

cd hullwatch
docker build -t hullwatch .
docker run -d --name hw -p 8000:8000 --restart unless-stopped hullwatch
```

瀏覽 `http://<EC2 公網 IP>:8000` → Live Demo 連結。

## 3. 比賽當天切換真資料 + Bedrock

```bash
# 1) 真實資料放進容器掛載的 data/raw/（noon_reports.csv + events.csv）
docker run -d --name hw -p 8000:8000 \
  -v ~/data:/srv/hullwatch/data \
  -e HW_LLM_PROVIDER=bedrock \
  -e HW_BEDROCK_MODEL=<環境提供的模型 ID> \
  -e HW_BEDROCK_REGION=us-east-1 \
  hullwatch
# 2) 欄位名不同 → 只改 app/schema.py 的 COLUMN_ALIASES 後重 build
# 3) Bedrock KB 可用時追加：-e HW_RETRIEVER=bedrock_kb -e HW_BEDROCK_KB_ID=<id>
```

## 4. 無 Docker 的備援路線

```bash
sudo dnf install -y python3.11 python3.11-pip
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.pipeline.run --synth
uvicorn app.api.main:app --host 0.0.0.0 --port 8000
```
