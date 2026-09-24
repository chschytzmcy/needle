# Needle 3 playground + 中文 grounding 插件
#
# 构建:  docker build -t needle-cn:latest .
# 运行:  docker run -d --name needle -p 7860:7860 needle-cn:latest
#
# 镜像自带引擎缓存 (docker-cache/cactus-needle/, 约 36MB):
#   - libneedle.so  (C++ 推理引擎, linux-x86_64 glibc 构建)
#   - needle3.cact  (基础权重 14MB 模型)
# 有缓存时启动零网络; 若 docker-cache 为空, 首次启动会从 huggingface.co 下载。
#
# 更新缓存: ./docker/prepare-cache.sh   (从 ~/.cache/cactus-needle 复制)

FROM python:3.12-slim

# glibc 版 libneedle.so 直接可用; 换 musl (alpine) 需另下 musl wheel
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── 安装 needle 包 (pyproject 只依赖 huggingface_hub) ──
COPY pyproject.toml README.md MANIFEST.in ./
COPY needle/ ./needle/
# 上游钉的引擎 3.0.2 在 HF 上没有 wheel (只有 3.0.0/3.0.1); 镜像内直接改常量,
# 让任何进程 (不只 scripts_run_playground.py) 都命中烘进来的 3.0.1 缓存,
# 容器网络不通 HF 也不会卡下载。launcher 里的同款补丁因此变成双保险。
RUN sed -i 's/^\( *\)3: "3\.0\.2",/\13: "3.0.1",/' needle/agent/fetch.py \
    && grep -A3 'ENGINE_VERSIONS' needle/agent/fetch.py | head -5 \
    && pip install --no-cache-dir .

# ── 中文 grounding 插件 + 启动脚本 (含 3.0.2→3.0.1 版本补丁) ──
COPY cn_grounding.py scripts_run_playground.py ./

# ── 预烘引擎缓存: 启动不碰网络 ──
COPY docker-cache/cactus-needle/ /root/.cache/cactus-needle/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7860
ENV NEEDLE_HOST=0.0.0.0 \
    NEEDLE_PORT=7860

# 健康检查: 首页 200 即服务可用 (slim 无 curl, 用 python one-liner)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/', timeout=4)"]

ENTRYPOINT ["/entrypoint.sh"]
