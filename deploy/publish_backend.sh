#!/usr/bin/env bash
# 后端发版脚本（本机 → 137）：打包源码 → 上传 → 构建镜像 → 交还 compose 换容器
#
# 用法：
#   SSH_KEY=<本机私钥路径> SSH_HOST=<ssh 目标> bash deploy/publish_backend.sh web|agent|all
#   例：SSH_KEY=/c/Users/<你>/.ssh/id_ed25519 SSH_HOST=137 bash deploy/publish_backend.sh all
#
# 约定（踩过坑，勿删）：
#   1. 2026-10-24 起改为「构建镜像 + compose 换容器」两段式。早期版本用 docker run
#      直接起独立容器，与 compose 争抢容器名（compose up 报 Conflict:
#      "container name /edu-agent-backend is already in use"）。现在容器统一归
#      /opt/tes-edu-gateway 的 compose 管理，本脚本只负责构建 :real 镜像。
#   2. 换装前自动给旧镜像打 :bak-YYYYMMDD 备份 tag；紧急回滚见 deploy/README.md。
#   3. CI（ACR）通道就绪前（仓库 Secrets ACR_USERNAME/ACR_PASSWORD 缺失，构建全败），
#      线上镜像来自本脚本的本地构建；Secrets 就绪后应改走 CI + IMAGE_TAG，本脚本退役。
#   4. web-backend 的 JWT_PRIVATE_KEY 是多行 PEM，不能进 env_file；换装时由
#      137 本地 secrets/jwt_private.pem 读入，经 compose 变量 substitution 注入。
#   5. agent 敏感环境变量（LLM_API_KEY 等）在 /opt/tes-edu-gateway/agent-backend.env，
#      不进镜像、不进 git。
set -euo pipefail

KEY="${SSH_KEY:?请设置 SSH_KEY（本机私钥路径）}"
HOST="${SSH_HOST:?请设置 SSH_HOST（ssh 目标，如 137）}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

build_one() {
  local svc="$1"            # web | agent
  local dir="/opt/tes-edu-${svc}"
  local img="edu-${svc}-backend:real"
  local tgz="/tmp/${svc}_rel.tgz"

  echo "==> [${svc}] 打包源码"
  tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.git' \
      -czf "${tgz}" -C "${LOCAL_ROOT}/${svc}-backend" .

  echo "==> [${svc}] 上传 + 解板 + 构建镜像（不换容器）"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${tgz}" "${HOST}:/opt/${svc}_rel.tgz"

  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cd ${dir} && rm -rf app data main.py requirements.txt Dockerfile .dockerignore .env.example tests README.md; \
       tar -xzf /opt/${svc}_rel.tgz -C ${dir}; \
       docker tag ${img} ${img%:*}:bak-\$(date +%Y%m%d) 2>/dev/null || true; \
       docker build -t ${img} ${dir} 2>&1 | tail -2"
}

recreate_via_compose() {
  # 容器换装交还 compose（服务名 agent-backend / web-backend，未变动的服务不会重建）。
  # 三个密钥/变量均从 137 本地读取，不在本机落地：
  #   JWT_PUBLIC_KEY   secrets/jwt_public.pem   （网关验签）
  #   JWT_PRIVATE_KEY  secrets/jwt_private.pem  （web-backend 签发，对应上面公钥）
  #   REDIS_PASSWORD   .env 首行               （限流令牌桶）
  #   VENDOR_CREDENTIAL_KEY  .env（T-AM6 厂商凭证加密；缺省空串→后端从 JWT 派生并告警）
  echo "==> compose 换容器 + 健康检查"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; cd /opt/tes-edu-gateway && \
       JWT_PUBLIC_KEY=\"\$(cat secrets/jwt_public.pem)\" \
       JWT_PRIVATE_KEY=\"\$(cat secrets/jwt_private.pem)\" \
       REDIS_PASSWORD=\"\$(sed -n 's/^REDIS_PASSWORD=//p' .env)\" \
       VENDOR_CREDENTIAL_KEY=\"\$(sed -n 's/^VENDOR_CREDENTIAL_KEY=//p' .env)\" \
       docker compose up -d agent-backend web-backend; \
       sleep 5; \
       for c in edu-agent-backend edu-web-backend; do \
         echo \"-- \$c\"; \
         docker exec \$c python -c 'import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8000/healthz\").read().decode())'; \
       done"
}

case "${1:-all}" in
  web)   build_one web ;;
  agent) build_one agent ;;
  all)   build_one web; build_one agent ;;
  *)     echo "用法: $0 web|agent|all" ; exit 1 ;;
esac
recreate_via_compose

echo "==> 发版完成"
