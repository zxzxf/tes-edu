#!/usr/bin/env bash
# 把官方 redis:7-alpine 镜像推送到阿里云容器镜像仓库（ACR）
# 前置：在 137 服务器先执行  docker login registry.cn-shanghai.aliyuncs.com
# 用法：
#   ACR_NAMESPACE=mis_ser ACR_REPO=redis ACR_TAG=7-alpine bash push-to-acr.sh
set -euo pipefail

REGISTRY="${ACR_REGISTRY:-registry.cn-shanghai.aliyuncs.com}"
NAMESPACE="${ACR_NAMESPACE:-mis_ser}"
REPO="${ACR_REPO:-redis}"
TAG="${ACR_TAG:-7-alpine}"
SRC="redis:7-alpine"
DST="${REGISTRY}/${NAMESPACE}/${REPO}:${TAG}"

echo ">> pull ${SRC}"
docker pull "${SRC}"
echo ">> tag ${SRC} -> ${DST}"
docker tag "${SRC}" "${DST}"
echo ">> push ${DST}"
docker push "${DST}"
echo "done: ${DST}"
