@echo off
REM 把官方 redis:7-alpine 镜像推送到阿里云容器镜像仓库（ACR）
REM 前置：在 137 服务器先执行  docker login registry.cn-shanghai.aliyuncs.com
REM 用法（PowerShell/CMD）：
REM   set ACR_NAMESPACE=mis_ser && set ACR_REPO=redis && set ACR_TAG=7-alpine && push-to-acr.bat
setlocal
set "REGISTRY=%ACR_REGISTRY%"
if "%REGISTRY%"=="" set "REGISTRY=registry.cn-shanghai.aliyuncs.com"
set "NAMESPACE=%ACR_NAMESPACE%"
if "%NAMESPACE%"=="" set "NAMESPACE=mis_ser"
set "REPO=%ACR_REPO%"
if "%REPO%"=="" set "REPO=redis"
set "TAG=%ACR_TAG%"
if "%TAG%"=="" set "TAG=7-alpine"

set "SRC=redis:7-alpine"
set "DST=%REGISTRY%/%NAMESPACE%/%REPO%:%TAG%"

echo pull %SRC%
docker pull %SRC%
echo tag %SRC% -^> %DST%
docker tag %SRC% %DST%
echo push %DST%
docker push %DST%
echo done: %DST%
endlocal
