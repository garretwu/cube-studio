# Cube Studio Edge Computing Architecture Design

## 1. Current Architecture Baseline

Cube Studio 当前的多集群架构基于以下模式：

```
CLUSTERS = {
    "cluster-a": { "KUBECONFIG": "/path/to/kubeconfig-a", ... },
    "cluster-b": { "KUBECONFIG": "/path/to/kubeconfig-b", ... },
}
```

- **Project → Cluster 绑定**：每个项目通过 `project.expand['cluster']` 指定目标集群
- **K8s Client 按需实例化**：`K8s(cluster['KUBECONFIG'])` 直连目标集群
- **Node Selector 调度**：通过 label（`gpu=true`, `org=xxx`, `train=true`）在集群内路由 Pod
- **CRD 管理**：Argo Workflow、TFJob、PyTorchJob 等直接提交到目标集群

## 2. Target Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │          Karmada Control Plane               │
                    │  (部署在 K8S 管理集群上)                      │
                    │                                             │
                    │  ┌───────────────┐  ┌────────────────────┐  │
                    │  │ karmada-api   │  │ PropagationPolicy  │  │
                    │  │ karmada-ctrl  │  │ OverridePolicy     │  │
                    │  │ karmada-sched │  │ ResourceBinding    │  │
                    │  └───────────────┘  └────────────────────┘  │
                    └──────────┬─────────────────┬────────────────┘
                               │                 │
              ┌────────────────▼──┐       ┌──────▼────────────────┐
              │  K8S 管理集群      │       │  K3S 边缘集群 (N个)    │
              │  (control plane)  │       │  (compute nodes)      │
              │                   │       │                       │
              │  Cube Studio      │       │  Training Jobs        │
              │  myapp 后端       │       │   TFJob/PyTorchJob    │
              │  frontend 前端    │       │   Argo Workflow       │
              │  MySQL / Redis    │       │   Volcano Job         │
              │  Celery Beat/Wkr  │       │                       │
              │  LiteLLM Proxy    │       │  Inference Services   │
              │  自研调度进程      │       │   vLLM (推理引擎)      │
              │  Prometheus       │       │                        │
              │  Grafana          │       │                       │
              │  Argo Server      │       │  Custom Services      │
              │                   │       │   用户自研服务          │
              │  Karmada Agent    │       │                       │
              │  (push mode)      │       │  Karmada Agent        │
              │                   │       │  (push mode)          │
              └───────────────────┘       └───────────────────────┘
```

### 2.1 集群职责划分

| 集群 | 角色 | 运行的组件 |
|------|------|-----------|
| **K8S 管理集群** | 控制面 | Cube Studio 全套、Karmada CP、LiteLLM、自研调度、监控栈、Argo Server |
| **K3S 边缘集群** | 计算面 | 训练任务、推理服务、自定义服务的实际 Pod |

### 2.2 为什么不把所有请求都通过 Karmada API

Cube Studio 当前的 CRD 管理（Argo Workflow、TFJob 等）依赖直接操作目标集群的 CustomObjectsApi。Karmada 对 CRD 的传播需要注册 ResourceInterpreter 来正确解析 status 回传和 replica 管理。对于 Argo Workflow 这类有复杂生命周期的 CRD，透传会引入大量适配工作。

**因此采用混合模式：**

| 资源类型 | 路由方式 | 原因 |
|---------|---------|------|
| Deployment + Service（推理服务、自定义服务） | **Karmada API** | 标准 K8s 资源，Karmada 原生支持，可利用 PropagationPolicy 做自动分发和故障转移 |
| CRD（Argo Workflow, TFJob, PyTorchJob, Volcano Job） | **直连 K3S kubeconfig** | CRD 状态回传复杂，直连最可靠；由自研调度进程决定目标集群 |
| 控制面组件（Cube Studio, MySQL, LiteLLM 等） | **直接部署在 K8S** | 不需要跨集群分发 |

## 3. Component Design

### 3.1 Karmada Federation Setup

```yaml
# Karmada 部署在 K8S 管理集群
# K3S 边缘集群以 push 模式注册

# 注册边缘集群示例
karmadactl join edge-cluster-01 \
  --kubeconfig=/etc/karmada/karmada-apiserver.config \
  --cluster-kubeconfig=/path/to/k3s-edge-01.kubeconfig
```

**PropagationPolicy 示例 — 推理服务分发到边缘：**

```yaml
apiVersion: policy.karmada.io/v1alpha1
kind: PropagationPolicy
metadata:
  name: inference-service-propagation
  namespace: service
spec:
  resourceSelectors:
    - apiVersion: apps/v1
      kind: Deployment
      labelSelector:
        matchLabels:
          pod-type: inference
    - apiVersion: v1
      kind: Service
      labelSelector:
        matchLabels:
          pod-type: inference
  placement:
    clusterAffinity:
      clusterNames:
        - edge-cluster-01
        - edge-cluster-02
    replicaScheduling:
      replicaSchedulingType: Divided    # 副本跨集群分散
      replicaDivisionPreference: Weighted
      weightPreference:
        staticWeightList:
          - targetCluster:
              clusterNames: [edge-cluster-01]
            weight: 1
          - targetCluster:
              clusterNames: [edge-cluster-02]
            weight: 1
```

**OverridePolicy 示例 — 边缘集群镜像仓库覆盖：**

```yaml
apiVersion: policy.karmada.io/v1alpha1
kind: OverridePolicy
metadata:
  name: edge-image-override
  namespace: service
spec:
  targetCluster:
    clusterNames:
      - edge-cluster-01
  overriders:
    imageOverrider:
      - component: Registry
        operator: replace
        value: edge-registry.internal:5000/cube-studio
```

### 3.2 Cube Studio 代码改造

#### 3.2.1 CLUSTERS 配置扩展

在 `install/docker/config.py` 中：

```python
CLUSTERS = {
    # 控制面 — 仅运行平台组件
    "control": {
        "NAME": "control",
        "KUBECONFIG": "/home/myapp/kubeconfig/k8s-control.kubeconfig",
        "SERVICE_DOMAIN": "control.svc.cluster.local",
        "ROLE": "control-plane",  # 新增字段
    },
    # 边缘计算集群
    "edge-01": {
        "NAME": "edge-01",
        "KUBECONFIG": "/home/myapp/kubeconfig/k3s-edge-01.kubeconfig",
        "SERVICE_DOMAIN": "edge-01.local",
        "ROLE": "compute",         # 新增字段
        "LOCATION": "region-east", # 新增: 地理位置
        "GPU_TYPES": ["A100"],     # 新增: 可用 GPU 类型
    },
    "edge-02": {
        "NAME": "edge-02",
        "KUBECONFIG": "/home/myapp/kubeconfig/k3s-edge-02.kubeconfig",
        "SERVICE_DOMAIN": "edge-02.local",
        "ROLE": "compute",
        "LOCATION": "region-west",
        "GPU_TYPES": ["V100", "T4"],
    },
}

