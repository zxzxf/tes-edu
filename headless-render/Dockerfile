# 独立 headless 渲染服务镜像
#
# 基础镜像 mcr.microsoft.com/playwright/python 已预装 Python + playwright + chromium
# 及全部系统依赖（libnss3 / libatk / etc.），无需再 apt-get 几百 MB 依赖，
# 这是最小且稳定的做法。
FROM mcr.microsoft.com/playwright/python:v1.49.0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    RENDER_TIMEOUT=25

WORKDIR /app

# 仅装应用依赖（playwright/chromium 已在基础镜像内）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# 非 root 运行（容器安全）
RUN useradd -m -u 10001 render && chown -R render:render /app
USER render

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
