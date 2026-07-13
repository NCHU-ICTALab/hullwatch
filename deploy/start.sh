#!/bin/sh
# 容器啟動：artifacts 不存在時先跑管線（預設合成資料；真資料放 data/raw/ 即自動使用）
set -e
if [ ! -f data/artifacts/fleet.csv ]; then
  echo "[start] artifacts 不存在，執行管線 ..."
  python -m app.pipeline.run --synth
fi
exec uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
