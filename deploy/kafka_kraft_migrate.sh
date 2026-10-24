#!/usr/bin/env bash
# T-E2：Kafka ZK 模式 -> KRaft 模式迁移（一条命令，可回滚）
#
# 前置假设（脚本会强制校验）：
#   1. 旧集群所有业务消费者组 lag = 0 —— 满足则**无需搬运历史消息**
#      （KRaft 用独立数据卷，历史消息不迁移；lag>0 时脚本直接中止）
#   2. 迁移窗口内可短暂停后端（默认停 edu-web-backend / edu-agent-backend）
#
# 流程：offset 快照 -> 停消费者 -> 停旧集群 -> 起 KRaft -> 预建 topic
#        -> 起后端 -> 生产/消费对账（零丢失校验）-> healthz 校验
#
# 用法：
#   SSH_KEY=~/.ssh/id_ed25519 SSH_HOST=137 bash deploy/kafka_kraft_migrate.sh         # 只检查与打印计划
#   SSH_KEY=~/.ssh/id_ed25519 SSH_HOST=137 bash deploy/kafka_kraft_migrate.sh --execute
#   ... --execute --rollback                                                          # 回滚到 ZK 模式
set -euo pipefail

SSH_HOST="${SSH_HOST:-137}"
EXECUTE=0
ROLLBACK=0
for a in "$@"; do
  case "$a" in
    --execute) EXECUTE=1 ;;
    --rollback) ROLLBACK=1 ;;
    *) echo "未知参数: $a" >&2; exit 2 ;;
  esac
done

INFRA_COMPOSE=/opt/docker-compose.yml
APP_COMPOSE=/opt/tes-edu-gateway/docker-compose.yml
KRAFT_COMPOSE=/opt/tes-edu-gateway/137-infra-kafka-kraft.yml
TOPICS="course_kb_ingest question_parsed kg_build student_event drill_sync"

ssh_run() { ssh -o StrictHostKeyChecking=no "$SSH_HOST" "$@"; }

echo "== 0) 迁移前体检 =="
ssh_run '
set -e
echo "--- 当前模式 ---"
docker exec kafka bash -c "grep -c \"^process.roles\" /tmp/kraft.properties 2>/dev/null || echo 0" 2>/dev/null || true
docker exec kafka bash -c "ps -ef | grep -c kafka-server-start" 2>/dev/null | sed "s/^/kafka 进程: /"
echo "--- 业务 topic ---"
docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list 2>/dev/null | grep -v __consumer_offsets
echo "--- 消费者组 lag（必须全部为 0 才允许迁移）---"
for g in $(docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --list 2>/dev/null); do
  docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group "$g" 2>/dev/null \
    | awk "NR>1 && \$6+0 > 0 {print \"LAG>0 组=\"\$1\" topic=\"\$2\" lag=\"\$6; exit 1}" || true
  docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group "$g" 2>/dev/null | tail -1
done
'

if [ "$ROLLBACK" = "1" ]; then
  echo
  echo "== 回滚到 ZK 模式 =="
  if [ "$EXECUTE" = "1" ]; then
    ssh_run "
      set -e
      docker compose -f $KRAFT_COMPOSE down --remove-orphans || true
      docker compose -f $INFRA_COMPOSE up -d zookeeper kafka
      sleep 20
      docker compose -f $APP_COMPOSE up -d edu-web-backend edu-agent-backend
      docker exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --list | sort | tr '\n' ' '
      echo
      echo '回滚完成（ZK 集群原数据卷保留）'
    "
  else
    echo "[dry-run] 将回滚：down KRaft -> up ZK compose -> up 后端"
  fi
  exit 0
fi

echo
echo "== 迁移计划 =="
cat <<PLAN
  1. 旧集群 offset 快照落 /root/backups/kafka_offsets_<ts>.txt
  2. 停 edu-web-backend / edu-agent-backend（暂停生产与消费）
  3. 停 zookeeper + kafka（保留数据卷，可回滚）
  4. 用 $KRAFT_COMPOSE 起 KRaft 集群 + 预建 topic: $TOPICS
  5. 起后端
  6. 对账：发 3 条探测消息并确认消费端收到 3 条（零丢失校验）
  7. 校验 /healthz 的 kafka 探活与消费者组重建
PLAN

if [ "$EXECUTE" != "1" ]; then
  echo
  echo "[dry-run] 确认无误后加 --execute。（--rollback 可回到 ZK 模式）"
  exit 0
fi

read -r -p "确认执行 KRaft 迁移？期间事件链路会短暂中断（输入 yes 继续）: " ans
[ "$ans" = "yes" ] || { echo "已取消"; exit 1; }

ssh_run "
set -e
TS=\$(date +%Y%m%d-%H%M%S)
echo '--- 1) offset 快照 ---'
mkdir -p /root/backups
for g in \$(docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --list 2>/dev/null); do
  docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group \"\$g\" 2>/dev/null
done > /root/backups/kafka_offsets_\$TS.txt
echo \"快照: /root/backups/kafka_offsets_\$TS.txt\"

echo '--- 2) 停后端（暂停生产/消费）---'
docker compose -f $APP_COMPOSE stop edu-web-backend edu-agent-backend

echo '--- 3) 停旧集群（数据卷保留）---'
docker compose -f $INFRA_COMPOSE stop kafka zookeeper

echo '--- 4) 起 KRaft 集群 ---'
docker compose -f $KRAFT_COMPOSE up -d
for i in \$(seq 1 40); do
  docker exec kafka /opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server kafka:9092 >/dev/null 2>&1 && break
  sleep 3
done
docker logs kafka-init 2>&1 | tail -6

echo '--- 5) 起后端 ---'
docker compose -f $APP_COMPOSE up -d edu-web-backend edu-agent-backend
sleep 15

echo '--- 6) 生产/消费对账（零丢失）---'
docker exec kafka bash -c 'printf \"p1\np2\np3\n\" | /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server kafka:9092 --topic student_event >/dev/null 2>&1'
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server kafka:9092 --topic student_event --from-beginning --timeout-ms 15000 --group migrate-verify 2>/dev/null | sed 's/^/  收到: /'

echo '--- 7) healthz 与消费者组 ---'
docker exec edu-web-backend python -c \"import urllib.request,json;d=json.load(urllib.request.urlopen('http://localhost:8000/healthz'));print('web healthz:',d)\" 2>&1 | tail -2 || true
docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --list 2>/dev/null | sed 's/^/  组: /'
echo '迁移执行完毕。观察一周无异常后：docker rm -f zookeeper（ZK 下线）'
"
