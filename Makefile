PY   ?= python3
PORT ?= 38419
VENV := .venv
# docker build 标签，与 README 示例一致
IMAGE ?= coze-openai-gateway
# make image-tar 输出（docker save）
TAR ?= coze-openai-gateway.image.tar
DOCKER_COMPOSE ?= docker compose

.PHONY: help install run run-dev run-pt run-pt-dev env image image-tar compose compose-down clean

help:
	@echo "make install    # $(VENV) + requirements.txt"
	@echo "make run        # 正式跑：uvicorn（改代码需手动重启）"
	@echo "make run-dev    # 开发：uvicorn --reload（改 .py 自动重启）"
	@echo "make run-pt     # passthrough：零配置启动（MODE=passthrough，不依赖 .env）"
	@echo "make run-pt-dev # passthrough + --reload"
	@echo "make image      # docker build，镜像名 IMAGE=$(IMAGE)"
	@echo "make image-tar    # 构建后 docker save 为 TAR（默认 $(TAR)）"
	@echo "make compose     # compose 起栈（发布端口见 docker-compose.yml，默认 38419）"
	@echo "make compose-down # $(DOCKER_COMPOSE) down"
	@echo "make env        # cp .env.example .env（若不存在；compose 需要 .env）"
	@echo "make clean      # 删 $(VENV)"
	@echo ""
	@echo "变量: PORT=$(PORT)  IMAGE=$(IMAGE)  TAR=$(TAR)"

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
	docker build -t $(IMAGE) .

image-tar: image
	docker save $(IMAGE) -o $(TAR)
	@echo "已写入 $(TAR) ，另一台机导入: docker load -i $(TAR)"

compose:
	@test -f .env || (echo "缺少 .env，请先: make env  或  cp .env.example .env" && exit 1)
	$(DOCKER_COMPOSE) up -d --build
	@docker ps --filter "name=coze-openai-gateway" --format "  {{.Names}}\t{{.Ports}}" 2>/dev/null | head -5

compose-down:
	$(DOCKER_COMPOSE) down

clean:
	rm -rf $(VENV)

.DEFAULT_GOAL := help
