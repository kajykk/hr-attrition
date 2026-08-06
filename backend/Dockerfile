FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 系统依赖（asyncpg/bcrypt/lightgbm 编译需要）
# 换国内 apt 镜像源（官方 deb.debian.org 在国内网络极慢，会拖垮构建）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/*.sources 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制 pyproject.toml 安装依赖（利用 Docker 层缓存）
COPY pyproject.toml ./
# 创建临时包结构使 pip install 可解析（避免源码未到位导致失败）
RUN mkdir -p app && echo "" > app/__init__.py && \
    pip install --upgrade pip && \
    pip install ".[serving]" && \
    rm -rf app

# 复制源码 + 模型工件
COPY . /app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
