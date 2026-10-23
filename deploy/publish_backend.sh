#!/usr/bin/env bash
# 后端发版脚本（本机 → 137）：打包源码 → 上传 → 构建镜像 → 换容器
#
# 用法：
#   bash deploy/publish_backend.sh web
#   bash deploy/publish_backend.sh agent
#   bash deploy/publish_backend.sh all
#
# 约定（踩过坑，勿删）：
#   1. 容器必须在 tes-edu-gateway_edu-net 网络里，并带 --network-alias web-backend /
#      agent-backend —— 网关 config.yaml 的上游写死 http://web-backend:8000，
#      别名丢了会导致网关 DNS 解析失败（2026-10-23 踩过）。
#   2. 换容器前先给当前镜像打 :bak-YYYYMMDD 备份 tag，便于回滚。
#   3. agent 的敏感环境变量（LLM_API_KEY 等）由 /opt/tes-edu-agent.runtime.env 注入，
#      不进镜像、不进 git。
set -euo pipefail

KEY="${SSH_KEY:-/e/137服务器/id_ed25519}"
HOST="${SSH_HOST:-root@124.221.253.137}"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

publish_one() {
  local svc="$1"            # web | agent
  local dir="/opt/tes-edu-${svc}"
  local img="edu-${svc}-backend:real"
  local cname="edu-${svc}-backend"
  local alias="${svc}-backend"
  local tgz="/tmp/${svc}_rel.tgz"

  echo "==> [${svc}] 打包源码"
  tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.git' \
      -czf "${tgz}" -C "${LOCAL_ROOT}/${svc}-backend" .

  echo "==> [${svc}] 上传 + 构建 + 换容器"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${tgz}" "${HOST}:/opt/${svc}_rel.tgz"

  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cd ${dir} && rm -rf app data main.py requirements.txt Dockerfile .dockerignore .env.example tests README.md; \
       tar -xzf /opt/${svc}_rel.tgz -C ${dir}; \
       docker tag ${img} ${img%:*}:bak-\$(date +%Y%m%d) 2>/dev/null || true; \
       docker build -t ${img} ${dir}; \
       docker rm -f ${cname} >/dev/null 2>&1 || true; \
       if [ '${svc}' = 'agent' ]; then \
         docker run -d --name ${cname} --network tes-edu-gateway_edu-net --network-alias ${alias} \
           --restart unless-stopped --env-file /opt/tes-edu-agent.runtime.env ${img}; \
       else \
         docker run -d --name ${cname} --network tes-edu-gateway_edu-net --network-alias ${alias} \
           --restart unless-stopped \
           -e DATABASE_URL='postgresql+asyncpg://tes:Tes%40Pg%23106@106.12.25.5:5432/tes_edu' \
           -e KAFKA_BOOTSTRAP='kafka:9092' -e KAFKA_TOPIC_INGEST='course_kb_ingest' \
           -e KAFKA_GROUP='course-kb-ingest' ${img}; \
       fi; \
       sleep 8; \
       docker exec ${cname} python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())\"; \
       docker exec tes-edu-gateway-gateway-2 sh -c 'wget -qO- http://${alias}:8000/healthz'"

  echo "==> [${svc}] 发版完成"
}

case "${1:-all}" in
  web)   publish_one web ;;
  agent) publish_one agent ;;
  all)   publish_one web; publish_one agent ;;
  *)     echo "用法: $0 web|agent|all" ; exit 1 ;;
esac
