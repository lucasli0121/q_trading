# For more information, please refer to https://aka.ms/vscode-docker-python
#FROM ubuntu:22.04 AS base
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.12-slim

# 设置非交互模式，避免安装过程中提示交互
ENV DEBIAN_FRONTEND=noninteractive

# 安装依赖库以及工具
#RUN apt-get update && apt-get install -y software-properties-common
#RUN add-apt-repository "deb http://security.ubuntu.com/ubuntu focal-security main"
RUN apt-get update && apt-get install -y \
    git \
    wget \
    pkg-config \
    build-essential \
    tzdata \
    poppler-utils \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo 'Asia/Shanghai' > /etc/timezone \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
    # Keeps Python from generating .pyc files in the container

ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel

LABEL Name=q_data_proxy Version=0.0.1
WORKDIR /

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY . /q_data_proxy
WORKDIR /q_data_proxy

# Explicitly copy cert directory (must be present in build context)
COPY cert/ /q_data_proxy/cert/

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /q_data_proxy
USER appuser

RUN pip install -r requirements.txt
RUN pip install akshare --upgrade -i https://pypi.tuna.tsinghua.edu.cn/simple

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
ENTRYPOINT ["python3", "main.py"]