# Karmada API 配置 — 用于推理/自定义服务分发
KARMADA_KUBECONFIG = "/home/myapp/kubeconfig/karmada-apiserver.kubeconfig"
KARMADA_ENABLED = True
```

#### 3.2.2 K8s Client 改造 (`myapp/utils/py/py_k8s.py`)

现有 `K8s.__init__` 按 kubeconfig 初始化。改造点：

```python
class K8s():
    def __init__(self, file_path=None, cluster_name=''):
        # ... 现有逻辑不变 ...
        self.cluster_name = cluster_name

    # 新增: 通过 Karmada API 创建带 PropagationPolicy 的 Deployment
    def create_deployment_via_karmada(self, namespace, name, target_clusters, **kwargs):
        """
        提交 Deployment 到 Karmada API，附带 PropagationPolicy 注解。
        仅用于推理服务和自定义服务。
        """
        karmada_client = K8s(file_path=conf.get('KARMADA_KUBECONFIG'))

        # 创建 Deployment（提交到 Karmada API）
        deployment = karmada_client.create_deployment(namespace=namespace, name=name, **kwargs)

        # 创建对应的 PropagationPolicy
        policy = {
            "apiVersion": "policy.karmada.io/v1alpha1",
            "kind": "PropagationPolicy",
            "metadata": {
                "name": f"{name}-propagation",
                "namespace": namespace,
            },
            "spec": {
                "resourceSelectors": [{
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": name,
                }],
                "placement": {
                    "clusterAffinity": {
                        "clusterNames": target_clusters
                    }
                }
            }
        }
        karmada_client.create_crd(
            group="policy.karmada.io",
            version="v1alpha1",
            plural="propagationpolicies",
            namespace=namespace,
            body=policy,
        )
        return deployment
```

#### 3.2.3 View 层改造

**`view_inferenceserving.py` 和 `view_serving.py` 的 `deploy()` 方法：**

```python
def deploy(self, service_id):
    service = db.session.query(InferenceService).filter_by(id=service_id).first()

    if conf.get('KARMADA_ENABLED') and service.project.cluster.get('ROLE') == 'compute':
        # Karmada 路径: 推理服务/自定义服务走 Karmada 分发
        target_clusters = scheduler.select_clusters(service)  # 调用自研调度
        k8s_client = K8s(conf.get('KARMADA_KUBECONFIG'))
        k8s_client.create_deployment_via_karmada(
            namespace=namespace,
            name=name,
            target_clusters=target_clusters,
            # ... 其他参数 ...
        )
    else:
        # 直连路径: 和现在一样
        k8s_client = K8s(service.project.cluster.get('KUBECONFIG', ''))
        k8s_client.create_deployment(...)
```

**`view_pipeline.py` 的 `run_pipeline()` — CRD 直连不变：**

```python
def run_pipeline(pipeline):
    # CRD 类资源直连目标集群，由自研调度决定哪个集群
    target_cluster = scheduler.select_cluster_for_pipeline(pipeline)
    k8s_client = K8s(target_cluster['KUBECONFIG'])
    k8s_client.create_crd(
        group=crd_info['group'],
        version=crd_info['version'],
        plural=crd_info['plural'],
        namespace=namespace,
        body=workflow_json,
    )
```

### 3.3 两层调度：K8s Pod 调度 vs 推理 Request 调度

系统中存在两个完全不同层面的调度，不能混淆：

| 层面 | 调度什么 | 谁来做 | 频率 |
|------|---------|--------|------|
| **Pod 调度** | 推理服务 Deployment 部署到哪个集群/节点 | Cube Studio 现有 project→cluster 绑定 + K8s/K3S 原生 scheduler | 低频（部署/扩容时） |
| **Request 调度** | 每个推理 API 请求路由到哪个后端实例 | **自研调度进程**（在 LiteLLM 和推理引擎之间） | 高频（每个请求） |

自研调度进程是一个 **request-level 智能路由**，不操作 K8s API，不创建/删除 Pod。

### 3.3.1 推理请求数据流

```
用户/应用
    │
    │ POST /v1/chat/completions (OpenAI 协议)
    ▼
┌──────────┐
│ LiteLLM  │  (hub / K8S 管理集群)
│ port 4000│
│          │  model_name → 查路由表 → 发现后端由 scheduler 管理
└────┬─────┘
     │ 转发请求
     ▼
┌─────────────────────────────────────────────────────────┐
│              自研调度进程 (Inference Router)              │
│              port 8080, hub / K8S 管理集群               │
│                                                         │
│  1. 接收 OpenAI 兼容的推理请求                            │
│  2. 查询后端注册表: 哪些边缘实例提供该 model              │
│  3. 获取各实例实时负载 (GPU利用率, 队列深度, 延迟)         │
│  4. 按策略选择最优后端实例                                │
│  5. 反向代理请求到边缘推理引擎                            │
│  6. 支持流式 (SSE) 透传                                  │
│                                                         │
│  后端注册来源:                                           │
│    - Cube Studio MySQL: InferenceService 表              │
│    - 或 K8s Service Discovery (list services in edge)    │
│                                                         │
│  负载数据来源:                                           │
│    - Prometheus: GPU利用率, 请求QPS                      │
│    - 直接健康检查: /health 端点                           │
│    - 本地统计: 已转发请求数、响应延迟                      │
└────┬────────────────────────┬───────────────────────────┘
     │                        │
     ▼                        ▼
┌────────────┐          ┌────────────┐
│ edge-01    │          │ edge-02    │
│ vLLM :8000 │          │ vLLM :8000 │
│ (K3S)      │          │ (K3S)      │
└────────────┘          └────────────┘
```

### 3.3.2 自研调度进程设计

**本质：** 一个 OpenAI API 兼容的反向代理，核心是路由决策。

```python
# 请求处理伪代码
@app.route('/v1/chat/completions', methods=['POST'])
async def route_inference(request):
    model = request.json['model']

    # 1. 查后端注册表
    backends = registry.get_backends(model)  # 返回 [endpoint_url, ...]

    # 2. 获取各后端负载
    scored = []
    for b in backends:
        load = metrics.get_load(b)   # GPU%, queue_depth, latency_p99
        score = strategy.score(b, load)
        scored.append((b, score))

    # 3. 选最优
    target = max(scored, key=lambda x: x[1])

    # 4. 反向代理 (支持 stream=true)
    return await proxy_request(request, target.url)
