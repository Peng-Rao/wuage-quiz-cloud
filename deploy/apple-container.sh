#!/usr/bin/env bash
# 在 Apple 芯片的 Mac 上用 Apple Container 部署题库（见 docs/deployment.md）。
set -euo pipefail

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

usage='用法：apple-container.sh up [--no-build] | down'
case "${1:-}" in
  up) [[ $# == 1 || ( $# == 2 && $2 == --no-build ) ]] || die "$usage" ;;
  down) [[ $# == 1 ]] || die "$usage" ;;
  *) die "$usage" ;;
esac

[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || die '需要使用 Apple 芯片的 Mac'
command -v container >/dev/null || die '请先安装 Apple Container'
container list --quiet >/dev/null || die '请先运行 container system start'

cd "$(dirname "$0")/.."
project=${APPLE_CONTAINER_PROJECT:-fg-quiz-apple}
[[ $project =~ ^[a-z0-9][a-z0-9-]*$ ]] || die 'APPLE_CONTAINER_PROJECT 只能使用小写字母、数字和连字符'
db=$project-db
redis=$project-redis
app=$project-app
worker=$project-worker
app_image=$project-app:latest
db_image=$project-postgres:17-vector
redis_image=redis:8-alpine

# 数据目录由 app 与 worker 共同挂载。每个命名卷只能挂载到一台运行中的 VM。
state_dir="$HOME/Library/Application Support/$project"
umask 077
mkdir -p "$state_dir"
exec 9>"$state_dir/deploy.lock"
lockf -t 0 9 || die "另一个 $project 部署命令正在运行"

down() {
  local containers name state
  containers=$(container list --all --format json)
  for name in "$worker" "$app" "$redis" "$db"; do
    state=$(jq -r --arg name "$name" '.[] | select(.id == $name) | .status.state' <<<"$containers")
    if [[ $state == running ]]; then
      container stop --time 30 "$name"
    fi
    if [[ -n $state ]]; then
      container rm "$name"
    fi
  done
}

if [[ $1 == down ]]; then
  down
  exit 0
fi

port=${APPLE_CONTAINER_PORT:-8000}
[[ $port =~ ^[1-9][0-9]{0,4}$ && $port -le 65535 ]] || die 'APPLE_CONTAINER_PORT 必须在 1–65535 之间'
export POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-fg-quiz-local}
[[ $POSTGRES_PASSWORD =~ ^[a-zA-Z0-9_-]+$ ]] || die 'POSTGRES_PASSWORD 只能使用字母、数字、- 和 _'
export CONTAINER_DEFAULT_PLATFORM=linux/arm64

if [[ ${2:-} != --no-build ]]; then
  container build --build-arg "EMBEDDINGS_EXTRA=${EMBEDDINGS_EXTRA:-}" -t "$app_image" .
  container build -f Dockerfile.pgvector -t "$db_image" .
fi
container image inspect "$app_image" "$db_image" >/dev/null
container image inspect "$redis_image" >/dev/null 2>&1 || container image pull "$redis_image"

down
container network inspect "$project" >/dev/null 2>&1 || container network create "$project"
for volume in "$project-pg" "$project-redis"; do
  container volume inspect "$volume" >/dev/null 2>&1 || container volume create "$volume"
done
mkdir -p "$state_dir/data"

wait_ready() {
  local name=$1 deadline=$((SECONDS + 120))
  shift
  until "$@" >/dev/null 2>&1; do
    container inspect "$name" | jq -e '.[0].status.state == "running"' >/dev/null \
      || die "$name 已停止，请查看 container logs $name"
    (( SECONDS < deadline )) || die "$name 未就绪，请查看 container logs $name"
    sleep 2
  done
}

container run -d --name "$db" --network "$project" \
  -e POSTGRES_DB=fg_quiz -e POSTGRES_USER=fg_quiz -e POSTGRES_PASSWORD \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  --mount "type=volume,source=$project-pg,target=/var/lib/postgresql/data" "$db_image"
# 新建命名卷的根目录由 root 持有，Redis 镜像会切换到 redis 用户运行。
container run -d --name "$redis" --network "$project" \
  --mount "type=volume,source=$project-redis,target=/data" --entrypoint sh "$redis_image" \
  -ec 'chown redis:redis /data; exec docker-entrypoint.sh redis-server --appendonly yes'
wait_ready "$db" container exec "$db" pg_isready -h 127.0.0.1 -U fg_quiz -d fg_quiz
wait_ready "$redis" container exec "$redis" redis-cli ping

# 自定义网络不依赖全局 DNS 设置。每次重建后读取当前 IP，再创建 app 与 worker。
container_ip() {
  container inspect "$1" | jq -er --arg network "$project" \
    '.[0].status.networks[] | select(.network == $network) | .ipv4Address | split("/")[0]'
}
db_ip=$(container_ip "$db")
redis_ip=$(container_ip "$redis")
export DATABASE_URL=${DATABASE_URL:-postgresql+psycopg://fg_quiz:$POSTGRES_PASSWORD@$db_ip:5432/fg_quiz?sslmode=disable}
export REDIS_URL=${REDIS_URL:-redis://$redis_ip:6379/0}

backend=(--network "$project" -v "$state_dir/data:/data" -e DATA_DIR=/data -e DATABASE_URL -e REDIS_URL)
if [[ -f server/.env ]]; then
  backend+=(--env-file server/.env)
fi
url=http://127.0.0.1:$port
container run -d --name "$app" "${backend[@]}" -p "127.0.0.1:$port:8000" "$app_image"
wait_ready "$app" curl --noproxy '*' -fsS --max-time 5 "$url/api/health"
container run -d --name "$worker" "${backend[@]}" "$app_image" python -m app.worker
wait_ready "$worker" container exec "$worker" python -m app.worker --check
printf '服务已就绪：%s\n' "$url"
