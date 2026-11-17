# TripAgent API 镜像（B13：Windows 本地与 VPS 行为对齐，依赖 Docker）
FROM python:3.11-slim

WORKDIR /app

# 先装依赖层（利用缓存）：只拷贝 pyproject，避免源码变动触发重装
# 默认走阿里云镜像（国内构建机直连可用）；海外构建传 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY pyproject.toml ./
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} .

# 应用代码；数据集与 SQLite 通过 compose 卷挂载 ./data，不打进镜像
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# 版本可追溯（§11.4）：构建时注入，--build-arg GIT_SHA=$(git rev-parse --short HEAD)
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# 启动即迁移，再起服务（一键复现，G5）
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
