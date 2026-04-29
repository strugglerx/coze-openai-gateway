PY   ?= python3
PORT ?= 38419
VENV := .venv
# docker build 标签，与 README 示例一致
IMAGE ?= coze-openai-gateway
# docker build 平台；留空表示使用宿主机默认平台
PLATFORM ?=
BUILDX ?= docker buildx build
# buildx 输出模式：默认 --load 便于后续 docker save
IMAGE_OUTPUT ?= --load
# make image-tar 输出（docker save）
TAR ?= coze-openai-gateway.image.tar
DIST_DIR ?= dist
EXPORT_NAME ?= coze-openai-gateway-linux-amd64
DOCKER_COMPOSE ?= docker compose

.PHONY: help install run run-dev run-pt run-pt-dev env image image-tar export-linux-amd64-zip compose compose-down clean

help:
	@echo "make install    # $(VENV) + requirements.txt"
	@echo "make run        # 正式跑：uvicorn（改代码需手动重启）"
	@echo "make run-dev    # 开发：uvicorn --reload（改 .py 自动重启）"
	@echo "make run-pt     # passthrough：零配置启动（MODE=passthrough，不依赖 .env）"
	@echo "make run-pt-dev # passthrough + --reload"
	@echo "make image      # docker buildx build；可选 PLATFORM=linux/amd64"
	@echo "make image-tar    # 构建后 docker save 为 TAR（默认 $(TAR)）"
	@echo "make export-linux-amd64-zip # 导出 linux/amd64 镜像 tar + .env.example + .docker-compose.yml + 启停脚本并打 zip"
	@echo "make compose     # compose 起栈（发布端口见 docker-compose.yml，默认 38419）"
	@echo "make compose-down # $(DOCKER_COMPOSE) down"
	@echo "make env        # cp .env.example .env（若不存在；compose 需要 .env）"
	@echo "make clean      # 删 $(VENV)"
	@echo ""
	@echo "变量: PORT=$(PORT)  IMAGE=$(IMAGE)  PLATFORM=$(PLATFORM)  IMAGE_OUTPUT=$(IMAGE_OUTPUT)  TAR=$(TAR)"

$(VENV)/bin/pip:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -U pip -r requirements.txt

install: $(VENV)/bin/pip

run: install
	$(VENV)/bin/uvicorn app:app --host 0.0.0.0 --port $(PORT)

run-dev: install
	$(VENV)/bin/uvicorn app:app --host 0.0.0.0 --port $(PORT) --reload

run-pt: install
	MODE=passthrough $(VENV)/bin/uvicorn app:app --host 0.0.0.0 --port $(PORT)

run-pt-dev: install
	MODE=passthrough $(VENV)/bin/uvicorn app:app --host 0.0.0.0 --port $(PORT) --reload

env:
	@test -f .env && echo ".env 已存在，跳过" \
		|| (cp .env.example .env && echo "已创建 .env，请编辑")

image:
ifeq ($(strip $(PLATFORM)),)
	$(BUILDX) $(IMAGE_OUTPUT) -t $(IMAGE) .
else
	$(BUILDX) --platform $(PLATFORM) $(IMAGE_OUTPUT) -t $(IMAGE) .
endif

image-tar: image
	docker save $(IMAGE) -o $(TAR)
	@echo "已写入 $(TAR) ，另一台机导入: docker load -i $(TAR)"

export-linux-amd64-zip:
	@mkdir -p "$(DIST_DIR)/$(EXPORT_NAME)"
	@$(MAKE) image-tar PLATFORM=linux/amd64 TAR="$(DIST_DIR)/$(EXPORT_NAME)/$(EXPORT_NAME).image.tar"
	@cp .env.example "$(DIST_DIR)/$(EXPORT_NAME)/.env.example"
	@cp docker-compose.yml "$(DIST_DIR)/$(EXPORT_NAME)/docker-compose.yml"
	@cp scripts/start-server.sh "$(DIST_DIR)/$(EXPORT_NAME)/start-server.sh"
	@cp scripts/stop-server.sh "$(DIST_DIR)/$(EXPORT_NAME)/stop-server.sh"
	@chmod +x "$(DIST_DIR)/$(EXPORT_NAME)/start-server.sh" "$(DIST_DIR)/$(EXPORT_NAME)/stop-server.sh"
	@rm -f "$(DIST_DIR)/$(EXPORT_NAME).zip"
	@cd "$(DIST_DIR)" && zip -qr "$(EXPORT_NAME).zip" "$(EXPORT_NAME)"
	@echo "已打包: $(DIST_DIR)/$(EXPORT_NAME).zip"

compose:
	@test -f .env || (echo "缺少 .env，请先: make env  或  cp .env.example .env" && exit 1)
	$(DOCKER_COMPOSE) up -d --build
	@docker ps --filter "name=coze-openai-gateway" --format "  {{.Names}}\t{{.Ports}}" 2>/dev/null | head -5

compose-down:
	$(DOCKER_COMPOSE) down

clean:
	rm -rf $(VENV)

.DEFAULT_GOAL := help
