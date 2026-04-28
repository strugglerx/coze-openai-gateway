#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".docker-compose.yml" ]; then
  echo "缺少 .docker-compose.yml（请在导出包目录执行）"
  exit 1
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "已创建 .env（来自 .env.example），请按需修改后重试。"
fi

IMAGE_TAR="${1:-}"
if [ -z "$IMAGE_TAR" ] && [ -f "coze-openai-gateway-linux-amd64.image.tar" ]; then
  IMAGE_TAR="coze-openai-gateway-linux-amd64.image.tar"
fi

if [ -n "$IMAGE_TAR" ]; then
  if [ ! -f "$IMAGE_TAR" ]; then
    echo "找不到镜像 tar: $IMAGE_TAR"
    exit 1
  fi
  echo "加载镜像: $IMAGE_TAR"
  docker load -i "$IMAGE_TAR"
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "未检测到 docker compose / docker-compose"
  exit 1
fi

$COMPOSE_CMD -f .docker-compose.yml up -d
$COMPOSE_CMD -f .docker-compose.yml ps
echo "服务已启动。"
