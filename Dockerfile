FROM node:22-slim AS frontend-build

WORKDIR /build/webapp
COPY webapp/package.json webapp/package-lock.json ./
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM python:3.10-slim

WORKDIR /srv/hullwatch

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY --from=frontend-build /build/webapp/dist ./webapp/dist
COPY kb ./kb
COPY deploy/start.sh ./start.sh

EXPOSE 8000
# 環境變數（比賽當天視環境調整）：
#   HW_LLM_PROVIDER=bedrock  HW_BEDROCK_MODEL=...  HW_BEDROCK_REGION=...
#   HW_RETRIEVER=bedrock_kb  HW_BEDROCK_KB_ID=...
#   HW_FUEL_PRICE / HW_CLEAN_COST / HW_THRESHOLD
CMD ["sh", "start.sh"]