```

**路由策略：**

| 策略 | 说明 |
|------|------|
| `least-loaded` | 选 GPU 利用率最低的后端 |
| `least-queue` | 选排队请求最少的后端 |
| `round-robin` | 简单轮询（兜底） |
| `latency-aware` | 选历史 P99 延迟最低的后端 |
| `sticky-model` | 相同 model 倾向同一后端（利用 KV cache warmup） |

### 3.3.3 自研调度 vs LiteLLM 内置路由的关系

LiteLLM 自带 `routing_strategy: least-busy`，为什么还需要自研？

| 维度 | LiteLLM 内置 | 自研调度 |
|------|-------------|---------|
| 路由粒度 | model 级别（同 model 的多个 deployment 间轮询） | 实例级别（感知每个 replica 的 GPU 负载） |
| 负载感知 | 基于 LiteLLM 自身请求计数 | 基于 Prometheus 实时 GPU 利用率 + 推理引擎队列深度 |
| 边缘感知 | 无 | 感知边缘集群拓扑、网络延迟 |
| 扩缩容联动 | 无 | 可触发 Cube Studio API 扩容推理服务副本 |
| 自定义策略 | 有限 | 完全可控 |

**两者配合方式：**
- LiteLLM 的 `model_list` 中，将自研调度的地址作为后端的 `api_base`
- LiteLLM 负责 API key 管理、协议适配、重试
- 自研调度负责实例级路由和负载均衡

```yaml
# LiteLLM config — 对接自研调度
model_list:
  - model_name: llama-3-70b
    litellm_params:
      model: openai/llama-3-70b
      api_base: http://inference-router.infra:8080/v1  # 自研调度地址
      api_key: internal-key
```

### 3.3.4 LiteLLM 部署

部署在 K8S 管理集群，作为统一的 LLM API 网关。

```yaml
# install/kubernetes/litellm/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: litellm
  namespace: infra
spec:
  replicas: 2
  selector:
    matchLabels:
      app: litellm
  template:
    metadata:
      labels:
        app: litellm
    spec:
      containers:
        - name: litellm
          image: ghcr.io/berriai/litellm:main-latest
          ports:
            - containerPort: 4000
          env:
            - name: DATABASE_URL
              value: "mysql+pymysql://root:admin@mysql.infra:3306/litellm"
            - name: LITELLM_MASTER_KEY
              valueFrom:
                secretKeyRef:
                  name: litellm-secrets
                  key: master-key
          volumeMounts:
            - name: config
              mountPath: /app/config.yaml
              subPath: config.yaml
      volumes:
        - name: config
          configMap:
            name: litellm-config
---
apiVersion: v1
kind: Service
metadata:
  name: litellm
  namespace: infra
spec:
  ports:
    - port: 4000
      targetPort: 4000
  selector:
    app: litellm
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: litellm-config
  namespace: infra
data:
  config.yaml: |
    model_list:
      - model_name: gpt-4
        litellm_params:
          model: openai/gpt-4
          api_key: os.environ/OPENAI_API_KEY

      # 边缘集群自部署的模型 (vLLM) — 通过自研调度进程路由
      - model_name: llama-3-local
        litellm_params:
          model: openai/llama-3
          api_base: http://inference-router.infra:8080/v1  # 走自研调度
          api_key: internal-key

    litellm_settings:
      drop_params: true
      set_verbose: false

    router_settings:
      routing_strategy: least-busy
      num_retries: 3
      timeout: 120
```

**与 Cube Studio 集成 — 修改 `config.py`：**

```python
# 替换现有的 ChatGPT 配置
CHATGPT_CHAT_URL = ['http://litellm.infra:4000/v1/chat/completions']
CHATGPT_ARGS = {
    "model": "gpt-4"  # LiteLLM 会路由到实际后端
}
```

Cube Studio 已有 `view_chat.py` 调用 OpenAI 兼容接口，LiteLLM 完全兼容此协议，无需改动 Chat 界面代码。

### 3.4 K3S 边缘集群部署清单（含 JuiceFS）

每个 K3S 边缘集群需要安装的组件：

```
K3S 边缘集群
├── K3S Server (控制面, 可精简为 agent-only 如果用外部 etcd)
├── Kubeflow Training Operator    ← TFJob/PyTorchJob/MPIJob CRD controller
├── Karmada Agent                 ← push 模式注册到 Karmada CP
├── Prometheus Node Exporter      ← 指标采集，remote_write 到管理集群 Prometheus
├── GPU Operator (如有 GPU)       ← NVIDIA device plugin + DCGM exporter
└── 镜像缓存                      ← 可选，加速边缘镜像拉取
```

**不需要在边缘安装的：**
- Argo Server（Workflow Controller 需要，但 Argo Server UI 不需要）
- ~~Argo Workflow Controller~~ → **需要安装**，CRD 直接提交到边缘集群
- Istio（可选，如需边缘集群内的流量管理）
- MySQL/Redis（控制面组件，仅在管理集群）

修正：需要安装 Argo Workflow Controller，因为 Workflow CRD 直接提交到边缘集群。

**K3S 边缘集群最终组件清单：**

| 组件 | 必选/可选 | 作用 |
|------|---------|------|
| K3S Server + Agent | 必选 | K8s 运行时 |
| Kubeflow Training Operator | 必选 | 管理 TFJob/PyTorchJob 等训练 CRD |
| Argo Workflow Controller | 必选 | 执行 Pipeline DAG |
| Karmada Agent | 必选 | 加入 Karmada 联邦 |
| Prometheus Node Exporter | 必选 | 资源监控指标采集 |
| NVIDIA GPU Operator | 按需 | GPU 节点支持 |
| 镜像缓存 (Harbor/Dragonfly) | 按需 | 加速边缘镜像拉取 |
| JuiceFS CSI Driver | 必选 | 共享文件系统 (连接 Ceph) |
| Volcano | 按需 | gang scheduling |

## 4. Data Flow

### 4.1 训练任务提交流程

```
用户在 Web UI 创建 Pipeline 并点击运行
    │
    ▼
myapp 后端 view_pipeline.py::run_pipeline()
    │
    ├─ 调用自研调度: scheduler.select_cluster_for_pipeline(pipeline)
    │     │
    │     ▼
    │  自研调度进程查询各边缘集群资源 (Prometheus)
    │     │
    │     ▼
    │  返回: target_cluster = "edge-01"
    │
    ▼
K8s(CLUSTERS["edge-01"]["KUBECONFIG"])  ← 直连 K3S
    │
    ▼
create_crd(Workflow) → 提交到 edge-01 的 Argo Workflow Controller
    │
    ▼
Argo Workflow Controller 在 edge-01 上编排 DAG
    │
    ├─ Task A: create TFJob CRD → Training Operator 在 edge-01 执行
    ├─ Task B: create PyTorchJob CRD → Training Operator 在 edge-01 执行
    └─ Task C: 普通 Pod → 直接在 edge-01 运行
```

### 4.2 推理服务生命周期（部署 + 请求路由）

**阶段 1: Pod 部署（低频，用户操作触发）**

```
用户在 Web UI 创建推理服务 (vLLM + llama-3-70b)
    │
    ▼
myapp 后端 view_inferenceserving.py::deploy()
    │
    ▼
project.cluster → 确定目标 K3S 边缘集群
    │
    ├─ 方式A (Karmada): K8s(KARMADA_KUBECONFIG) → PropagationPolicy → 分发到边缘
    └─ 方式B (直连):   K8s(edge-01 kubeconfig) → 直接创建 Deployment
    │
    ▼
