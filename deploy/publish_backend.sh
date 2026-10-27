#!/usr/bin/env bash
# 一键发版脚本（本机 → 137）：打包源码 → 上传 → 构建 :real 镜像 → 精确重建容器
#
# 用法：
#   SSH_KEY=<本机私钥路径> SSH_HOST=<ssh 目标> bash deploy/publish_backend.sh web|agent|front|student|all
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
#   7. 学生端 edu-student-web（base/router 均为 /student/）与 edu-lb 的 /student/
#      静态路由是一对：必须【先起容器、再 reload edu-lb】。顺序反了 nginx -t 会因
#      proxy_pass http://student-web:80 解析不到主机名而失败，reload 直接报错。
#      另：容器别名必须含 student-web（edu-lb 配置写死该上游名）。
#   8. 网关 gateway 的 config.yaml 是【打进镜像】的（Dockerfile COPY → /app/config.yaml），
#      不是 bind mount，改路由必须重建镜像 + 换容器，改宿主机文件不生效。
#      且绝不能用 `docker compose build/up`：compose 解析整个 compose 文件时会要求
#      web-backend 的 JWT_PRIVATE_KEY 已 export（多行 PEM 无法进 .env），否则
#      插值失败 exit 15（2026-10-27 踩过）。正确做法：docker build 直接构建 +
#      docker run 重建 2 副本，env 从 secrets/jwt_public.pem 与 .env 取。
#      副本名 tes-edu-gateway-gateway-{2,3}，网络别名必须含 gateway（edu-lb upstream 写死）。
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

