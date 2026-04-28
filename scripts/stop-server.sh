#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".docker-compose.yml" ]; then
  echo "缺少 .docker-compose.yml（请在导出包目录执行）"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "未检测到 docker compose / docker-compose"
  exit 1
fi

$COMPOSE_CMD -f .docker-compose.yml down
echo "服务已停止。"