K3S 边缘集群上运行 vLLM Pod，监听 :8000
    │
    ▼
推理服务注册到 Cube Studio MySQL (InferenceService 表)
  → endpoint: http://llama-3.service.edge-01.local:8000
```

**阶段 2: 请求路由（高频，每个推理请求）**

```
用户应用
    │ POST /v1/chat/completions  {"model":"llama-3-70b"}
    ▼
LiteLLM (hub, port 4000)
    │ 查 model_list → api_base = http://inference-router.infra:8080/v1
    ▼
自研调度进程 (hub, port 8080)
    │ 查后端注册表 → [edge-01:vllm:8000, edge-02:vllm:8000]
    │ 查 Prometheus → edge-01 GPU 80%, edge-02 GPU 40%
    │ 策略: least-loaded → 选 edge-02
    ▼
edge-02 vLLM :8000 → 执行推理 → 返回结果 → 原路返回用户
```

### 4.3 监控数据流

```
K3S edge-01                     K3S edge-02
  │ node-exporter                 │ node-exporter
  │ dcgm-exporter                 │ dcgm-exporter
  │                               │
  └──── remote_write ────┐  ┌────┘ remote_write
                         │  │
                    ┌────▼──▼─────┐
                    │ Prometheus  │  (K8S 管理集群)
                    │  (联邦)     │
                    └──────┬─────┘
                           │
                    ┌──────▼─────┐
                    │  Grafana   │
                    └──────┬─────┘
                           │
                    ┌──────▼──────────────┐
                    │ Cube Studio         │
                    │ view_total_resource  │
                    │ (PromQL 查询)       │
                    └─────────────────────┘
```

## 5. Storage: JuiceFS + Ceph (独立部署)

管理集群和边缘集群共享同一套 JuiceFS 文件系统，底层由独立部署的 Ceph 集群提供对象存储。

```
┌──────────────┐     ┌──────────────┐
│ K8S 管理集群  │     │ K3S 边缘集群  │
│              │     │              │
│  JuiceFS CSI │     │  JuiceFS CSI │
│  Driver      │     │  Driver      │
└──────┬───────┘     └──────┬───────┘
       │                    │
       └──────┬─────────────┘
              │ S3 (RGW) / RADOS
       ┌──────▼───────┐
       │   Ceph 集群   │
       │  (独立部署)   │
       │  3+ OSD 节点  │
       │  RGW (S3 API) │
       │  元数据: Redis │
       └──────────────┘
```

### 5.1 Ceph 独立部署

Ceph 集群不运行在 K8S/K3S 上，使用独立物理机或虚拟机部署：

| 组件 | 数量 | 作用 |
|------|------|------|
| MON (Monitor) | 3 | Ceph 集群监控和共识 |
| OSD (Object Storage Daemon) | 3+ | 实际数据存储 |
| RGW (RADOS Gateway) | 2 | 提供 S3 兼容 API 给 JuiceFS |
| MGR (Manager) | 2 | Dashboard 和监控 |

JuiceFS 通过 Ceph RGW 的 S3 API 存储数据，元数据存储在 Redis（复用管理集群的 Redis 或独立部署）。

### 5.2 对 Cube Studio 的影响

| 现有配置 | 当前值 | 改为 |
|---------|--------|------|
| `WORKSPACE_HOST_PATH` | `/data/k8s/kubeflow/pipeline/workspace` (hostPath) | JuiceFS PVC 挂载点 |
| `ARCHIVES_HOST_PATH` | `/data/k8s/kubeflow/pipeline/archives` (hostPath) | JuiceFS PVC 挂载点 |
| volume_mount 格式 | `pvc_name(pvc):/path` | 不变，PVC 后端指向 JuiceFS StorageClass |

**好处：** 训练数据、模型文件、Pipeline workspace 在管理集群和所有边缘集群上自动可见。模型在管理集群训练完成后，边缘集群的 vLLM 可直接从 JuiceFS 路径加载，无需手动拷贝。

### 5.3 需要部署的组件

| 位置 | 组件 |
|------|------|
| 独立机器 (3+ 台) | Ceph MON + OSD + RGW + MGR |
| K8S 管理集群 | JuiceFS CSI Driver |
| K3S 边缘集群 | JuiceFS CSI Driver (连接同一 Ceph RGW) |
| 管理集群 Redis (或独立) | JuiceFS metadata engine |

## 6. Network Topology

### 6.1 管理集群 ↔ 边缘集群 连通性（内网直连）

```
K8S 管理集群                       K3S 边缘集群
┌──────────────────┐              ┌──────────────────┐
│ Cube Studio      │──kubeconfig─▶│ K3S API (6443)   │
│ (myapp)          │              │                  │
│ Karmada CP       │──push mode──▶│ Karmada Agent    │
│ Prometheus       │──scrape ────▶│ Node Exporter    │
│                  │              │ DCGM Exporter    │
│ 自研调度进程      │──HTTP ──────▶│ 推理引擎 (:8000) │
│ (inference-router)│             │ (vLLM)             │
│ LiteLLM          │──via router─▶│                  │
└──────────────────┘              └──────────────────┘
         │                                 │
         └──── JuiceFS (Ceph S3) ──────────┘
```

**必须打通的端口：**

| 源 | 目标 | 端口 | 用途 |
|----|------|------|------|
| 管理集群 myapp | K3S API | 6443 | kubeconfig 直连 |
| Karmada CP | K3S Karmada Agent | 10350 | 集群联邦通信 |
| 管理集群 Prometheus | K3S node-exporter | 9100 | 节点指标 |
| 管理集群 Prometheus | K3S dcgm-exporter | 9400 | GPU 指标 |
| **自研调度进程** | K3S 推理引擎 | 8000-8082 | **推理请求转发** |
| 两端 JuiceFS CSI | Ceph S3 | 7480 / 8080 | 存储访问 |

### 6.2 推理流量入口

确定路径：LiteLLM (hub) → 自研调度进程 (hub) → 边缘推理引擎 (spoke)

```
用户/应用
    │ OpenAI 兼容 API
    ▼
LiteLLM (K8S 管理集群, :4000)
    │ 按 model 查路由 → api_base = scheduler
    ▼
自研调度进程 (K8S 管理集群, :8080)
    │ 智能路由 → 选最优边缘实例
    ▼
K3S 边缘集群 vLLM (:8000)
    │ 执行推理
    ▼
响应原路返回
```

用户只需知道 LiteLLM 的入口地址，不需要感知边缘集群的存在。

## 7. Config Changes Summary

`install/docker/config.py` 需要新增的配置项：

```python
# Karmada 联邦配置
KARMADA_ENABLED = True
KARMADA_KUBECONFIG = '/home/myapp/kubeconfig/karmada-apiserver.kubeconfig'

# LiteLLM (替换现有 CHATGPT 配置)
CHATGPT_CHAT_URL = ['http://litellm.infra:4000/v1/chat/completions']

