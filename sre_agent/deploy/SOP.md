# SRE Agent Web SOP

## 1. Scope

This SOP covers three ways to run the SRE Agent web stack:

- Host manual run
- Docker container run
- Kubernetes run

The current cloud-native delivery model is:

- `Deployment` + `Service`

Helm chart support is also provided now for parameterized multi-environment deployment.

Related operational docs:

- [FAQ.md](/home/kevin/project/cube-studio/sre_agent/deploy/FAQ.md)
- [RELEASE_CHECKLIST.md](/home/kevin/project/cube-studio/sre_agent/deploy/RELEASE_CHECKLIST.md)

## 2. What Gets Built

Two images are built:

- `sre-agent-base:<YYYYMMDDHHMM>`
- `sre-agent-web:<YYYYMMDDHHMM>`

The base image contains system, Python, and Node dependencies.

The web image contains only the runtime code and startup scripts, and reuses the base image.

## 3. About `SRE_OPENAI_API_KEY`

`SRE_OPENAI_API_KEY` is the LLM runtime key used by the backend when the agent needs to call the configured model provider.

Why local startup may still work without you manually exporting it:

- the current [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) already contains `llm.api_key`
- startup loads that value into the runtime environment automatically

Recommended practice:

- do not rely on plaintext keys in config files for production
- for containers and Kubernetes, pass the key by environment variable or Secret

## 4. Host Manual Run

### 4.1 Files At A Glance

| File | Purpose |
| --- | --- |
| [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt) | Host-side Python dependency entrypoint for the SRE web stack. |
| [sre_agent/scripts/start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py) | Local one-command launcher that starts backend and frontend together. |

### 4.2 Prerequisites

- Python `>= 3.11`
- Node `>= 20`
- npm available
- `uv` installed

### 4.3 Create Python Environment

```bash
cd /home/kevin/project/cube-studio

uv venv --python 3.11 .venv
source .venv/bin/activate

uv pip install -r sre_agent/requirements.txt
```

### 4.4 Install Frontend Dependencies

```bash
cd /home/kevin/project/cube-studio/sre_agent/frontend
npm install
```

### 4.5 Start the Web Stack

```bash
cd /home/kevin/project/cube-studio
source .venv/bin/activate
python sre_agent/scripts/start_frontend_backend.py
```

Default ports:

- backend: `8000`
- frontend: `8080`

## 5. Docker Run

### 5.1 Files At A Glance

| File | Purpose |
| --- | --- |
| [sre_agent/docker/Dockerfile.base](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile.base) | Builds the dependency layer with apt, Python, and npm packages. |
| [sre_agent/docker/Dockerfile](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile) | Builds the runtime web image on top of the base image. |
| [sre_agent/docker/build_images.sh](/home/kevin/project/cube-studio/sre_agent/docker/build_images.sh) | Builds the two timestamp-tagged images and writes image metadata to `image.env`. |
| [sre_agent/docker/run_container.sh](/home/kevin/project/cube-studio/sre_agent/docker/run_container.sh) | Starts the web container with the expected env vars and port mappings. |
| [sre_agent/docker/container_entrypoint.sh](/home/kevin/project/cube-studio/sre_agent/docker/container_entrypoint.sh) | Container entrypoint that prepares runtime env and launches the stack. |
| [sre_agent/docker/run_web_stack.py](/home/kevin/project/cube-studio/sre_agent/docker/run_web_stack.py) | Container-only launcher that starts backend and frontend without changing local workflow. |
| [sre_agent/docker/healthcheck.py](/home/kevin/project/cube-studio/sre_agent/docker/healthcheck.py) | Verifies both backend and frontend are reachable before the container is marked healthy. |
| [sre_agent/docker/image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env) | Stores the most recent build metadata so run and deploy steps can reuse the same timestamp tag. |
| [.dockerignore](/home/kevin/project/cube-studio/.dockerignore) | Restricts Docker build context to only the files needed for this service. |

### 5.2 Build Images

```bash
cd /home/kevin/project/cube-studio
bash sre_agent/docker/build_images.sh
```

This creates a timestamp tag like:

- `cube-studio/sre-agent-base:202604100930`
- `cube-studio/sre-agent-web:202604100930`

It also writes image metadata to:

- [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env)

### 5.3 Start the Container

```bash
cd /home/kevin/project/cube-studio
export SRE_OPENAI_API_KEY=your-real-key
bash sre_agent/docker/run_container.sh
```

Default host ports:

- backend: `8000`
- frontend: `8080`

Optional overrides:

```bash
HOST_BACKEND_PORT=18000 \
HOST_FRONTEND_PORT=18080 \
CONTAINER_NAME=sre-agent-web-dev \
SRE_OPENAI_API_KEY=your-real-key \
bash sre_agent/docker/run_container.sh
```

### 5.4 Inspect Status

```bash
docker logs -f sre-agent-web
docker inspect --format '{{json .State.Health}}' sre-agent-web
```

