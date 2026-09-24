#!/bin/sh
# 把本机已下载的 needle3 引擎缓存复制到构建上下文 docker-cache/。
# 让 docker build 出来的镜像离线可用, 不带 huggingface 下载步骤。
#
# 用法: ./docker/prepare-cache.sh
set -e

SRC="${HOME}/.cache/cactus-needle"
DST="$(dirname "$0")/../docker-cache/cactus-needle"

if [ ! -d "$SRC" ]; then
    echo "ERROR: $SRC 不存在 — 先在本机跑一次 needle (python scripts_run_playground.py) 让它下载引擎"
    exit 1
fi

# 只带 3.0.1: scripts_run_playground.py 把 ENGINE_VERSIONS[3] 钉在 3.0.1,
# 镜像里 needle 运行时会去 ~/.cache/cactus-needle/v3/3.0.1/ 找引擎+权重。
# 整目录复制 v3/ 会多塞一份 3.0.2 权重 (~34MB, 运行时用不到)。
mkdir -p "$DST/v3"
[ -d "$SRC/v3/3.0.1" ] && cp -r "$SRC/v3/3.0.1" "$DST/v3/"

echo "已复制引擎缓存 -> $DST"
du -sh "$DST"
find "$DST" -type f | sed "s|$DST/||"
