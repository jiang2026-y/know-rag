# Python 代码分析沙箱

本目录定义独立 Docker 沙箱，只允许两种操作：

- `analyze`：使用 Python AST 进行静态语法分析，不导入或执行待分析代码。
- `test`：在容器临时目录执行 `pytest -q`。

沙箱仅适用于 Python 代码分析和测试，不提供 Shell、原生 SQL、其他语言运行时或网络访问。

## 安全与资源边界

- 使用非 root 用户运行，根文件系统只读。
- 无网络、无 Docker Socket、无宿主机目录默认挂载。
- 受控调度器仅复制 `src/**/*.py`、`tests/**/*.py`、`pyproject.toml` 与调度器自身 Python 文件到一次性临时工作区；`.env`、Git 元数据、非 Python 文件和依赖目录不会进入容器。
- 调度器只读挂载临时工作区；测试生成的文件仅位于容器内 `/tmp`。
- CPU 上限为 1 核，内存硬上限为 512 MiB、软预留为 256 MiB，进程上限为 128。
- `/tmp` 使用 128 MiB 的 tmpfs；容器由 `--rm` 自动清理。

## 构建与运行

在项目根目录构建镜像：

```powershell
docker compose -f infra/agent_sandbox/compose.yaml --profile sandbox build
```

静态分析：

```powershell
python infra/agent_sandbox/sandbox_dispatcher.py analyze
```

运行测试：

```powershell
python infra/agent_sandbox/sandbox_dispatcher.py test
```

调度器是 Docker 的可信调用方。未来 Agent Runtime 应通过受控 HTTP 接口请求调度器，不能直接获得 Docker Socket 或执行此脚本的宿主机权限。

镜像构建阶段需要访问 Python 软件包源；容器运行阶段使用 `network_mode: none`，无法访问网络。