# CLUSTERS 中新增 ROLE 字段（见 3.2.1）

# JuiceFS StorageClass (边缘集群和管理集群使用同一 StorageClass)
JUICEFS_STORAGE_CLASS = 'juicefs-sc'
```

## 8. 自研代码全部放入 cube-studio 仓库

### 8.1 为什么不拆独立仓库

Cube Studio 已有成熟的"同代码库、多进程"模式：

| 进程 | 入口 | 共享内容 |
|------|------|---------|
| Web Server | `python myapp/run.py` 或 `gunicorn myapp:app` | 全部 |
| Celery Beat | `celery -A myapp.tasks.celery_app beat` | models, utils, config |
| Celery Worker | `celery -A myapp.tasks.celery_app worker` | models, utils, config |
| 监控 Watch | `python myapp/tools/watch_service.py` | py_k8s, models, config |
| CLI | `myapp init / myapp db upgrade` | 全部 |

自研调度进程（inference-router）也是一个独立进程，需要复用：

- `myapp.models.model_serving.InferenceService` — 读取推理服务的 endpoint、model 信息，构建后端注册表
- `install/docker/config.py` 中的 `CLUSTERS`、`PROMETHEUS` 等配置
- `myapp.utils.celery.session_scope` — 独立进程安全访问 DB（NullPool）

如果拆独立仓库，这些全部要 copy 或做成 pip 包，维护成本极高。放在同一仓库里，一个 import 解决。

### 8.2 自研代码树

以下是新增文件和目录的完整结构（`[新建]` 标记为新文件，其余为改动现有文件）：

```
cube-studio/
│
├── myapp/
│   ├── __init__.py                          # 无改动
│   ├── config.py                            # 无改动（基础配置）
│   ├── run.py                               # 无改动
│   │
│   ├── models/
│   │   ├── model_serving.py                 # 小改: InferenceService 加 endpoint 注册相关字段
│   │   └── model_team.py                    # 小改: Project.cluster 属性增加 ROLE 字段解析
│   │
│   ├── views/
│   │   ├── view_inferenceserving.py         # 改: deploy() 增加 Karmada 分支
│   │   ├── view_serving.py                  # 改: deploy() 增加 Karmada 分支
│   │   └── view_total_resource.py           # 小改: 资源视图增加边缘集群标识
│   │
│   ├── utils/
│   │   └── py/
│   │       └── py_k8s.py                    # 改: 新增 create_deployment_via_karmada()
│   │
│   ├── inference_router/                    # [新建] 推理请求调度进程 (自研调度)
│   │   ├── __init__.py
│   │   ├── run.py                           # 进程入口: Flask HTTP server, port 8080
│   │   ├── proxy.py                         # 核心: OpenAI 兼容反向代理 (支持 SSE 流式)
│   │   ├── registry.py                      # 后端注册表: 从 MySQL InferenceService 表构建 model→endpoints 映射
│   │   ├── metrics.py                       # 负载采集: vLLM Prometheus metrics (gpu_cache, queue_depth)
│   │   ├── autoscaler.py                    # 自动扩缩容: 负载过高时调 Cube Studio API 扩容
│   │   ├── strategies/                      # 路由策略 (可插拔)
│   │   │   ├── __init__.py
│   │   │   ├── base.py                      # RoutingStrategy 抽象基类
│   │   │   ├── least_loaded.py              # 选 GPU 利用率最低的后端
│   │   │   ├── least_queue.py               # 选 vLLM 等待队列最短的后端
│   │   │   ├── round_robin.py               # 轮询 (兜底)
│   │   │   └── latency_aware.py             # 选 P99 延迟最低的后端
│   │   ├── health.py                        # 后端健康检查 (定期探测 vLLM /health)
│   │   └── config.py                        # 路由配置 (策略选择, 刷新间隔, 扩缩容阈值)
│   │
│   ├── karmada/                             # [新建] Karmada 集成模块
│   │   ├── __init__.py
│   │   ├── client.py                        # KarmadaClient: 封装 PropagationPolicy/OverridePolicy 操作
│   │   └── policy.py                        # Policy 模板生成: 根据服务类型生成对应 Policy
│   │
│   ├── tasks/
│   │   ├── celery_app.py                    # 无改动
│   │   ├── async_task.py                    # 无改动
│   │   └── schedules.py                     # 小改: 新增定时同步边缘集群健康状态的 task
│   │
│   └── tools/
│       └── watch_service.py                 # 无改动
│
├── install/
│   ├── docker/
│   │   ├── config.py                        # 改: 新增 KARMADA / LiteLLM 配置
│   │   ├── docker-compose.yml               # 改: 新增 inference-router 服务定义
│   │   └── entrypoint.sh                    # 无改动
│   │
│   └── kubernetes/
│       ├── karmada/                          # [新建] Karmada 部署
│       │   ├── karmada-init.sh              # Karmada 安装脚本
│       │   ├── join-edge-cluster.sh         # K3S 边缘集群注册脚本
│       │   └── propagation-policies/        # 预置的分发策略模板
│       │       ├── inference-service.yaml
│       │       └── custom-service.yaml
│       │
│       ├── litellm/                          # [新建] LiteLLM 部署
│       │   ├── deployment.yaml
│       │   ├── service.yaml
│       │   └── configmap.yaml
│       │
│       ├── juicefs/                          # [新建] JuiceFS CSI 部署
│       │   ├── storageclass.yaml            # JuiceFS StorageClass
│       │   └── csi-driver.yaml              # CSI driver (管理集群 + 边缘集群各装一份)
│       │
│       ├── edge-cluster/                     # [新建] K3S 边缘集群初始化
│       │   ├── setup-k3s.sh                 # K3S 安装
│       │   ├── install-training-operator.sh # Kubeflow Training Operator
│       │   ├── install-argo.sh              # Argo Workflow Controller
│       │   ├── install-gpu-operator.sh      # NVIDIA GPU Operator
│       │   ├── install-juicefs-csi.sh       # JuiceFS CSI Driver
│       │   └── install-prometheus-agent.sh  # Prometheus remote_write agent
│       │
│       └── prometheus/                       # 已有，小改
│           └── prometheus-additional.yaml    # 改: 增加边缘集群 scrape 配置
│
├── CLAUDE.md
└── design.md
```

### 8.3 各模块详解

#### `myapp/inference_router/` — 推理请求调度进程

**角色：** OpenAI API 兼容的反向代理服务。位于 LiteLLM 和边缘推理引擎之间，做 request-level 智能路由。

**入口与启动方式：**

```python
# myapp/inference_router/run.py
from flask import Flask
from myapp import app as cube_app  # 复用 cube-studio 的 config/DB

router_app = Flask(__name__)
router_app.config.from_object(cube_app.config)

from myapp.inference_router.proxy import bp
router_app.register_blueprint(bp)

# 启动后台线程: 定期从 DB 刷新后端注册表 + 从 Prometheus 刷新负载
from myapp.inference_router.registry import start_sync_loop
start_sync_loop(router_app)

