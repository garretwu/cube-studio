# SRE Agent Web FAQ

## 1. 常用命令速查

### 1.1 Host 手动运行

| 目标 | 命令 |
| --- | --- |
| 查看 Python 版本 | `python --version` |
| 创建虚拟环境 | `uv venv --python 3.11 .venv` |
| 激活虚拟环境 | `source .venv/bin/activate` |
| 安装 Python 依赖 | `uv pip install -r sre_agent/requirements.txt` |
| 安装前端依赖 | `cd sre_agent/frontend && npm install` |
| 启动本地 Web 工程 | `python sre_agent/scripts/start_frontend_backend.py` |

### 1.2 Docker

| 目标 | 命令 |
| --- | --- |
| 构建镜像 | `bash sre_agent/docker/build_images.sh` |
| 启动容器 | `bash sre_agent/docker/run_container.sh` |
| 跟随日志 | `docker logs -f sre-agent-web` |
| 查看健康检查 | `docker inspect --format '{{json .State.Health}}' sre-agent-web` |
| 停止容器 | `docker stop sre-agent-web` |

### 1.3 Kubernetes

| 目标 | 命令 |
| --- | --- |
| 应用原始 YAML | `bash sre_agent/deploy/k8s/apply.sh` |
| 查看 Pods | `kubectl get pods` |
| 查看 Services | `kubectl get svc` |
| 查看 Deployment 详情 | `kubectl describe deploy sre-agent-web` |
| 查看 Pod 详情 | `kubectl describe pod <pod-name>` |
| 查看 Deployment 日志 | `kubectl logs deploy/sre-agent-web` |
| 为 frontend 做 `port-forward` | `kubectl port-forward svc/sre-agent-web 18080:80` |

### 1.4 Helm

| 目标 | 命令 |
| --- | --- |
| 安装或升级 | `helm upgrade --install sre-agent-web /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web --set image.repository=registry.example.com/cube-studio/sre-agent-web --set image.tag=202604100930 --set secret.create=true --set secret.sreOpenaiApiKey=your-real-key` |
| 查看 releases | `helm list` |
| 查看 release 状态 | `helm status sre-agent-web` |
| 本地渲染 chart | `helm template sre-agent-web /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web --set image.repository=registry.example.com/cube-studio/sre-agent-web --set image.tag=202604100930 --set secret.create=true --set secret.sreOpenaiApiKey=dummy-key` |

## 2. 本地运行常见问题

### 2.1 `python sre_agent/scripts/start_frontend_backend.py` 报 `vite: not found`

原因：

- frontend 依赖还没有安装

处理方式：

```bash
cd /home/kevin/project/cube-studio/sre_agent/frontend
npm install

cd /home/kevin/project/cube-studio
python sre_agent/scripts/start_frontend_backend.py
```

### 2.2 `npm install` 出现大量 `EBADENGINE`

原因：

- Node 版本过低

要求：

- Node `>= 20`

检查方式：

```bash
node -v
npm -v
```

### 2.3 本地 Python 启动时提示缺少模块

原因：

- 虚拟环境没有激活
- Python 依赖还没有安装

处理方式：

```bash
cd /home/kevin/project/cube-studio
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r sre_agent/requirements.txt
```

## 3. Docker 常见问题

### 3.1 容器启动后很快退出

先检查：

```bash
docker logs <container-name>
docker inspect --format '{{json .State.Health}}' <container-name>
```

常见原因：

- `SRE_OPENAI_API_KEY` 缺失，且没有其他 fallback 配置
- Host 端口映射冲突
- 使用了错误的时间戳 tag

### 3.2 健康检查启动初期失败一次，随后恢复

这通常是正常现象。

原因：

- 健康检查可能早于 Vite 完全就绪

预期结果：

- 后续状态会变成 `healthy`

## 4. Kubernetes 常见问题

### 4.1 Pod 一直处于 `CrashLoopBackOff`

先检查：

```bash
kubectl logs deploy/sre-agent-web
kubectl describe pod <pod-name>
```

常见原因：

- `Secret` 里没有 `SRE_OPENAI_API_KEY`
- 镜像 tag 不正确
- 镜像没有推送到目标 registry
- 集群无法拉取镜像

### 4.2 Pod 是 `Running`，但页面打不开

先检查：

```bash
kubectl get svc sre-agent-web
kubectl describe svc sre-agent-web
```

说明：

- frontend `Service` 端口是 `80`
- backend `Service` 端口是 `8000`

如果要从集群外访问，可以使用以下任一方式：

- `Ingress`
- `NodePort`
- `kubectl port-forward`

示例：

```bash
kubectl port-forward svc/sre-agent-web 18080:80
```

然后访问：

```text
http://127.0.0.1:18080
```

## 5. 配置相关问题

### 5.1 为什么本地启动时没有手动导出 `SRE_OPENAI_API_KEY` 也能跑

原因：

- [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 当前已经包含 `llm.api_key`

更推荐的生产实践：

- Docker 环境变量
- Kubernetes `Secret`

### 5.2 原始 YAML 和 Helm 应该选哪个

建议：

- 原始 YAML 更适合快速验证和直接调试
- Helm 更适合多环境复用和参数化发布

两种方式最终对应的资源模型是一致的：

- `Deployment`
- `Service`
- `ConfigMap`
- `Secret`
