#!/usr/bin/env bash
# 在服务器上把 app、worker 更新到指定版本。GitHub Actions 自动部署时执行（见 .github/workflows/deploy.yml），也可手动运行：
#   bash deploy/deploy.sh <镜像> <提交>
#   例：bash deploy/deploy.sh ccr.ccs.tencentyun.com/<命名空间>/fg-quiz-cloud:<提交> <提交>
# 步骤：拉取镜像 → 备份数据库 → 仓库快进到该提交 → 新镜像标记为 latest 并重建 app、worker → 等待健康检查。
# 切换前任一步失败都不影响正在运行的服务；切换后健康检查不通过时自动回滚到旧镜像与旧提交。
# 环境变量：DEPLOY_DIR（仓库目录）、BACKUP_DIR（备份目录）、BACKUP_KEEP（保留的部署前备份份数）、
#   ROLLBACK_KEEP（保留的回滚镜像数）、DOCKER（docker 命令，默认 sudo -n docker）
set -Eeuo pipefail

IMAGE=${1:?用法：deploy.sh <镜像> <提交>}
SHA=${2:?用法：deploy.sh <镜像> <提交>}
DEPLOY_DIR=${DEPLOY_DIR:-$HOME/wuage-quiz-cloud}
BACKUP_DIR=${BACKUP_DIR:-$HOME/backups}
BACKUP_KEEP=${BACKUP_KEEP:-20}
ROLLBACK_KEEP=${ROLLBACK_KEEP:-3}
read -r -a DOCKER <<< "${DOCKER:-sudo -n docker}"
LOCAL=fg-quiz-cloud   # docker-compose.yml 使用 fg-quiz-cloud:latest

log() { printf '[%(%F %T)T] %s\n' -1 "$*"; }
die() { log "失败：$*"; exit 1; }
dc() { "${DOCKER[@]}" compose "$@"; }

cd "$DEPLOY_DIR"
# 同一时间只运行一次部署
exec 9> .git/deploy.lock
flock -n 9 || die "另一次部署正在进行"

# 1. 确认提交：Actions 部署前已把提交推送到本仓库；手动运行时从 GitHub 获取（服务器访问 GitHub 常有 TLS 错误，多试几次）
if ! git cat-file -e "$SHA^{commit}" 2>/dev/null; then
  for i in 1 2 3 4 5 6; do
    git fetch -q origin && break
    log "git fetch 失败（第 $i 次），5 秒后重试"; sleep 5
  done
  git cat-file -e "$SHA^{commit}" 2>/dev/null || die "找不到提交 $SHA"
fi
SHA=$(git rev-parse "$SHA^{commit}")
OLD=$(git rev-parse HEAD)
git merge-base --is-ancestor "$OLD" "$SHA" \
  || die "${SHA:0:7} 不是当前版本 ${OLD:0:7} 之后的提交（回到旧版本见 docs/deployment.md「自动部署」）"
log "部署 ${OLD:0:7} → ${SHA:0:7}"

# 2. 拉取镜像（耗时最长，失败时不影响正在运行的服务）
"${DOCKER[@]}" pull -q "$IMAGE" > /dev/null || die "拉取镜像 $IMAGE 失败"

# 3. 备份数据库
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/fg_quiz-$(date +%Y%m%d-%H%M%S)-pre-deploy-${SHA:0:7}.sql.gz"
dc exec -T db pg_dump -U fg_quiz fg_quiz < /dev/null | gzip > "$BACKUP" || { rm -f "$BACKUP"; die "备份数据库失败"; }
log "已备份数据库：$BACKUP（$(du -h "$BACKUP" | cut -f1)）"

# 4. 记录回滚点，仓库快进到新提交（docker-compose.yml 等配置随提交更新）
ROLLBACK=$LOCAL:rollback-${OLD:0:7}
"${DOCKER[@]}" tag "$LOCAL:latest" "$ROLLBACK"
git merge -q --ff-only "$SHA" || die "仓库无法快进到 ${SHA:0:7}（服务器上改过受版本管理的文件？）"

# 5. 切换到新镜像；db、redis 保持运行
rollback() {
  log "新版本未通过健康检查，回滚到 ${OLD:0:7}"
  dc logs --tail 80 app worker || true
  git reset -q --keep "$OLD" || log "仓库未能退回 ${OLD:0:7}，请手动处理"
  "${DOCKER[@]}" tag "$ROLLBACK" "$LOCAL:latest"
  dc up -d --no-build --wait --wait-timeout 240 app worker || log "回滚后仍未通过健康检查，请登录服务器检查"
  exit 1
}
"${DOCKER[@]}" tag "$IMAGE" "$LOCAL:latest"
dc up -d --no-build --wait --wait-timeout 240 app worker || rollback
curl -fsS --retry 3 --retry-delay 2 http://127.0.0.1:8000/api/health > /dev/null || rollback
log "已上线 ${SHA:0:7}"

# 6. 清理：镜像仓库的旧标签、较早的回滚镜像与部署前备份（只删除标签，镜像层仍被引用时保留）
if [[ ${IMAGE%:*} != "$LOCAL" ]]; then
  "${DOCKER[@]}" images --format '{{.Repository}}:{{.Tag}}' "${IMAGE%:*}" | grep -vxF "$IMAGE" \
    | xargs -r "${DOCKER[@]}" rmi > /dev/null 2>&1 || true
fi
"${DOCKER[@]}" images --format '{{.Tag}}' "$LOCAL" | grep '^rollback-' | tail -n +$((ROLLBACK_KEEP + 1)) \
  | sed "s/^/$LOCAL:/" | xargs -r "${DOCKER[@]}" rmi > /dev/null 2>&1 || true
"${DOCKER[@]}" image prune -f > /dev/null || true
find "$BACKUP_DIR" -maxdepth 1 -name 'fg_quiz-*-pre-deploy-*.sql.gz' -printf '%T@ %p\n' | sort -rn \
  | tail -n +$((BACKUP_KEEP + 1)) | cut -d' ' -f2- | xargs -r rm -f
