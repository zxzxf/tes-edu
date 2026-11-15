FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# 国内 137 直连 deb.debian.org / pypi.org 极慢（apt-get update 会挂死），
# apt 与 pip 均切国内镜像，与 gateway Dockerfile 的 alpine 源切换同 convention。
# Debian trixie 用 DEB822 格式 /etc/apt/sources.list.d/debian.sources；旧版在 /etc/apt/sources.list。
RUN sed -i 's|deb.debian.org/|mirrors.tencent.com/|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org/|mirrors.tencent.com/|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/* \
    && pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip config set global.trusted-host mirrors.aliyun.com \
    && pip install --no-cache-dir -r requirements.txt
COPY . .
# 端口必须为 8000：网关 config.yaml 的上游写死 http://web-backend:8000
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
