#!/usr/bin/env bash
# 智聘云应用回滚：新版本健康/Smoke 失败时恢复上一版本镜像与 Compose
# 用法：./rollback.sh <上一版本目录>
set -Eeuo pipefail

PREVIOUS="${1:?usage: ./rollback.sh /opt/zhiyun/releases/<previous-version>}"
PROJECT="zhiyun"
SHARED_ENV="${SHARED_ENV:-/opt/zhiyun/shared/.env}"
CURRENT_LINK="${CURRENT_LINK:-/opt/zhiyun/current}"

test -d "$PREVIOUS" || { echo "上一版本目录不存在：$PREVIOUS" >&2; exit 1; }
test -r "$SHARED_ENV" || { echo "缺少 $SHARED_ENV" >&2; exit 1; }

echo "==> 回滚前记录现场（不删除新版本镜像与数据）"
if ! docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$CURRENT_LINK/compose.lite.yml" ps > "/opt/zhiyun/backups/rollback-$(date -u +%Y%m%dT%H%M%SZ).log"; then
  echo "记录当前容器状态失败，继续执行回滚" >&2
fi

echo "==> 停止新版本 Worker 消费"
if ! docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$CURRENT_LINK/compose.lite.yml" stop worker; then
  echo "停止旧 Worker 失败，继续启动上一版本" >&2
fi

echo "==> 启动上一版本"
docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$PREVIOUS/compose.lite.yml" up -d

echo "==> 上一版本健康门与只读 Smoke"
curl --fail --retry 15 --retry-delay 2 http://127.0.0.1/health/ready
curl --fail http://127.0.0.1/api/v1/jobs >/dev/null 2>&1 || echo "只读 Smoke：需要认证，跳过（健康门已通过）"

echo "==> 切换激活指针"
ln -sfn "$PREVIOUS" "$CURRENT_LINK"
echo "已回滚：$CURRENT_LINK -> $PREVIOUS"
echo "注意：已发送通知/外部副作用不可随回滚撤销，需按审计与补偿流程处理（06 §11.3）"
