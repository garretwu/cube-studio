# SRE Agent Web 部署说明

## 1. 适用范围

这份说明覆盖 `sre_agent` Web 工程的三种运行方式：

- Host 手动运行
- Docker 容器运行
- Kubernetes 运行

当前推荐的云原生交付形态是：

- `Deployment` + `Service`

同时也提供了 Helm chart，方便多环境参数化部署。

相关运维文档：

- [FAQ.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/FAQ.md)
- [RELEASE_CHECKLIST.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/RELEASE_CHECKLIST.md)

## 2. 会构建什么

当前会构建两个镜像：

- `sre-agent-base:<YYYYMMDDHHMM>`
- `sre-agent-web:<YYYYMMDDHHMM>`

其中：

- `sre-agent-base` 包含系统依赖、Python 依赖和 Node 依赖
- `sre-agent-web` 只包含运行时代码和启动脚本，并复用基础镜像

## 3. 关于 `SRE_OPENAI_API_KEY`

`SRE_OPENAI_API_KEY` 是后端在调用配置好的模型服务时使用的运行时 key。

为什么本地启动时，即使你没有手动 `export SRE_OPENAI_API_KEY`，也可能仍然能跑起来：

- 当前 [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 已经包含 `llm.api_key`
- 启动脚本会自动把这个值带入运行环境

推荐做法：

- 生产环境不要依赖配置文件中的明文 key
- Docker 和 Kubernetes 场景优先通过环境变量或 `Secret` 传入

## 4. Host 手动运行

### 4.1 文件一览

| 文件 | 用途 |
| --- | --- |
| [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt) | `sre_agent` Web 工程在 Host 侧的 Python 依赖入口。 |
| [sre_agent/scripts/start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py) | 本地一键启动脚本，会同时拉起 backend 和 frontend。 |

### 4.2 前置条件

- Python `>= 3.11`
- Node `>= 20`
- npm 可用
- `uv` 已安装

### 4.3 创建 Python 环境

```bash
cd /home/kevin/project/cube-studio

uv venv --python 3.11 .venv
source .venv/bin/activate

uv pip install -r sre_agent/requirements.txt
```

### 4.4 安装前端依赖

```bash
cd /home/kevin/project/cube-studio/sre_agent/frontend
npm install
```

### 4.5 启动 Web 工程

```bash
cd /home/kevin/project/cube-studio
source .venv/bin/activate
python sre_agent/scripts/start_frontend_backend.py
```

默认端口：

- backend: `8000`
- frontend: `8080`

## 5. Docker 运行

原则说明：

- 正式交付时，优先使用 GitLab 流水线构建并发布到 Nexus 的镜像
- 本地手动执行 `build_images.sh` 主要用于功能验证、容器调试和页面效果确认
- 本地手动构建的镜像不应直接作为正式发布版本使用

### 5.1 文件一览

| 文件 | 用途 |
| --- | --- |
| [sre_agent/docker/Dockerfile.base](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile.base) | 构建包含 `apt`、Python、npm 依赖的基础镜像。 |
| [sre_agent/docker/Dockerfile](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile) | 基于基础镜像构建业务运行镜像。 |
| [sre_agent/docker/build_images.sh](/home/kevin/project/cube-studio/sre_agent/docker/build_images.sh) | 构建两个带时间戳的镜像，并把元数据写入 `image.env`。 |
| [sre_agent/docker/run_container.sh](/home/kevin/project/cube-studio/sre_agent/docker/run_container.sh) | 使用约定的环境变量和端口映射启动容器。 |
| [sre_agent/docker/container_entrypoint.sh](/home/kevin/project/cube-studio/sre_agent/docker/container_entrypoint.sh) | 容器启动入口，负责准备运行环境并拉起服务。 |
| [sre_agent/docker/run_web_stack.py](/home/kevin/project/cube-studio/sre_agent/docker/run_web_stack.py) | 容器专用启动器，不影响本地手动运行路径。 |
| [sre_agent/docker/healthcheck.py](/home/kevin/project/cube-studio/sre_agent/docker/healthcheck.py) | 在容器被标记为 `healthy` 前，确认 backend 和 frontend 都可访问。 |
| [sre_agent/docker/image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env) | 记录最近一次构建的镜像元数据，便于后续复用同一批 tag。 |
| [.dockerignore](/home/kevin/project/cube-studio/.dockerignore) | 控制 Docker build context，只保留这个服务真正需要的文件。 |

### 5.2 构建镜像

```bash
cd /home/kevin/project/cube-studio
bash sre_agent/docker/build_images.sh
```

会生成类似这样的 tag：

- `cube-studio/sre-agent-base:202604100930`
- `cube-studio/sre-agent-web:202604100930`

同时会把元数据写到：

- [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env)

### 5.3 启动容器

```bash
cd /home/kevin/project/cube-studio
export SRE_OPENAI_API_KEY=your-real-key
bash sre_agent/docker/run_container.sh
```

默认 Host 端口：

- backend: `28000`
- frontend: `28080`

可选覆盖方式：

```bash
HOST_BACKEND_PORT=18000 \
HOST_FRONTEND_PORT=28081 \
CONTAINER_NAME=sre-agent-web-dev \
SRE_OPENAI_API_KEY=your-real-key \
bash sre_agent/docker/run_container.sh
```

说明：

- 如果没有显式 `export SRE_OPENAI_API_KEY`，`run_container.sh` 会尝试从 [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 的 `llm.api_key` 中读取 fallback 值
- 如果两边都没有可用值，脚本才会退出报错
- `run_container.sh` 在启动前会先检查 Host 端口；如果默认端口被占用，会自动向后寻找可用端口，尽量避免因为端口冲突导致容器启动失败

### 5.4 查看状态

```bash
docker logs -f sre-agent-web
docker inspect --format '{{json .State.Health}}' sre-agent-web
```

### 5.5 本地体验完整流程

如果你现在只是想在本机验证“两个镜像能不能构建出来、容器能不能跑起来、页面能不能打开”，可以直接按下面步骤执行。

1. 构建两个镜像

```bash
cd /home/kevin/project/cube-studio
bash sre_agent/docker/build_images.sh
```

2. 确认镜像已经生成

```bash
cat sre_agent/docker/image.env
docker images | grep sre-agent
```

3. 启动容器

```bash
cd /home/kevin/project/cube-studio
bash sre_agent/docker/run_container.sh
```

4. 查看容器日志和健康状态

```bash
docker logs -f sre-agent-web
docker inspect --format '{{json .State.Health}}' sre-agent-web
```

启动脚本执行成功后，也会直接打印当前实际使用的 backend 和 frontend 访问地址。

5. 在浏览器中查看效果

默认访问地址：

- frontend: `http://127.0.0.1:28080`
- backend OpenAPI: `http://127.0.0.1:28000/openapi.json`

6. 停止容器

```bash
docker stop sre-agent-web
```

说明：

- 如果 [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 中已有 `llm.api_key`，`run_container.sh` 会自动读取这个值作为 `SRE_OPENAI_API_KEY` 的 fallback
- 默认 Host 端口已经避开了 `80`、`443`、`8080`、`18080`、`6443` 等常见服务端口；如果本机仍然冲突，可以继续手动覆盖，例如：

```bash
HOST_BACKEND_PORT=28100 \
HOST_FRONTEND_PORT=28180 \
CONTAINER_NAME=sre-agent-web-dev \
bash sre_agent/docker/run_container.sh
```

然后访问：

- frontend: `http://127.0.0.1:28180`
- backend OpenAPI: `http://127.0.0.1:28100/openapi.json`

## 6. Kubernetes 运行

### 6.1 文件一览

| 文件 | 用途 |
| --- | --- |
| [sre_agent/deploy/k8s/configmap.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/configmap.yaml) | 提供 Kubernetes 工作负载使用的非敏感运行时环境变量。 |
| [sre_agent/deploy/k8s/secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml) | `SRE_OPENAI_API_KEY` 等敏感配置的 `Secret` 示例文件。 |
| [sre_agent/deploy/k8s/service.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/service.yaml) | 暴露 frontend 和 backend 的集群内访问端口。 |
| [sre_agent/deploy/k8s/deployment.template.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/deployment.template.yaml) | 带参数占位的 `Deployment` 模板，会引用指定镜像 tag。 |
| [sre_agent/deploy/k8s/render_deployment.py](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/render_deployment.py) | 根据模板和镜像元数据渲染出实际可应用的 `Deployment`。 |
| [sre_agent/deploy/k8s/apply.sh](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/apply.sh) | 按约定顺序应用 Kubernetes 资源。 |

### 6.2 交付模型

当前 Kubernetes 交付模型是：

- 一个 `Deployment`
- 一个 `Service`
- 一个 `ConfigMap`
- 一个 `Secret`

支持两种部署路径：

- 原始 Kubernetes YAML
- Helm chart

其中：

- 原始 YAML 更适合直接调试和快速验证
- Helm 更适合多环境复用和参数化发布

### 6.3 构建并推送镜像

示例：

```bash
cd /home/kevin/project/cube-studio
IMAGE_REGISTRY=registry.example.com \
IMAGE_NAMESPACE=cube-studio \
bash sre_agent/docker/build_images.sh
```

推送生成的镜像：

```bash
source sre_agent/docker/image.env
docker push "${BASE_TAGGED}"
docker push "${APP_TAGGED}"
```

### 6.4 创建 Secret

先编辑：

- [secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml)

再执行：

```bash
kubectl apply -f sre_agent/deploy/k8s/secret.example.yaml
```

### 6.5 渲染并应用

如果存在 `sre_agent/docker/image.env`，渲染脚本会自动复用其中的最新时间戳 tag。

示例：

```bash
cd /home/kevin/project/cube-studio/sre_agent/deploy/k8s
python render_deployment.py
bash apply.sh
```

## 7. Helm 运行

### 7.1 文件一览

| 文件 | 用途 |
| --- | --- |
| [sre_agent/deploy/helm/sre-agent-web/Chart.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/Chart.yaml) | Helm chart 基本信息。 |
| [sre_agent/deploy/helm/sre-agent-web/values.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/values.yaml) | 默认 values 配置。 |
| [sre_agent/deploy/helm/sre-agent-web/templates/deployment.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/deployment.yaml) | `Deployment` 模板。 |
| [sre_agent/deploy/helm/sre-agent-web/templates/service.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/service.yaml) | `Service` 模板。 |
| [sre_agent/deploy/helm/sre-agent-web/templates/configmap.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/configmap.yaml) | `ConfigMap` 模板。 |
| [sre_agent/deploy/helm/sre-agent-web/templates/secret.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/secret.yaml) | `Secret` 模板。 |

### 7.2 安装或升级

```bash
helm upgrade --install sre-agent-web \
  /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web \
  --set image.repository=registry.example.com/cube-studio/sre-agent-web \
  --set image.tag=202604100930 \
  --set secret.create=true \
  --set secret.sreOpenaiApiKey=your-real-key
```

### 7.3 本地渲染检查

```bash
helm template sre-agent-web \
  /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web \
  --set image.repository=registry.example.com/cube-studio/sre-agent-web \
  --set image.tag=202604100930 \
  --set secret.create=true \
  --set secret.sreOpenaiApiKey=dummy-key
```