## 6. Kubernetes Run

### 6.1 Files At A Glance

| File | Purpose |
| --- | --- |
| [sre_agent/deploy/k8s/configmap.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/configmap.yaml) | Provides non-secret runtime environment variables for the Kubernetes workload. |
| [sre_agent/deploy/k8s/secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml) | Example Secret manifest for the LLM key and other sensitive values. |
| [sre_agent/deploy/k8s/service.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/service.yaml) | Exposes frontend and backend ports inside the cluster. |
| [sre_agent/deploy/k8s/deployment.template.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/deployment.template.yaml) | Parameterized Deployment template that references the chosen image tag. |
| [sre_agent/deploy/k8s/render_deployment.py](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/render_deployment.py) | Renders a concrete Deployment manifest from the template and image metadata. |
| [sre_agent/deploy/k8s/apply.sh](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/apply.sh) | Applies the rendered Kubernetes manifests in the intended order. |

### 6.2 Delivery Model

Current delivery is:

- one `Deployment`
- one `Service`
- one `ConfigMap`
- one `Secret`

Two deployment paths are supported:

- raw Kubernetes YAML
- Helm chart

Raw YAML is simpler for direct debugging.

Helm is better for repeated environment rollout.

### 6.3 Build and Push Image

Example:

```bash
cd /home/kevin/project/cube-studio
IMAGE_REGISTRY=registry.example.com \
IMAGE_NAMESPACE=cube-studio \
bash sre_agent/docker/build_images.sh
```

Push the generated images:

```bash
source sre_agent/docker/image.env
docker push "${BASE_TAGGED}"
docker push "${APP_TAGGED}"
```

### 6.4 Create Secret

Edit:

- [secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml)

Then apply:

```bash
kubectl apply -f sre_agent/deploy/k8s/secret.example.yaml
```

### 6.5 Render and Apply

If `sre_agent/docker/image.env` exists, the deployment renderer will reuse its timestamp tag automatically.

```bash
cd /home/kevin/project/cube-studio
bash sre_agent/deploy/k8s/apply.sh
```

If you want to override the image reference explicitly:

```bash
cd /home/kevin/project/cube-studio
IMAGE_REGISTRY=registry.example.com \
IMAGE_NAMESPACE=cube-studio \
IMAGE_TAG=202604100930 \
bash sre_agent/deploy/k8s/apply.sh
```

### 6.6 Verify

```bash
kubectl get pods
kubectl get svc
kubectl describe deploy sre-agent-web
kubectl logs deploy/sre-agent-web
```

## 7. Helm Run

### 7.1 Files At A Glance

| File | Purpose |
| --- | --- |
| [sre_agent/deploy/helm/sre-agent-web/Chart.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/Chart.yaml) | Defines the Helm chart metadata. |
| [sre_agent/deploy/helm/sre-agent-web/values.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/values.yaml) | Holds the default configurable parameters for Helm installs. |
| [sre_agent/deploy/helm/sre-agent-web/templates/configmap.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/configmap.yaml) | Helm template for non-secret runtime configuration. |
| [sre_agent/deploy/helm/sre-agent-web/templates/secret.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/secret.yaml) | Helm template for optional Secret creation. |
| [sre_agent/deploy/helm/sre-agent-web/templates/service.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/service.yaml) | Helm template for the cluster Service. |
| [sre_agent/deploy/helm/sre-agent-web/templates/deployment.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/deployment.yaml) | Helm template for the application Deployment. |
| [sre_agent/deploy/helm/sre-agent-web/templates/_helpers.tpl](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/templates/_helpers.tpl) | Shared naming and label helpers used by the chart templates. |

### 7.2 Chart Location

- [Chart.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/Chart.yaml)

### 7.3 Install Or Upgrade

```bash
helm upgrade --install sre-agent-web \
  /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web \
  --set image.repository=registry.example.com/cube-studio/sre-agent-web \
  --set image.tag=202604100930 \
  --set secret.create=true \
  --set secret.sreOpenaiApiKey=your-real-key
```

If your Secret already exists:

```bash
helm upgrade --install sre-agent-web \
  /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web \
  --set image.repository=registry.example.com/cube-studio/sre-agent-web \
  --set image.tag=202604100930 \
  --set secret.create=false \
  --set secret.name=sre-agent-web-secrets
```

### 7.4 Verify Helm Release

```bash
helm list
helm status sre-agent-web
kubectl get pods
kubectl get svc
```

## 8. Recommended Operating Mode

Recommended by environment:

- local developer workstation: host manual run
- single-host validation or demo: Docker scripts
- shared or production-like environment: Kubernetes `Deployment + Service`
- multi-environment reusable delivery: Helm chart

For the current project shape, raw `Deployment + Service` remains the simplest cloud-native baseline. Helm is the better choice once you need repeatable staging, pre-prod, and production parameter sets.