if __name__ == '__main__':
    router_app.run(host='0.0.0.0', port=8080, threaded=True)
```

**启动命令（与现有进程并列）：**

```bash
# 现有进程
python myapp/run.py                                           # Web Server (port 80)
celery -A myapp.tasks.celery_app:celery_app beat              # Celery Beat
celery -A myapp.tasks.celery_app:celery_app worker            # Celery Worker

# 新增进程
python myapp/inference_router/run.py                          # 推理路由 (port 8080)
```

**Docker Compose 新增服务（`install/docker/docker-compose.yml`）：**

```yaml
  inference-router:
    image: ccr.ccs.tencentyun.com/cube-studio/kubeflow-dashboard:2026.01.01
    restart: unless-stopped
    command: ['python', 'myapp/inference_router/run.py']
    ports:
      - '8080:8080'
    environment:
      REDIS_HOST: 'redis'
      REDIS_PORT: '6379'
      REDIS_PASSWORD: admin
      MYSQL_SERVICE: 'mysql+pymysql://root:admin@mysql:3306/kubeflow?charset=utf8mb4'
    depends_on:
      redis:
        condition: service_started
      mysql:
        condition: service_healthy
    volumes:
      - ../../myapp/:/home/myapp/myapp/
      - ./config.py:/home/myapp/myapp/config.py
```

**与 myapp 使用完全相同的镜像**，只是启动命令不同。不需要挂载 kubeconfig（不操作 K8s API）。

**核心: 反向代理（支持 SSE 流式）：**

```python
# myapp/inference_router/proxy.py
import requests
from flask import Blueprint, request, Response, stream_with_context, jsonify
from myapp.inference_router.registry import BackendRegistry
from myapp.inference_router.metrics import MetricsCollector
from myapp.inference_router.strategies import get_strategy

bp = Blueprint('router', __name__)
registry = BackendRegistry()
metrics = MetricsCollector()

@bp.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    body = request.json
    model = body.get('model', '')
    stream = body.get('stream', False)

    # 1. 查后端注册表
    backends = registry.get_backends(model)
    if not backends:
        return jsonify({"error": f"No backends for model {model}"}), 503

    # 2. 路由决策
    strategy = get_strategy()  # least_loaded / round_robin / ...
    backend = strategy.select(backends, metrics)

    # 3. 反向代理
    target_url = f"{backend.endpoint}/v1/chat/completions"
    headers = {k: v for k, v in request.headers if k.lower() != 'host'}

    if stream:
        # SSE 流式透传
        resp = requests.post(target_url, json=body, headers=headers, stream=True, timeout=300)
        return Response(
            stream_with_context(resp.iter_content(chunk_size=None)),
            content_type=resp.headers.get('content-type'),
            status=resp.status_code,
        )
    else:
        resp = requests.post(target_url, json=body, headers=headers, timeout=300)
        return Response(resp.content, content_type='application/json', status=resp.status_code)

# 兼容其他 OpenAI 端点
@bp.route('/v1/completions', methods=['POST'])
def completions():
    # 同样的路由逻辑...
    pass

@bp.route('/v1/models', methods=['GET'])
def list_models():
    """聚合所有后端的可用模型列表"""
    return jsonify(registry.list_all_models())
```

**后端注册表（从 Cube Studio DB 自动同步）：**

```python
# myapp/inference_router/registry.py
import threading, time
from myapp.utils.celery import session_scope
from myapp.models.model_serving import InferenceService
from myapp import conf

class Backend:
    def __init__(self, name, model_name, endpoint, cluster, status):
        self.name = name
        self.model_name = model_name
        self.endpoint = endpoint       # e.g. http://10.0.1.5:8000
        self.cluster = cluster
        self.status = status           # 'Running' / 'Stopped'

class BackendRegistry:
    def __init__(self):
        self._backends = {}  # model_name → [Backend, ...]
        self._lock = threading.Lock()

    def get_backends(self, model_name):
        with self._lock:
            return [b for b in self._backends.get(model_name, []) if b.status == 'Running']

    def sync_from_db(self):
        """从 InferenceService 表刷新后端列表"""
        with session_scope(nullpool=True) as session:
            services = session.query(InferenceService).filter(
                InferenceService.model_status == 'serving'
            ).all()
            new_backends = {}
            for svc in services:
                # 构建 endpoint: service_name.namespace.svc.cluster_domain:port
                cluster_cfg = conf.get('CLUSTERS', {}).get(svc.project.cluster_name, {})
                domain = cluster_cfg.get('SERVICE_DOMAIN', 'svc.cluster.local')
                endpoint = f"http://{svc.name}.service.{domain}:{svc.ports}"
                backend = Backend(svc.name, svc.model_name, endpoint, svc.project.cluster_name, svc.status)
                new_backends.setdefault(svc.model_name, []).append(backend)

            with self._lock:
                self._backends = new_backends

def start_sync_loop(app, interval=10):
    """后台线程每 10s 从 DB 刷新后端注册表"""
    def _loop():
        with app.app_context():
            while True:
                try:
                    registry.sync_from_db()
                except Exception as e:
                    print(f"Registry sync error: {e}")
                time.sleep(interval)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
```

**负载指标采集（vLLM Prometheus metrics）：**

vLLM 在 `:8000/metrics` 暴露 Prometheus 指标。关键指标：

| vLLM Metric | 含义 | 用于策略 |
|-------------|------|---------|
| `vllm:num_requests_running` | 当前正在处理的请求数 | least_queue |
| `vllm:num_requests_waiting` | 等待队列深度 | least_queue |
| `vllm:gpu_cache_usage_perc` | GPU KV cache 使用率 (0-1) | least_loaded |
| `vllm:avg_generation_throughput_toks_per_s` | 平均生成吞吐 (tokens/s) | latency_aware |
| `vllm:e2e_request_latency_seconds` (histogram) | 端到端请求延迟 | latency_aware |
| `DCGM_FI_DEV_GPU_UTIL` | GPU 计算利用率 (0-100, DCGM exporter) | least_loaded |

```python
# myapp/inference_router/metrics.py
import requests, threading, time
from dataclasses import dataclass, field
from myapp import conf

@dataclass
class BackendMetrics:
    gpu_utilization: float = 0.0        # DCGM_FI_DEV_GPU_UTIL (0-100)
    gpu_cache_usage: float = 0.0        # vllm:gpu_cache_usage_perc (0-1)
    requests_running: int = 0           # vllm:num_requests_running
    requests_waiting: int = 0           # vllm:num_requests_waiting
    latency_p99_ms: float = 0.0         # vllm:e2e_request_latency_seconds p99
    healthy: bool = True

