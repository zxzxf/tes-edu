#!/usr/bin/env bash
# 一键发版脚本（本机 → 137）：打包源码 → 上传 → 构建 :real 镜像 → 精确重建容器
#
# 用法：
#   SSH_KEY=<本机私钥路径> SSH_HOST=<ssh 目标> bash deploy/publish_backend.sh web|agent|front|all
#   例：SSH_KEY=E:/137服务器/id_ed25519 SSH_HOST=137 bash deploy/publish_backend.sh all
#   简写（读默认私钥）：bash deploy/publish_backend.sh all
#
# 踩过的坑（勿删，每条都踩过）：
#   1. 2026-10-23 起容器必须用「网络别名」重建：网关 config.yaml 上游写死
#      http://web-backend:8000 / http://agent-backend:8000，但容器名是
#      edu-web-backend / edu-agent-backend。docker run 重建时务必带
#      --network-alias web-backend（agent 同理），否则网关 DNS 解析 bad address、
#      整条链路断。
#   2. 网络名是 tes-edu-gateway_edu-net（compose 项目名前缀 tes-edu-gateway），
#      不是 docker-compose.yml 里写的 edu-net（那是相对名，compose 会加前缀）。
#      用错网络名会导致容器不在同一网络、网关连不上后端。
#   3. web-backend 的 JWT_PRIVATE_KEY 是多行 PEM，不能进 env_file，也绝不能走
#      `docker compose up`（compose 变量 substitution 无法正确处理多行 PEM →
#      2026-10-27 因此漏注入私钥，登录签不出 token、所有 /api/admin/* 401）。
#      改为 docker run -e "JWT_PRIVATE_KEY=$(cat secrets/jwt_private.pem)" 注入。
#   4. 换装前自动给旧 :real 镜像打 :bak-YYYYMMDD 标签留回滚点；紧急回滚：
#      docker tag edu-*-backend:bak-YYYYMMDD edu-*-backend:real && 重建容器。
#   5. agent 敏感环境变量（LLM_API_KEY 等）在 /opt/tes-edu-gateway/agent-backend.env，
#      web 的 DATABASE_URL/KAFKA_* + VENDOR_CREDENTIAL_KEY 在 web-backend.env，
#      二者均 600 权限、不进镜像、不进 git。
#   6. 前端 edu-web-front 是 nginx 静态托管，base/router 均为 /admin/；BYOM 页面
#      （ModelConfig.vue + api/byom.js）必须进构建，否则浏览器访问 model-config
#      拿不到厂商列表（旧前端 404 → 前端 fallback “厂商id不存在”）。
set -euo pipefail

KEY="${SSH_KEY:-E:/137服务器/id_ed25519}"
HOST="${SSH_HOST:-137}"
BAK="$(date +%Y%m%d)"
REMOTE_DIR="/opt/tes-edu-gateway"
LOCAL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NET="tes-edu-gateway_edu-net"

# ---------- 1. 上传 + 构建镜像（不换容器） ----------
build_backend() {
  local svc="$1"            # web | agent
  local dir="/opt/tes-edu-${svc}"
  local img="edu-${svc}-backend:real"
  local tgz="/tmp/${svc}_rel.tgz"

  echo "==> [${svc}] 打包源码"
  tar --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.git' \
      -czf "${tgz}" -C "${LOCAL_ROOT}/${svc}-backend" .

  echo "==> [${svc}] 上传 + 解板 + 构建镜像"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${tgz}" "${HOST}:/opt/${svc}_rel.tgz"

  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cd ${dir} && rm -rf app data main.py requirements.txt Dockerfile .dockerignore .env.example tests README.md; \
       tar -xzf /opt/${svc}_rel.tgz -C ${dir}; \
       docker tag ${img} ${img%:*}:bak-${BAK} 2>/dev/null || true; \
       docker build -t ${img} ${dir} 2>&1 | tail -3"
}

build_front() {
  local dir="/opt/tes-edu-web-front"
  local img="edu-web-front:real"
  local tgz="/tmp/webfront_rel.tgz"

  echo "==> [front] 打包前端源码（排除 node_modules/dist）"
  tar --exclude='node_modules' --exclude='dist' -czf "${tgz}" -C "${LOCAL_ROOT}/web-frontend" .

  echo "==> [front] 上传 + 解板 + 构建镜像（node:20 编译 → nginx 托管）"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${tgz}" "${HOST}:/opt/webfront_rel.tgz"

  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cd ${dir} && rm -rf src index.html vite.config.js package.json package-lock.json nginx.conf Dockerfile .dockerignore README.md; \
       tar -xzf /opt/webfront_rel.tgz -C ${dir}; \
       docker tag ${img} ${img%:*}:bak-${BAK} 2>/dev/null || true; \
       docker build -t ${img} ${dir} 2>&1 | tail -3"
}

