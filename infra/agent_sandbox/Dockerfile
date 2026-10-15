# Python 代码分析沙箱镜像：仅提供受限的静态分析与 pytest 测试入口。
# 构建时安装本项目测试依赖；运行时通过只读工作区挂载接收待分析代码。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid sandbox --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /opt/build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir ".[dev]"

COPY infra/agent_sandbox/runner.py /opt/sandbox/runner.py
RUN chown -R sandbox:sandbox /opt/sandbox

USER sandbox:sandbox
WORKDIR /workspace
ENTRYPOINT ["python", "/opt/sandbox/runner.py"]
CMD ["analyze"]
