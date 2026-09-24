#!/bin/sh
# needle-cn 容器入口: 启 playground (自动装中文 grounding + 引擎版本补丁)
set -e

echo "[entrypoint] needle-cn  host=${NEEDLE_HOST:-0.0.0.0}  port=${NEEDLE_PORT:-7860}"

# 缓存缺失提示 (不阻塞: needle 会自行从 HF 下载)
CACHE=/root/.cache/cactus-needle/v3
if [ ! -f "$CACHE/3.0.1/libneedle.so" ] && [ ! -f "$CACHE/3.0.2/libneedle.so" ]; then
    echo "[entrypoint] WARN: no baked engine cache under $CACHE, first request will download from huggingface.co"
fi

# 如需换微调权重: docker run -v ./my.cact:/weights/my.cact -e NEEDLE_WEIGHTS=/weights/my.cact ...
[ -n "$NEEDLE_WEIGHTS" ] && set -- --weights "$NEEDLE_WEIGHTS" "$@"

exec python /app/scripts_run_playground.py \
    --host "${NEEDLE_HOST:-0.0.0.0}" \
    --port "${NEEDLE_PORT:-7860}" \
    "$@"
