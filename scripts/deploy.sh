#!/usr/bin/env bash
# 智聘云发布脚本：预检 → 摘要校验 → 镜像导入 → 备份 → 迁移 → 启动 → 健康门 → 激活
# 用法：./deploy.sh /opt/zhiyun/releases/<version>
set -Eeuo pipefail

RELEASE_DIR="${1:?usage: ./deploy.sh /opt/zhiyun/releases/<version>}"
PROJECT="zhiyun"
SHARED_ENV="${SHARED_ENV:-/opt/zhiyun/shared/.env}"
CURRENT_LINK="${CURRENT_LINK:-/opt/zhiyun/current}"

cd "$RELEASE_DIR"

PREVIOUS_DIR=""
if [ -L "$CURRENT_LINK" ]; then
  PREVIOUS_DIR="$(readlink -f "$CURRENT_LINK")"
fi

rollback_on_error() {
  local exit_code=$?
  trap - ERR
  if [ -n "$PREVIOUS_DIR" ] && [ -d "$PREVIOUS_DIR" ] && [ "$PREVIOUS_DIR" != "$RELEASE_DIR" ]; then
    echo "健康门失败，恢复上一版本：$PREVIOUS_DIR" >&2
    if ! docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$PREVIOUS_DIR/compose.lite.yml" up -d; then
      echo "自动恢复上一版本失败，请立即执行 rollback.sh $PREVIOUS_DIR" >&2
    else
      ln -sfn "$PREVIOUS_DIR" "$CURRENT_LINK"
    fi
  fi
  exit "$exit_code"
}
trap rollback_on_error ERR

echo "==> 预检"
[ "$(uname -m)" = "x86_64" ] || { echo "架构必须是 x86_64/amd64" >&2; exit 1; }
docker version >/dev/null
docker compose version >/dev/null
df -h /opt/zhiyun | tail -1
free -m | head -2
test -r "$SHARED_ENV" || { echo "缺少可读的 $SHARED_ENV" >&2; exit 1; }

echo "==> 摘要校验（失败即停止，不加载镜像）"
sha256sum -c SHA256SUMS

echo "==> 镜像导入（显式解压，兼容不同 Docker 版本）"
for archive in images/api.tar.gz images/worker.tar.gz images/web.tar.gz; do
  gzip -dc "$archive" | docker load
done
python3 - <<'PY' || { echo "release-manifest.json 校验失败" >&2; exit 1; }
import json, subprocess, sys
manifest = json.load(open("release-manifest.json"))
for name, tag in manifest["images"].items():
    actual = subprocess.check_output(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], text=True).strip()
    expected = manifest["image_digests"][name]
    if actual != expected:
        raise SystemExit(f"镜像摘要不匹配：{name} expected={expected} actual={actual}")
    print(f"已导入 {name}: {tag}")
print("manifest images:", ", ".join(manifest["images"].values()))
print("db revision:", manifest["database_revision"])
PY

echo "==> 发布前备份（首次部署无 current 时记录空备份）"
BACKUP="/opt/zhiyun/backups/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 700 "$BACKUP"
if [ -L "$CURRENT_LINK" ] && [ -f "$CURRENT_LINK/compose.lite.yml" ]; then
  docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$CURRENT_LINK/compose.lite.yml" \
    exec -T mysql sh -c 'exec mysqldump --single-transaction -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
    > "$BACKUP/mysql.sql"
  docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f "$CURRENT_LINK/compose.lite.yml" \
    exec -T worker tar -C /data/uploads -czf - . > "$BACKUP/uploads.tar.gz"
  cp "$CURRENT_LINK/compose.lite.yml" "$BACKUP/"
  cp "$SHARED_ENV" "$BACKUP/env.snapshot"
  chmod 600 "$BACKUP/env.snapshot"
else
  echo "首次部署：无旧数据，创建空备份标记"
  : > "$BACKUP/mysql.sql"
fi
sha256sum "$BACKUP"/* > "$BACKUP/SHA256SUMS"
sha256sum -c "$BACKUP/SHA256SUMS"
echo "备份完成：$BACKUP"

echo "==> 数据库迁移（从新 API 镜像执行；业务容器未切换）"
docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f compose.lite.yml \
  run --rm api alembic -c /app/alembic.ini upgrade head

echo "==> 启动与健康门（禁止 || true 吞掉失败）"
docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f compose.lite.yml up -d
docker compose --project-name "$PROJECT" --env-file "$SHARED_ENV" -f compose.lite.yml ps
curl --fail --retry 15 --retry-delay 2 http://127.0.0.1/health/ready

echo "==> Smoke Test（合成数据；不向真实候选人发送通知）"
python3 - <<'PY'
import json, urllib.request
base = "http://127.0.0.1"
req = urllib.request.Request(
    base + "/health/ready",
    headers={"Accept": "application/json"},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    assert resp.status == 200, "readiness 失败"
print("健康检查通过")
PY

echo "==> 激活版本"
ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
echo "已激活：$CURRENT_LINK -> $RELEASE_DIR"
echo "发布日志：$BACKUP"
echo "部署完成"
