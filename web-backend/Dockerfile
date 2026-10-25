FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# 阿里云 PyPI 镜像：137 直连 pypi.org 仅 ~25kB/s，全量依赖要 30min+；
# 与 gateway Dockerfile 的 alpine 镜像源切换同 convention。
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com \
    && pip install --no-cache-dir -r requirements.txt
COPY . .
# 端口必须为 8000：网关 config.yaml 的上游写死 http://web-backend:8000
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