class MetricsCollector:
    def __init__(self):
        self._metrics = {}  # endpoint → BackendMetrics
        self.prometheus_url = f"http://{conf.get('PROMETHEUS', 'prometheus-k8s.monitoring:9090')}"

    def get_load(self, backend):
        return self._metrics.get(backend.endpoint, BackendMetrics())

    def refresh(self, backends):
        """从 Prometheus 批量查询 vLLM 和 GPU 指标, 每 5s 调用一次"""
        for backend in backends:
            m = BackendMetrics()
            try:
                # vLLM 指标: 直接查 Prometheus (vLLM pod 的 metrics 由 Prometheus 抓取)
                pod_selector = f'pod=~"{backend.name}.*"'

                # GPU cache 使用率
                r = self._prom_query(f'vllm:gpu_cache_usage_perc{{{pod_selector}}}')
                if r: m.gpu_cache_usage = float(r[0]['value'][1])

                # 运行中请求数
                r = self._prom_query(f'vllm:num_requests_running{{{pod_selector}}}')
                if r: m.requests_running = int(float(r[0]['value'][1]))

                # 等待中请求数
                r = self._prom_query(f'vllm:num_requests_waiting{{{pod_selector}}}')
                if r: m.requests_waiting = int(float(r[0]['value'][1]))

                # GPU 利用率 (DCGM)
                r = self._prom_query(f'DCGM_FI_DEV_GPU_UTIL{{{pod_selector}}}')
                if r: m.gpu_utilization = float(r[0]['value'][1])

                m.healthy = True
            except Exception:
                m.healthy = False

            self._metrics[backend.endpoint] = m

    def _prom_query(self, query):
        resp = requests.get(f'{self.prometheus_url}/api/v1/query', params={'query': query}, timeout=3)
        return resp.json().get('data', {}).get('result', [])
```

**自动扩缩容联动：**

当所有后端 GPU 利用率持续高位时，inference_router 通过 Cube Studio API 触发推理服务扩容。

```python
# myapp/inference_router/autoscaler.py
import requests, time, threading
from myapp import conf

class InferenceAutoScaler:
    """监控后端负载，触发 Cube Studio API 扩缩容"""

    def __init__(self, metrics_collector, registry):
        self.metrics = metrics_collector
        self.registry = registry
        # 阈值配置
        self.scale_up_gpu_threshold = 85       # GPU 利用率 > 85% 触发扩容
        self.scale_up_queue_threshold = 10     # 等待队列 > 10 触发扩容
        self.scale_down_gpu_threshold = 20     # GPU 利用率 < 20% 触发缩容
        self.cooldown_seconds = 300            # 扩缩容冷却期 5 分钟
        self.cube_api = conf.get('KFJ_MODEL_REPO_API_URL', 'http://kubeflow-dashboard.infra')
        self._last_scale = {}                  # service_name → timestamp

    def check_and_scale(self):
        """定期检查（每 30s），判断是否需要扩缩容"""
        for model_name, backends in self.registry.all_backends().items():
            if not backends:
                continue

            loads = [self.metrics.get_load(b) for b in backends]
            healthy_loads = [l for l in loads if l.healthy]
            if not healthy_loads:
                continue

            avg_gpu = sum(l.gpu_utilization for l in healthy_loads) / len(healthy_loads)
            max_queue = max(l.requests_waiting for l in healthy_loads)
            service_name = backends[0].name

            # 扩容判断
            if (avg_gpu > self.scale_up_gpu_threshold or max_queue > self.scale_up_queue_threshold):
                self._scale(service_name, direction='up')

            # 缩容判断 (仅当副本数 > 1)
            elif avg_gpu < self.scale_down_gpu_threshold and len(backends) > 1:
                self._scale(service_name, direction='down')

    def _scale(self, service_name, direction):
        # 冷却期检查
        last = self._last_scale.get(service_name, 0)
        if time.time() - last < self.cooldown_seconds:
            return

        # 调用 Cube Studio API 扩缩容
        # InferenceService 的 min_replicas/max_replicas 控制 HPA 范围
        # 这里直接 PATCH InferenceService 的 replicas
        try:
            resp = requests.patch(
                f'{self.cube_api}/inferenceservice_modelview/api/{service_name}',
                json={"action": "scale_up" if direction == 'up' else "scale_down"},
                headers={"Authorization": conf.get('INTERNAL_API_TOKEN', '')},
                timeout=10,
            )
            if resp.ok:
                self._last_scale[service_name] = time.time()
        except Exception as e:
            print(f"Autoscale failed for {service_name}: {e}")
```

#### `myapp/karmada/` — Karmada 操作封装

```python
# myapp/karmada/client.py
from myapp import conf
from myapp.utils.py.py_k8s import K8s

class KarmadaClient:
    def __init__(self):
        self.k8s = K8s(file_path=conf.get('KARMADA_KUBECONFIG'))

    def create_propagation_policy(self, namespace, name, resource_selectors, target_clusters, replica_scheduling=None):
        policy = {
            "apiVersion": "policy.karmada.io/v1alpha1",
            "kind": "PropagationPolicy",
            "metadata": {"name": f"{name}-pp", "namespace": namespace},
            "spec": {
                "resourceSelectors": resource_selectors,
                "placement": {
                    "clusterAffinity": {"clusterNames": target_clusters}
                }
            }
        }
        if replica_scheduling:
            policy["spec"]["placement"]["replicaScheduling"] = replica_scheduling

        return self.k8s.create_crd(
            group="policy.karmada.io", version="v1alpha1",
            plural="propagationpolicies", namespace=namespace, body=policy,
        )

    def create_override_policy(self, namespace, name, target_cluster, overriders):
        # ... 类似封装 ...
        pass

    def deploy_with_propagation(self, namespace, name, target_clusters, **deployment_kwargs):
        """一站式: 创建 Deployment + Service + PropagationPolicy"""
        self.k8s.create_deployment(namespace=namespace, name=name, **deployment_kwargs)
        self.k8s.create_service(namespace=namespace, name=name, ...)
        self.create_propagation_policy(
            namespace=namespace, name=name,
            resource_selectors=[
                {"apiVersion": "apps/v1", "kind": "Deployment", "name": name},
                {"apiVersion": "v1", "kind": "Service", "name": name},
            ],
            target_clusters=target_clusters,
        )
```

这个模块只是 `py_k8s.K8s` 的上层封装，专门处理 Karmada 特有的 CRD（PropagationPolicy、OverridePolicy）。

### 8.4 进程架构总览

所有进程共享一个 Docker 镜像，通过不同启动命令运行：

```
同一镜像: kubeflow-dashboard:2026.01.01
同一代码: /home/myapp/myapp/
│
├─ python myapp/run.py                       → Web Server (port 80)
│    └─ import myapp (Flask app, views, models, utils)
│
├─ celery ... beat                           → Celery Beat (定时任务调度)
│    └─ import myapp.tasks.celery_app
│         └─ import myapp (config, models, utils)
│
├─ celery ... worker                         → Celery Worker (异步任务执行)
│    └─ import myapp.tasks.celery_app
│         └─ import myapp (config, models, utils)
│
├─ python myapp/inference_router/run.py      → 推理路由 (port 8080)  [新增]
│    └─ import myapp (config, models.model_serving)
│    └─ import myapp.inference_router.strategies.*
│    └─ 不需要 py_k8s，不操作 K8s API
│
└─ python myapp/tools/watch_*.py             → 监控进程
     └─ import myapp (config, models, py_k8s)