# ---------- 2. 精确重建容器（替换 ip/别名/CMD 用已核对的固定值） ----------
recreate_web() {
  echo "==> [web] 重建 edu-web-backend（注入 JWT 私钥 + VENDOR 密钥）"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       PEM=\$(cat ${REMOTE_DIR}/secrets/jwt_private.pem); \
       VCK=\$(sed -n 's/^VENDOR_CREDENTIAL_KEY=//p' ${REMOTE_DIR}/web-backend.env); \
       docker stop edu-web-backend >/dev/null 2>&1 || true; \
       docker rm   edu-web-backend >/dev/null 2>&1 || true; \
       docker run -d --name edu-web-backend \
         --network ${NET} --network-alias web-backend --network-alias edu-web-backend \
         --restart unless-stopped \
         --env-file ${REMOTE_DIR}/web-backend.env \
         -e \"JWT_PRIVATE_KEY=\${PEM}\" \
         -e \"VENDOR_CREDENTIAL_KEY=\${VCK}\" \
         edu-web-backend:real uvicorn main:app --host 0.0.0.0 --port 8000; \
       echo 'started'"
}

recreate_agent() {
  echo "==> [agent] 重建 edu-agent-backend"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       docker stop edu-agent-backend >/dev/null 2>&1 || true; \
       docker rm   edu-agent-backend >/dev/null 2>&1 || true; \
       docker run -d --name edu-agent-backend \
         --network ${NET} --network-alias agent-backend --network-alias edu-agent-backend \
         --restart unless-stopped \
         --env-file ${REMOTE_DIR}/agent-backend.env \
         edu-agent-backend:real uvicorn main:app --host 0.0.0.0 --port 8000; \
       echo 'started'"
}

recreate_front() {
  echo "==> [front] 重建 edu-web-front"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       docker stop edu-web-front >/dev/null 2>&1 || true; \
       docker rm   edu-web-front >/dev/null 2>&1 || true; \
       docker run -d --name edu-web-front \
         --network ${NET} --network-alias web-front --network-alias edu-web-front \
         --restart unless-stopped \
         edu-web-front:real; \
       echo 'started'"
}

# ---------- 3. 健康检查 + BYOM 关键路由自检 ----------
healthcheck() {
  echo "==> 健康检查 + BYOM 自检"
  # 用文件脚本避免 heredoc 一行 python 的语法陷阱；走 gateway 验证完整鉴权链。
  cat > /tmp/_hc_byom.py <<'PY'
import urllib.request, json, sys
BACK="http://127.0.0.1:8000"; GW="http://gateway:8080"
try:
    tok=json.loads(urllib.request.urlopen(urllib.request.Request(BACK+"/login",
        data=json.dumps({"username":"admin@tes.edu","password":"demo1234"}).encode(),
        headers={"Content-Type":"application/json"}, method="POST"), timeout=10).read().decode())["access_token"]
    print("LOGIN_OK token_len=", len(tok))
except Exception as e:
    print("LOGIN_FAIL", e); sys.exit(1)
for path in ["/api/admin/byom/presets","/api/admin/byom/config"]:
    try:
        r=urllib.request.urlopen(urllib.request.Request(GW+path, headers={"Authorization":"Bearer "+tok}), timeout=10)
        body=json.loads(r.read().decode())
        print("OK", path, ("count="+str(len(body))) if isinstance(body,list) else str(body)[:120])
    except urllib.error.HTTPError as e:
        print("FAIL", path, e.code, e.read().decode()[:160])
PY
  scp -i "${KEY}" -o StrictHostKeyChecking=no /tmp/_hc_byom.py "${HOST}:/tmp/_hc_byom.py" >/dev/null 2>&1
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       sleep 6; \
       for c in edu-web-backend edu-agent-backend edu-web-front; do \
         echo \"-- \$c: \$(docker ps --filter name=\$c --format '{{.Status}}')\"; \
       done; \
       echo '-- web healthz'; \
       docker exec edu-web-backend python -c 'import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8000/healthz\",timeout=5).read().decode()[:60])' 2>&1 | tail -1; \
       echo '-- BYOM 端到端自检（经网关）'; \
       docker cp /tmp/_hc_byom.py edu-web-backend:/tmp/_hc_byom.py 2>/dev/null; \
       docker exec edu-web-backend python /tmp/_hc_byom.py 2>&1 | grep -E 'LOGIN|OK|FAIL'; \
       echo '-- web-front BYOM bundle'; \
       docker exec edu-web-front grep -rl 'byom-form' /usr/share/nginx/html/assets/*.js >/dev/null 2>&1 && echo 'BYOM JS BUNDLED OK' || echo 'BYOM JS MISSING (前端需含 ModelConfig.vue)'"
}

case "${1:-all}" in
  web)    build_backend web;  recreate_web ;;
  agent)  build_backend agent; recreate_agent ;;
  front)  build_front;        recreate_front ;;
  all)    build_backend web;  build_backend agent; build_front; \
          recreate_web; recreate_agent; recreate_front ;;
  *)      echo "用法: $0 web|agent|front|all"; exit 1 ;;
esac

healthcheck
echo "==> 发版完成（bak 标签 edu-*-backend:bak-${BAK} / edu-web-front:bak-${BAK}）"
