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

RUN apt-get update && apt-get install -y xfonts-75dpi xfonts-base
    
RUN wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb && \
    apt-get install -y ./wkhtmltox_0.12.6.1-2.jammy_amd64.deb || apt-get -f install -y && \
    rm -f ./wkhtmltox_0.12.6.1-2.jammy_amd64.deb

RUN wget https://github.com/ta-lib/ta-lib/releases/download/v0.6.4/ta-lib-0.6.4-src.tar.gz && \
    tar -xzf ta-lib-0.6.4-src.tar.gz && \
    cd ta-lib-0.6.4 && \
    ./configure --prefix=/usr && \
    make && \
    make install

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

# Install pip requirements
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel

LABEL Name=q_trading_work Version=0.0.1
WORKDIR /

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1


RUN pip install --upgrade pip setuptools wheel

RUN pip install curl_cffi
# Install pip requirements
COPY . /q_trading_work
WORKDIR /q_trading_work

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser -u 5678 --disabled-password --gecos "" appuser && chown -R appuser /q_trading_work
USER appuser

RUN pip install -r requirements.txt

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
ENTRYPOINT ["python3", "main.py"]