```

### 8.5 代码变更汇总

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| **新建 — 推理路由进程 (自研调度)** | | |
| `myapp/inference_router/__init__.py` | 新建 | 模块初始化 |
| `myapp/inference_router/run.py` | 新建 | 进程入口 (Flask, port 8080) |
| `myapp/inference_router/proxy.py` | 新建 | OpenAI 兼容反向代理 (SSE 流式支持) |
| `myapp/inference_router/registry.py` | 新建 | 后端注册表 (从 InferenceService 表同步) |
| `myapp/inference_router/metrics.py` | 新建 | 后端负载采集 (Prometheus GPU%, queue depth) |
| `myapp/inference_router/health.py` | 新建 | 后端健康检查 |
| `myapp/inference_router/strategies/base.py` | 新建 | RoutingStrategy 抽象基类 |
| `myapp/inference_router/strategies/least_loaded.py` | 新建 | GPU 利用率最低优先 |
| `myapp/inference_router/strategies/least_queue.py` | 新建 | 队列最短优先 |
| `myapp/inference_router/strategies/round_robin.py` | 新建 | 轮询兜底 |
| `myapp/inference_router/strategies/latency_aware.py` | 新建 | P99 延迟最低优先 |
| `myapp/inference_router/config.py` | 新建 | 路由配置 |
| **新建 — Karmada 集成** | | |
| `myapp/karmada/__init__.py` | 新建 | 模块初始化 |
| `myapp/karmada/client.py` | 新建 | Karmada API 封装 |
| `myapp/karmada/policy.py` | 新建 | PropagationPolicy/OverridePolicy 模板 |
| **新建 — 部署配置** | | |
| `install/kubernetes/karmada/` | 新建 | Karmada 安装和集群注册脚本 |
| `install/kubernetes/litellm/` | 新建 | LiteLLM 部署 manifests |
| `install/kubernetes/juicefs/` | 新建 | JuiceFS CSI + StorageClass |
| `install/kubernetes/edge-cluster/` | 新建 | K3S 边缘集群初始化脚本 |
| **改动 — 现有文件** | | |
| `install/docker/config.py` | 改动 | 新增 KARMADA / LiteLLM / JuiceFS 配置 |
| `install/docker/docker-compose.yml` | 改动 | 新增 inference-router 服务 |
| `myapp/utils/py/py_k8s.py` | 改动 | 新增 `create_deployment_via_karmada()` |
| `myapp/views/view_inferenceserving.py` | 改动 | deploy() 增加 Karmada 分支 |
| `myapp/views/view_serving.py` | 改动 | deploy() 增加 Karmada 分支 |
| `myapp/views/view_total_resource.py` | 改动 | 资源视图增加边缘集群标识 |
| `myapp/models/model_serving.py` | 改动 | InferenceService 增加 endpoint 字段 |
| `myapp/models/model_team.py` | 改动 | Project.cluster 增加 ROLE 解析 |
| `myapp/tasks/schedules.py` | 改动 | 新增边缘集群健康同步定时任务 |

## 9. Decisions Made

| # | 问题 | 决定 | 对设计的影响 |
|---|------|------|-------------|
| 1 | 边缘集群规模 | 1-2 个 K3S，每集群 3-4 节点 | Karmada push 模式即可，无需 pull 模式 |
| 2 | 网络环境 | 内网直连 | 无需 VPN/mTLS，kubeconfig 直连 K3S API |
| 3 | 数据存储 | JuiceFS + Ceph | 管理集群和边缘共享文件系统，不需要数据亲和调度 |
| 4 | 推理流量入口 | LiteLLM (hub) → 自研调度 (hub) → 推理引擎 (spoke) | 自研调度是 request-level 路由，不是 pod scheduler |
| 5 | 自研调度粒度 | 调度推理 request，与 K8s pod 调度是两个层面 | 自研调度不操作 K8s API，是反向代理 |
| 6 | LLM 后端 | 当前接边缘自部署模型，未来加云端 API | LiteLLM config 优先配置边缘 vLLM 端点 |
| 7 | 推理负载均衡 | 在边缘集群内做 | 由自研调度进程在多个后端实例间路由，不做跨集群分片 |
| 8 | 推理引擎选型 | vLLM | metrics.py 基于 vLLM Prometheus 指标 (`vllm:gpu_cache_usage_perc`, `vllm:num_requests_running` 等) |
| 9 | Ceph 部署位置 | 独立部署 (3+ 台物理机/VM) | 不占用 K8S/K3S 集群资源，MON/OSD/RGW/MGR 独立运维 |
| 10 | 扩缩容联动 | 自动扩容 | inference_router 内置 autoscaler，GPU>85% 或队列>10 时调 Cube Studio API 扩容 |

## 10. Open Questions — 全部已解决

所有开放性问题已在讨论中达成决定，见 Section 9 Decisions Made。

---

## 11. Implementation Phases

建议按以下阶段实施：

### Phase 1: 基础设施层
- 独立部署 Ceph 集群 (MON×3 + OSD×3 + RGW×2 + MGR×2)
- 部署 K3S 边缘集群 (1-2 个, 每集群 3-4 节点)
- 在管理集群和边缘集群安装 JuiceFS CSI Driver
- 部署 Karmada 控制面, 以 push 模式注册 K3S 集群
- 边缘集群安装必要组件: Kubeflow Training Operator, Argo Workflow Controller, GPU Operator, Prometheus Agent

### Phase 2: 推理路由核心
- 开发 `myapp/inference_router/` 全部模块
  - `proxy.py`: OpenAI 兼容反向代理 + SSE 流式
  - `registry.py`: 从 InferenceService 表同步后端
  - `metrics.py`: vLLM Prometheus 指标采集
  - `strategies/`: least_loaded, least_queue, round_robin
  - `health.py`: 后端健康检查
- 在 docker-compose.yml 新增 inference-router 服务
- 部署 LiteLLM, 配置 model_list 指向 inference-router

### Phase 3: Cube Studio 集成
- `install/docker/config.py` 新增 CLUSTERS ROLE/KARMADA 配置
- `myapp/karmada/` 模块: PropagationPolicy 封装
- `view_inferenceserving.py` / `view_serving.py`: deploy() 增加 Karmada 分支
- `model_serving.py`: InferenceService 增加 endpoint 字段
- 边缘集群部署 vLLM 推理服务验证端到端流程

### Phase 4: 自动扩缩容与运维
- 开发 `autoscaler.py`: 阈值检测 + Cube Studio API 扩缩容
- `view_total_resource.py`: 资源视图增加边缘集群标识
- Grafana Dashboard: 边缘集群 GPU/vLLM 指标面板
- 告警规则: GPU 持续高位、推理延迟 P99 过高、后端不健康