#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x backend/.venv/bin/uvicorn ]; then
  echo '请先按 README 安装后端依赖。'
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  echo '请先运行 npm --prefix frontend run build。'
  exit 1
fi
if [ -f .env ]; then
  exec backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --env-file .env
fi
exec backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
