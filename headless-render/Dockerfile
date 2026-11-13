# 独立 headless 渲染服务镜像（137 国内服务器友好版）
#
# 背景：原方案 FROM mcr.microsoft.com/playwright/python 在 137 上拉不动——
#   mcr 的镜像层 blob CDN 在该国内服务器上不可达/极慢（70s 仅走到 Pulling fs layer），
#   且 DockerHub(registry-1.docker.io) 同样不可达。
# 改法：复用已在 137 本地的 edu-web-backend:real（Debian 13 / Python 3.11 / apt 可用）
#   作基础镜像，零外网拉取；playwright 经阿里云 pip 镜像安装，chromium 浏览器二进制
#   经 npmmirror(cdn.npmmirror.com) 安装。
#
# 部署顺序约束：本镜像依赖同机已存在 edu-web-backend:real，须先发 web 再构建 headless。
# 非国内/可直连 mcr 的环境可改回 FROM mcr.microsoft.com/playwright/python:v1.49.0
#   （此时无需 PIP_INDEX_URL / PLAYWRIGHT_DOWNLOAD_HOST 两个环境变量）。
FROM edu-web-backend:real

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright \
    RENDER_TIMEOUT=25

WORKDIR /app

# fastapi / uvicorn 已由 web-backend 基础镜像提供；这里仅装 playwright 与 chromium 运行时。
# --with-deps 通过 apt 安装 chromium 所需的系统库（libnss3 等），web-backend 基础镜像基于
# Debian 13 且构建时即经 apt 装过依赖，故 apt 源可达。
RUN pip install --no-cache-dir playwright \
    && playwright install --with-deps chromium

COPY app.py ./

# web-backend 基础镜像默认以 root 运行；chromium 已加 --no-sandbox，root 可接受
# （本服务仅内网可达，由 web-backend 经 edu-net 调用，不暴露宿主机端口）。
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
