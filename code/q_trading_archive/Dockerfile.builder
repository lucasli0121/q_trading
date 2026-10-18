# 打包用构建环境，不进产物。
#
# 只在打 Linux 可执行文件时使用：开发机是 Windows，而 PyInstaller 不能跨平台
# 产出，所以固定在一个 x86-64 Linux 容器里构建。
#
# 基础镜像走华为云加速，本机直连 Docker Hub 不通。
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# binutils 提供 readelf，用于交付前核对产物的 glibc 版本需求；
# file 用于确认产出确实是 ELF 而不是被拷错平台的二进制。
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
        file \
        tzdata \
        ca-certificates \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo 'Asia/Shanghai' > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 运行依赖与打包依赖分开装：前者决定产物内容，后者只影响构建。
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir "pyinstaller==6.11.1" "pyinstaller-hooks-contrib>=2024.8"

WORKDIR /src
