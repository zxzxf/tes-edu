#!/usr/bin/env bash
# redis-repl-check.sh —— 106 侧跨云主从（137 主 → 106 从）健康探针
# 供 cron 每分钟执行并落日志；exit 0=健康，1=复制异常，2=环境异常。
# 密码从 /opt/deploy/.env 读取，不写入本脚本（避免落盘扩散）。
set -uo pipefail

ENV_FILE=/opt/deploy/.env
MASTER_HOST=124.221.253.137
MASTER_PORT=6379
LAG_THRESHOLD=10000   # 字节；7.4ms RTT 下正常 lag 为 0，超 10k 视为异常

[ -r "$ENV_FILE" ] || { echo "$(date '+%F %T') ENV_UNREADABLE"; exit 2; }
PW_106=$(grep -E "^REDIS_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
PW_137=$(grep -E "^MASTER_REDIS_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
[ -n "$PW_106" ] && [ -n "$PW_137" ] || { echo "$(date '+%F %T') ENV_KEY_MISSING"; exit 2; }

RC="docker exec srv-redis redis-cli"

# 从库侧：链路状态 + 已应用 offset
rep=$($RC -a "$PW_106" --no-auth-warning INFO replication 2>/dev/null | tr -d "\r")
st=$(echo "$rep" | grep -oE "^master_link_status:[a-z]+" | cut -d: -f2)
soff=$(echo "$rep" | grep -oE "^slave_repl_offset:[0-9]+" | cut -d: -f2)

# 主库侧 offset：经从库容器出站查询（宿主机无 redis-cli，且该路径即复制链路本身）
mst=$($RC -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$PW_137" --no-auth-warning INFO replication 2>/dev/null | tr -d "\r")
moff=$(echo "$mst" | grep -oE "^master_repl_offset:[0-9]+" | head -1 | cut -d: -f2)

if [ "${st:-}" != "up" ]; then
    echo "$(date '+%F %T') REPL_BROKEN st=${st:-unknown}"; exit 1
fi
if [ -z "$soff" ] || [ -z "$moff" ]; then
    echo "$(date '+%F %T') REPL_UNKNOWN soff=${soff:-null} moff=${moff:-null}"; exit 1
fi
lag=$((moff - soff))
if [ "$lag" -ge "$LAG_THRESHOLD" ]; then
    echo "$(date '+%F %T') REPL_LAG lag=$lag"; exit 1
fi
echo "$(date '+%F %T') OK st=up lag=$lag"
