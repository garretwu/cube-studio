# SRE Agent Web FAQ

## 1. Command Quick Reference

### 1.1 Host Manual Run

| Goal | Command |
| --- | --- |
| Check Python version | `python --version` |
| Create virtual env | `uv venv --python 3.11 .venv` |
| Activate virtual env | `source .venv/bin/activate` |
| Install Python deps | `uv pip install -r sre_agent/requirements.txt` |
| Install frontend deps | `cd sre_agent/frontend && npm install` |
| Start local web stack | `python sre_agent/scripts/start_frontend_backend.py` |

### 1.2 Docker

| Goal | Command |
| --- | --- |
| Build images | `bash sre_agent/docker/build_images.sh` |
| Start container | `bash sre_agent/docker/run_container.sh` |
| Follow logs | `docker logs -f sre-agent-web` |
| Inspect health | `docker inspect --format '{{json .State.Health}}' sre-agent-web` |
| Stop container | `docker stop sre-agent-web` |

### 1.3 Kubernetes

| Goal | Command |
| --- | --- |
| Apply raw manifests | `bash sre_agent/deploy/k8s/apply.sh` |
| List pods | `kubectl get pods` |
| List services | `kubectl get svc` |
| Check deployment | `kubectl describe deploy sre-agent-web` |
| Check pod details | `kubectl describe pod <pod-name>` |
| Read deployment logs | `kubectl logs deploy/sre-agent-web` |
| Port-forward frontend | `kubectl port-forward svc/sre-agent-web 18080:80` |

### 1.4 Helm

| Goal | Command |
| --- | --- |
| Install or upgrade | `helm upgrade --install sre-agent-web /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web --set image.repository=registry.example.com/cube-studio/sre-agent-web --set image.tag=202604100930 --set secret.create=true --set secret.sreOpenaiApiKey=your-real-key` |
| List releases | `helm list` |
| Show release status | `helm status sre-agent-web` |
| Render chart locally | `helm template sre-agent-web /home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web --set image.repository=registry.example.com/cube-studio/sre-agent-web --set image.tag=202604100930 --set secret.create=true --set secret.sreOpenaiApiKey=dummy-key` |

## 2. Local Runtime Issues

### 2.1 `python sre_agent/scripts/start_frontend_backend.py` fails with `vite: not found`

Reason:

- frontend dependencies are not installed

Fix:

```bash
cd /home/kevin/project/cube-studio/sre_agent/frontend
npm install

cd /home/kevin/project/cube-studio
python sre_agent/scripts/start_frontend_backend.py
```

### 2.2 `npm install` shows many `EBADENGINE` warnings

Reason:

- Node version is too old

Required:

- Node `>= 20`

Check:

```bash
node -v
npm -v
```

### 2.3 Local Python startup reports missing modules

Reason:

- virtual environment is not activated
- Python dependencies are not installed

Fix:

```bash
cd /home/kevin/project/cube-studio
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -r sre_agent/requirements.txt
```

## 3. Docker Issues

### 3.1 Container exits shortly after startup

Check:

```bash
docker logs <container-name>
docker inspect --format '{{json .State.Health}}' <container-name>
```

Common causes:

- `SRE_OPENAI_API_KEY` is missing and no fallback config is available
- host port mapping conflicts
- wrong timestamp tag is being used

### 3.2 Health check fails once at startup and then recovers

This is normal.

Reason:

- health check may run before Vite is fully ready

Expected outcome:

- later status changes to `healthy`

## 4. Kubernetes Issues

### 4.1 Pod stays in `CrashLoopBackOff`

Check:

```bash
kubectl logs deploy/sre-agent-web
kubectl describe pod <pod-name>
```

Common causes:

- Secret does not contain `SRE_OPENAI_API_KEY`
- image tag is wrong
- image was not pushed to the target registry
- cluster cannot pull the image

### 4.2 Pod is `Running` but page cannot be opened

Check:

```bash
kubectl get svc sre-agent-web
kubectl describe svc sre-agent-web
```

Notes:

- frontend Service port is `80`
- backend Service port is `8000`

For external access, use one of:

- Ingress
- NodePort
- `kubectl port-forward`

Example:

```bash
kubectl port-forward svc/sre-agent-web 18080:80
```

Then open:

```text
http://127.0.0.1:18080
```

## 5. Configuration Questions

### 5.1 Why can local startup work even if I did not export `SRE_OPENAI_API_KEY`

Reason:

- [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) currently contains `llm.api_key`

Recommended production practice:

- Docker environment variable
- Kubernetes Secret

### 5.2 Should I use raw YAML or Helm

Recommendation:

- raw YAML for quick validation and direct debugging
- Helm for reusable multi-environment deployment

Both delivery paths map to the same resource model:

- `Deployment`
- `Service`
- `ConfigMap`
- `Secret`