build_student() {
  local dir="/opt/tes-edu-student-web"
  local img="edu-student-web:real"
  local tgz="/tmp/student_rel.tgz"

  echo "==> [student] 打包学生端源码（排除 node_modules/dist）"
  tar --exclude='node_modules' --exclude='dist' -czf "${tgz}" -C "${LOCAL_ROOT}/student-web" .

  echo "==> [student] 上传 + 解板 + 构建镜像（node:20 编译 → nginx 托管）"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${tgz}" "${HOST}:/opt/student_rel.tgz"

  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       mkdir -p ${dir}; \
       cd ${dir} && rm -rf src index.html vite.config.js package.json package-lock.json nginx.conf Dockerfile .dockerignore README.md; \
       tar -xzf /opt/student_rel.tgz -C ${dir}; \
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

# 学生端重建 + edu-lb 静态路由同步。
# ⚠️ 顺序敏感：必须先起 student-web 容器（让 DNS 名 student-web 可解析），
#    再覆盖 nginx.conf 并 reload；否则 nginx -t 会因 proxy_pass 主机名解析失败而报错。
recreate_student() {
  echo "==> [student] 重建 edu-student-web"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       docker stop edu-student-web >/dev/null 2>&1 || true; \
       docker rm   edu-student-web >/dev/null 2>&1 || true; \
       docker run -d --name edu-student-web \
         --network ${NET} --network-alias student-web --network-alias edu-student-web \
         --restart unless-stopped \
         -p 127.0.0.1:8082:80 \
         edu-student-web:real; \
       echo 'started'"

  echo "==> [student] 同步 edu-lb nginx.conf（/student/ 静态路由）并 reload"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${LOCAL_ROOT}/deploy/nginx.conf" "${HOST}:/opt/nginx.conf.new" >/dev/null
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cp ${REMOTE_DIR}/nginx.conf ${REMOTE_DIR}/nginx.conf.bak-${BAK} 2>/dev/null || true; \
       cp /opt/nginx.conf.new ${REMOTE_DIR}/nginx.conf; \
       docker exec edu-lb nginx -t; \
       docker kill -s HUP edu-lb; \
       echo 'lb reloaded'"
}

# ---------- 2b. 网关（config.yaml 打进镜像，必须重建镜像+换容器） ----------
# 见踩坑第 8 条：不能用 docker compose build/up（JWT_PRIVATE_KEY 插值会 exit 15）。
GW_IMG="registry.cn-shanghai.aliyuncs.com/mis_ser/edu-gateway:latest"

build_gateway() {
  echo "==> [gateway] 同步 config.yaml（先备份线上旧配置）"
  scp -i "${KEY}" -o StrictHostKeyChecking=no "${LOCAL_ROOT}/gateway/config.yaml" "${HOST}:/opt/gw_config.yaml.new" >/dev/null

  echo "==> [gateway] 构建镜像（docker build，非 compose）"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       cp ${REMOTE_DIR}/gateway/config.yaml ${REMOTE_DIR}/gateway/config.yaml.bak-${BAK} 2>/dev/null || true; \
       cp /opt/gw_config.yaml.new ${REMOTE_DIR}/gateway/config.yaml; \
       docker tag ${GW_IMG} edu-gateway:bak-${BAK} 2>/dev/null || true; \
       docker build -t ${GW_IMG} ${REMOTE_DIR}/gateway 2>&1 | tail -3"
}

recreate_gateway() {
  echo "==> [gateway] 重建 2 副本 + reload edu-lb"
  ssh -i "${KEY}" -o StrictHostKeyChecking=no "${HOST}" \
      "set -e; \
       PEM=\$(cat ${REMOTE_DIR}/secrets/jwt_public.pem); \
       RP=\$(sed -n 's/^REDIS_PASSWORD=//p' ${REMOTE_DIR}/.env | head -1 | tr -d '\r' | tr -d '\"'); \
       for i in 2 3; do \
         name=tes-edu-gateway-gateway-\$i; \
         docker rm -f \$name >/dev/null 2>&1 || true; \
         docker run -d --name \$name \
           --network ${NET} --network-alias gateway \
           --restart unless-stopped \
           -v tes-edu-gateway_gateway-log:/var/log/gateway \
           -e REDIS_ENABLED=true -e REDIS_ADDR=redis:6379 \
           -e REDIS_PASSWORD=\"\$RP\" \
           -e CONFIG_PATH=/app/config.yaml \
           -e \"JWT_PUBLIC_KEY=\$PEM\" \
           -e GATEWAY_LOG=/var/log/gateway/access.log \
           ${GW_IMG}; \
         echo \"started \$name\"; \
       done; \
       sleep 6; \
       docker ps --filter name=tes-edu-gateway-gateway --format '{{.Names}} {{.Status}}'; \
       docker kill -s HUP edu-lb; \
       echo 'lb reloaded'"
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
       for c in edu-web-backend edu-agent-backend edu-web-front edu-student-web; do \
         echo \"-- \$c: \$(docker ps --filter name=\$c --format '{{.Status}}')\"; \
       done; \
       echo '-- web healthz'; \
       docker exec edu-web-backend python -c 'import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8000/healthz\",timeout=5).read().decode()[:60])' 2>&1 | tail -1; \
       echo '-- BYOM 端到端自检（经网关）'; \
       docker cp /tmp/_hc_byom.py edu-web-backend:/tmp/_hc_byom.py 2>/dev/null; \
       docker exec edu-web-backend python /tmp/_hc_byom.py 2>&1 | grep -E 'LOGIN|OK|FAIL'; \
       echo '-- web-front BYOM bundle'; \
       docker exec edu-web-front sh -c 'grep -l "byom/presets" /usr/share/nginx/html/assets/*.js >/dev/null 2>&1' && echo 'BYOM JS BUNDLED OK' || echo 'BYOM JS MISSING (前端需含 ModelConfig.vue)'; \
       echo '-- student-web 静态路由（经 edu-lb :80）'; \
       if docker ps --filter name=edu-student-web --format '{{.Names}}' | grep -q edu-student-web; then \
         curl -s -o /dev/null -w '/student/ -> HTTP %{http_code}\n' -m 8 http://127.0.0.1:80/student/; \
         curl -s -o /dev/null -w '/student/projects (SPA深链) -> HTTP %{http_code}\n' -m 8 http://127.0.0.1:80/student/projects; \
       else echo '(student-web 未部署则跳过)'; fi; \
       echo '-- 学生端 API 路由（经 edu-lb，无 token 期望 401；404=网关缺 /api/student/ 路由）'; \
       curl -s -o /dev/null -w '/api/student/features -> HTTP %{http_code}\n' -m 8 http://127.0.0.1:80/api/student/features; \
       curl -s -o /dev/null -w '/api/student/projects -> HTTP %{http_code}\n' -m 8 http://127.0.0.1:80/api/student/projects"
}

case "${1:-all}" in
  web)     build_backend web;  recreate_web ;;
  agent)   build_backend agent; recreate_agent ;;
  front)   build_front;        recreate_front ;;
  student) build_student;      recreate_student ;;
  gateway) build_gateway;      recreate_gateway ;;
  all)     build_backend web;  build_backend agent; build_front; build_student; build_gateway; \
           recreate_web; recreate_agent; recreate_front; recreate_student; recreate_gateway ;;
  *)       echo "用法: $0 web|agent|front|student|gateway|all"; exit 1 ;;
esac

healthcheck
echo "==> 发版完成（bak 标签 edu-*-backend:bak-${BAK} / edu-web-front:bak-${BAK} / edu-student-web:bak-${BAK}）"
