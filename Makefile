.PHONY: help dev up down build start stop logs ps migrate migrate-test test backend-test frontend-install frontend-dev lint clean

# HRA 项目便捷命令（D10 3.2）

help:  ## 显示帮助
	@echo "HRA 项目命令："
	@echo "  make dev              启动本地开发（后端 + 前端并行）"
	@echo "  make up               启动全部 Docker 服务"
	@echo "  make down             停止全部 Docker 服务"
	@echo "  make build            构建全部镜像"
	@echo "  make migrate          执行数据库迁移"
	@echo "  make test             运行后端测试"
	@echo "  make frontend-install 安装前端依赖"
	@echo "  make frontend-dev     启动前端开发服务器"
	@echo "  make lint             运行 lint"
	@echo "  make clean            清理生成产物"

dev:  ## 启动本地开发（需先 install 后端依赖 + 前端依赖）
	@echo "==> 启动后端（uvicorn，端口 8000）"
	cd backend && uvicorn app.main:app --reload --port 8000 &
	@echo "==> 启动前端（vite，端口 5173）"
	cd frontend && npm run dev

up:  ## 启动全部 Docker 服务
	docker compose up -d

down:  ## 停止全部 Docker 服务
	docker compose down

build:  ## 构建全部镜像
	docker compose build

start: up  ## 启动（同 up）
stop: down  ## 停止（同 down）

logs:  ## 查看日志
	docker compose logs -f --tail=100

ps:  ## 查看服务状态
	docker compose ps

migrate:  ## 执行数据库迁移（需 Docker 服务运行）
	cd backend && alembic upgrade head

migrate-gen:  ## 生成迁移脚本（用法：make migrate-gen m="create tables"）
	cd backend && alembic revision --autogenerate -m "$(m)"

test: backend-test  ## 运行后端测试

backend-test:  ## 运行后端 pytest
	cd backend && pytest -v

frontend-install:  ## 安装前端依赖
	cd frontend && npm install

frontend-dev:  ## 启动前端开发服务器
	cd frontend && npm run dev

frontend-build:  ## 构建前端产物
	cd frontend && npm run build

lint:  ## 运行 lint
	cd backend && ruff check app tests
	cd frontend && npm run lint

clean:  ## 清理生成产物
	rm -rf backend/__pycache__ backend/app/__pycache__ backend/**/__pycache__
	rm -rf backend/.pytest_cache backend/.ruff_cache
	rm -rf frontend/node_modules frontend/dist
	rm -rf backend/dist backend/build
