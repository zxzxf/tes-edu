#!/usr/bin/env bash
#
# tes-edu-gateway 统一启动脚本
# 把 "gateway 起 2 个副本" 固化进脚本，避免直接 `docker compose up` 时
# 忘记 --scale 而退化成单副本、丢失多副本高可用。
#
# 用法:
#   ./start.sh          正常启动 / 确保 2 副本（使用已有镜像，幂等，不重建）
#   ./start.sh build    重新构建 gateway 镜像后再启动（代码改动后用）
#
# 注意: 脚本须放在 docker-compose.yml 同级目录（部署目录根）下运行。
set -euo pipefail

# 进入脚本所在目录（即 docker-compose.yml 所在目录），无论从哪调用都正确
cd "$(dirname "$0")"

BUILD=""
if [ "${1:-}" = "build" ]; then
  BUILD="--build"
  echo "==> 模式: 重新构建 gateway 镜像"
fi

echo "==> 启动 tes-edu-gateway (gateway x2 + nginx + redis)"
docker compose up -d ${BUILD} --scale gateway=2

# 平滑重载 nginx，让它重新解析 gateway 服务 DNS，确保 2 个副本都进入 upstream。
# 若 edu-lb 尚未启动（首次部署），跳过即可。
if docker ps -q -f name=edu-lb | grep -q .; then
  echo "==> 重载 nginx (HUP) 以重新解析 gateway DNS"
  docker kill -s HUP edu-lb
fi

echo "==> 当前运行实例:"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'gateway|edu-lb|edu-redis' || true
