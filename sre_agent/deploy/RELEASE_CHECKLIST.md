# SRE Agent Web Release Checklist

## 1. Pre-Build

- [ ] Confirm local code changes are complete and reviewed.
- [ ] Confirm Python version is `>= 3.11`.
- [ ] Confirm Node version is `>= 20`.
- [ ] Confirm frontend dependencies can install successfully.
- [ ] Confirm Python dependencies can install successfully from [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt).
- [ ] Confirm local startup still works with [start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py).
- [ ] Confirm [SOP.md](/home/kevin/project/cube-studio/sre_agent/deploy/SOP.md) and [FAQ.md](/home/kevin/project/cube-studio/sre_agent/deploy/FAQ.md) match the current delivery flow.

## 2. Local Validation

- [ ] Create or activate the local Python virtual environment.
- [ ] Run `uv pip install -r sre_agent/requirements.txt`.
- [ ] Run `cd sre_agent/frontend && npm install`.
- [ ] Start local stack with `python sre_agent/scripts/start_frontend_backend.py`.
- [ ] Confirm backend responds on `/openapi.json`.
- [ ] Confirm frontend page opens successfully.

## 3. Image Build

- [ ] Run `bash sre_agent/docker/build_images.sh`.
- [ ] Confirm exactly two timestamp-tagged images were built:
- [ ] `sre-agent-base:$YYYYMMDDHHMM`
- [ ] `sre-agent-web:$YYYYMMDDHHMM`
- [ ] Confirm [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env) was updated with the latest timestamp tag.
- [ ] Confirm the base image is used as the parent for the web image.

## 4. Container Validation

- [ ] Export `SRE_OPENAI_API_KEY` if the target environment does not inject it elsewhere.
- [ ] Run `bash sre_agent/docker/run_container.sh`.
- [ ] Confirm the container starts successfully.
- [ ] Confirm `docker inspect --format '{{json .State.Health}}' sre-agent-web` becomes `healthy`.
- [ ] Confirm frontend is reachable on the mapped host port.
- [ ] Confirm backend is reachable on the mapped host port.
- [ ] Stop the validation container after checks complete.

## 5. Registry Push

- [ ] Source [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env).
- [ ] Push `${BASE_TAGGED}` to the target registry.
- [ ] Push `${APP_TAGGED}` to the target registry.
- [ ] Confirm the target registry shows the new timestamp tag.

## 6. Kubernetes Raw YAML Release

- [ ] Confirm [secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml) has been adapted into a real Secret manifest or an existing Secret is already present.
- [ ] Confirm the deployment image tag matches the pushed timestamp tag.
- [ ] Run `bash sre_agent/deploy/k8s/apply.sh`.
- [ ] Confirm Deployment rollout succeeds.
- [ ] Confirm Pods become `Running` and `Ready`.
- [ ] Confirm Service is created successfully.
- [ ] Confirm frontend can be reached through Service, Ingress, or port-forward.

## 7. Helm Release

- [ ] Confirm Helm values use the correct image repository and timestamp tag.
- [ ] Confirm Secret strategy is correct:
- [ ] `secret.create=true` with inline value, or
- [ ] `secret.create=false` with an existing Secret name.
- [ ] Run `helm upgrade --install`.
- [ ] Confirm `helm status` is healthy.
- [ ] Confirm Pods become `Running` and `Ready`.
- [ ] Confirm Service exposure matches expectation.

## 8. Post-Release Verification

- [ ] Confirm backend `/openapi.json` is reachable in the target environment.
- [ ] Confirm frontend home page is reachable in the target environment.
- [ ] Confirm no immediate restart loop occurs.
- [ ] Confirm health checks stay green after the first startup window.
- [ ] Confirm logs do not show missing dependency errors.
- [ ] Confirm logs do not show missing configuration or secret errors.

## 9. Rollback Readiness

- [ ] Keep the previous known-good timestamp tag recorded.
- [ ] Confirm rollback command or manifest can point back to the previous image tag quickly.
- [ ] Confirm the previous image still exists in the registry before releasing.
