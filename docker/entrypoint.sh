#!/bin/sh
# needle-cn 容器入口: 一个镜像, 两种服务, NEEDLE_SERVICE 切换
#   playground (默认)  scripts_run_playground.py   GET / + POST /complete   演示/测试
#   extract            scripts_run_needle_http.py  GET /health + POST /extract  业务后端
# 两者都自带: 引擎版本补丁 + cn_grounding.install() + P1 归一化。
set -e

SERVICE="${NEEDLE_SERVICE:-playground}"
HOST="${NEEDLE_HOST:-0.0.0.0}"
PORT="${NEEDLE_PORT:-$( [ "$SERVICE" = "extract" ] && echo 8081 || echo 7860 )}"

echo "[entrypoint] service=${SERVICE}  host=${HOST}  port=${PORT}"

# 缓存缺失提示 (不阻塞: needle 会自行从 HF 下载)
CACHE=/root/.cache/cactus-needle/v3
if [ ! -f "$CACHE/3.0.1/libneedle.so" ] && [ ! -f "$CACHE/3.0.2/libneedle.so" ]; then
    echo "[entrypoint] WARN: no baked engine cache under $CACHE, first request will download from huggingface.co"
fi

# 如需换微调权重: docker run -v ./my.cact:/weights/my.cact -e NEEDLE_WEIGHTS=/weights/my.cact ...
[ -n "$NEEDLE_WEIGHTS" ] && set -- --weights "$NEEDLE_WEIGHTS" "$@"

case "$SERVICE" in
    playground)
        exec python /app/scripts_run_playground.py --host "$HOST" --port "$PORT" "$@"
        ;;
    extract)
        exec python /app/scripts_run_needle_http.py --host "$HOST" --port "$PORT" "$@"
        ;;
    *)
        echo "[entrypoint] ERROR: NEEDLE_SERVICE must be 'playground' or 'extract', got '${SERVICE}'" >&2
        exit 1
        ;;
esac
