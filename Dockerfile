FROM python:3.10-slim

WORKDIR /srv/hullwatch

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY kb ./kb
COPY deploy/start.sh ./start.sh

EXPOSE 8000
# 環境變數（比賽當天視環境調整）：
#   HW_LLM_PROVIDER=bedrock  HW_BEDROCK_MODEL=...  HW_BEDROCK_REGION=...
#   HW_RETRIEVER=bedrock_kb  HW_BEDROCK_KB_ID=...
#   HW_FUEL_PRICE / HW_CLEAN_COST / HW_THRESHOLD
CMD ["sh", "start.sh"]
