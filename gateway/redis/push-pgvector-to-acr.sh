#!/usr/bin/env bash
# 把 106 服务器上的 pgvector/pgvector:pg16 推送到阿里云容器镜像仓库（ACR）
# 前置：在 106 服务器已执行  docker login registry.cn-shanghai.aliyuncs.com
# 用法：
#   ACR_NAMESPACE=mis_ser ACR_REPO=pgvector ACR_TAG=pg16 bash push-pgvector-to-acr.sh
set -euo pipefail

REGISTRY="${ACR_REGISTRY:-registry.cn-shanghai.aliyuncs.com}"
NAMESPACE="${ACR_NAMESPACE:-mis_ser}"
REPO="${ACR_REPO:-pgvector}"
TAG="${ACR_TAG:-pg16}"
SRC="pgvector/pgvector:pg16"
DST="${REGISTRY}/${NAMESPACE}/${REPO}:${TAG}"

echo ">> tag ${SRC} -> ${DST}"
docker tag "${SRC}" "${DST}"
echo ">> push ${DST}"
docker push "${DST}"
echo "done: ${DST}"
