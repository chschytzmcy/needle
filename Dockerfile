# Needle 3 镜像: 一个镜像, 两种服务 (NEEDLE_SERVICE 切换)
#
#   playground  (默认)  :7860  GET / 演示 UI + POST /complete, 人用
#   extract             :8081  GET /health + POST /extract, 纯提取业务后端
#
# 构建:  docker build -t needle-cn:latest .
# 运行:  docker compose up -d           (两个服务一起起)
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

# ── 安装 needle 包; [http] extra 带 fastapi+uvicorn (extract 服务用) ──
COPY pyproject.toml README.md MANIFEST.in ./
COPY needle/ ./needle/
# 上游钉的引擎 3.0.2 在 HF 上没有 wheel (只有 3.0.0/3.0.1); 镜像内直接改常量,
# 让任何进程 (不只 launcher 脚本) 都命中烘进来的 3.0.1 缓存,
# 容器网络不通 HF 也不会卡下载。launcher 里的同款补丁因此变成双保险。
RUN sed -i 's/^\( *\)3: "3\.0\.2",/\13: "3.0.1",/' needle/agent/fetch.py \
    && grep -A3 'ENGINE_VERSIONS' needle/agent/fetch.py | head -5 \
    && pip install --no-cache-dir ".[http]"

# ── 中文插件 (grounding + P1) 与两个启动脚本 ──
COPY cn_grounding.py scripts_run_playground.py scripts_run_needle_http.py ./

# ── 预烘引擎缓存: 启动不碰网络 ──
COPY docker-cache/cactus-needle/ /root/.cache/cactus-needle/

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7860
ENV NEEDLE_HOST=0.0.0.0 \
    NEEDLE_PORT=7860

# 健康检查随服务切换: playground 探 GET /, extract 探 GET /health
# (extract 暖机期 /health 返回 503 → urlopen 抛 HTTPError → 非零退出, 语义正确)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request as u; p=os.environ.get('NEEDLE_PORT','7860'); s=os.environ.get('NEEDLE_SERVICE','playground'); u.urlopen('http://127.0.0.1:%s%s' % (p, '/health' if s=='extract' else '/'), timeout=4)"]

ENTRYPOINT ["/entrypoint.sh"]
