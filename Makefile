PY   ?= python3
PORT ?= 38419
VENV := .venv

.PHONY: help install run run-dev run-pt run-pt-dev env clean

help:
	@echo "make install    # $(VENV) + requirements.txt"
	@echo "make run        # 正式跑：uvicorn（改代码需手动重启）"
	@echo "make run-dev    # 开发：uvicorn --reload（改 .py 自动重启）"
	@echo "make run-pt     # passthrough：零配置启动（MODE=passthrough，不依赖 .env）"
	@echo "make run-pt-dev # passthrough + --reload"
	@echo "make env        # cp .env.example .env（若不存在）"
	@echo "make clean      # 删 $(VENV)"
	@echo ""
	@echo "变量: PORT=$(PORT)"

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

clean:
	rm -rf $(VENV)

.DEFAULT_GOAL := help
