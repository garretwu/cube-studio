# SRE Agent Release Notes

2026-04-10 14:10

**Scope**

- GitLab CI 联调阶段继续收敛 `shell runner` 行为，移除镜像 `tar` artifacts，改为优先复用本机 Docker image，并在 cache miss 时原地重建
- 调整 `preview` 与 `release` 的阶段关系，普通提交场景下改为先自动 `preview`，再由人工确认后手动触发后续验证与发布
- 优化 `preview` 成功提示，在 job 日志末尾直接输出 `Frontend URL`、`Backend URL`、容器名和 TTL，方便测试同学直接打开浏览器验证
- 修复 `PREVIEW_PUBLIC_HOST` 输出逻辑，即使 preview 因 `PREVIEW_DOCKER_HOST` 不可达而回退到 runner 本地 Docker，仍优先输出配置的公网或内网可达主机地址，而不是固定回落到 `127.0.0.1`
- 修复 GitLab CI helper 脚本变更未触发流水线的问题，后续 `sre_agent/deploy/gitlab/ci/**` 下的改动也会自动触发这条分支流水线

**Code**

GitLab CI:

- [/.gitlab-ci.yml](/home/kevin/project/cube-studio/.gitlab-ci.yml)
- [sre_agent/deploy/gitlab/ci/common.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/common.sh)
- [sre_agent/deploy/gitlab/ci/build_base_image.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/build_base_image.sh)
- [sre_agent/deploy/gitlab/ci/build_web_image.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/build_web_image.sh)
- [sre_agent/deploy/gitlab/ci/run_preview_container.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/run_preview_container.sh)
- [sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh)
- [sre_agent/deploy/gitlab/ci/validate_business_suite.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_business_suite.sh)
- [sre_agent/deploy/gitlab/ci/push_images.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/push_images.sh)
- [sre_agent/deploy/gitlab/ci/promote_release.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/promote_release.sh)

Docs:

- [sre_agent/docs/gitlab/README.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md)
- [sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)
- [sre_agent/docs/RELEASE_NOTES.md](/home/kevin/project/cube-studio/sre_agent/docs/RELEASE_NOTES.md)

**Runtime**

Preview behavior:

- 优先复用 shell runner 本机 Docker image，不再上传镜像 `tar` artifacts
- preview 成功后在 job 日志末尾打印可访问地址摘要
- 若配置了 `PREVIEW_PUBLIC_HOST`，日志优先输出该主机地址加动态端口
- 普通提交场景下，`release` 相关 job 改为人工确认 preview 后手动触发

2026-04-10 10:30

**Scope**

- `sre_agent` 补齐容器化交付能力，形成 `sre-agent-base` 与 `sre-agent-web` 两层镜像结构
- 业务镜像收敛为最小运行集，只保留 `sre_agent`、`fault_injector`、`lib` 等运行时需要的内容
- 容器运行链路补齐健康检查、启动入口和本地运行脚本，支持本地快速验证页面效果
- 本地容器运行支持 `SRE_OPENAI_API_KEY` 优先读取环境变量，未显式提供时 fallback 到 `sre_agent/conf/config.yaml`
- 本地容器默认 Host 端口调整到高位端口，并在启动前自动检查端口占用，尽量避免与 `80`、`443`、`8080`、`18080`、`6443` 等常用端口冲突
- 明确交付原则：正式发布优先使用 GitLab 流水线产出的镜像，本地手动构建仅用于功能验证和调试

**Code**

Frontend / Web runtime:

- [sre_agent/docker/Dockerfile](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile)
- [sre_agent/docker/Dockerfile.base](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile.base)
- [sre_agent/docker/run_container.sh](/home/kevin/project/cube-studio/sre_agent/docker/run_container.sh)
- [sre_agent/docker/container_entrypoint.sh](/home/kevin/project/cube-studio/sre_agent/docker/container_entrypoint.sh)
- [sre_agent/docker/run_web_stack.py](/home/kevin/project/cube-studio/sre_agent/docker/run_web_stack.py)
- [sre_agent/docker/healthcheck.py](/home/kevin/project/cube-studio/sre_agent/docker/healthcheck.py)

Backend / startup:

- [sre_agent/scripts/start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py)
- [sre_agent/tests/test_start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/tests/test_start_frontend_backend.py)

Docs:

- [sre_agent/docs/deploy/SOP.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/SOP.md)
- [sre_agent/docs/deploy/FAQ.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/FAQ.md)
- [sre_agent/docs/deploy/RELEASE_CHECKLIST.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/RELEASE_CHECKLIST.md)

**Runtime**

Base image:

- `cube-studio/sre-agent-base:<YYYYMMDDHHMM>`

Web image:

- `cube-studio/sre-agent-web:<YYYYMMDDHHMM>`

Default local container access:

- frontend: `http://127.0.0.1:28080`
- backend OpenAPI: `http://127.0.0.1:28000/openapi.json`


2026-04-10 00:15

**Scope**

- 为 `feature/sre-c-core-infra` 分支设计独立的 GitLab CI 流水线，避免影响其他分支
- 流水线覆盖 `build`、`preview`、`release`、`cleanup` 四个阶段
- 基础镜像仅在依赖变更或 `base_refresh` 定时任务中重建，业务镜像在普通提交与定时任务中分别打出对应 tag
- `preview` 支持自动创建预览容器，并增加 TTL 管理
- `release` 增加环境验收与业务验收门禁，验收通过后才推送 snapshot 镜像
- 正式 release 改为手动 `promote_sre_agent_release`，tag 规则为 `VERSION-YYYYMMDDHHMM`
- 增加 `weekly_build`、`base_refresh`、`cleanup` 三类 schedule 设计
- 集成飞书群机器人失败通知，并升级为交互卡片，支持按失败类型区分标题、颜色、按钮文案和建议动作
- 明确变量规则：只有 `SRE_OPENAI_API_KEY` 可从 `config.yaml` fallback，其他 CI/CD 凭证必须显式配置在 GitLab Variables 中

**Code**

GitLab CI:

- [/.gitlab-ci.yml](/home/kevin/project/cube-studio/.gitlab-ci.yml)
- [sre_agent/deploy/gitlab/ci/common.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/common.sh)
- [sre_agent/deploy/gitlab/ci/generate_metadata.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/generate_metadata.sh)
- [sre_agent/deploy/gitlab/ci/resolve_latest_base_tag.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/resolve_latest_base_tag.sh)
- [sre_agent/deploy/gitlab/ci/build_base_image.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/build_base_image.sh)
- [sre_agent/deploy/gitlab/ci/build_web_image.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/build_web_image.sh)
- [sre_agent/deploy/gitlab/ci/run_preview_container.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/run_preview_container.sh)
- [sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh)
- [sre_agent/deploy/gitlab/ci/validate_business_suite.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_business_suite.sh)
- [sre_agent/deploy/gitlab/ci/push_images.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/push_images.sh)
- [sre_agent/deploy/gitlab/ci/promote_release.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/promote_release.sh)
- [sre_agent/deploy/gitlab/ci/cleanup_docker_state.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/cleanup_docker_state.sh)
- [sre_agent/deploy/gitlab/ci/notify_feishu.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/notify_feishu.sh)

Cloud-native delivery:

- [sre_agent/deploy/k8s/configmap.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/configmap.yaml)
- [sre_agent/deploy/k8s/secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml)
- [sre_agent/deploy/k8s/service.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/service.yaml)
- [sre_agent/deploy/k8s/deployment.template.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/deployment.template.yaml)
- [sre_agent/deploy/k8s/render_deployment.py](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/render_deployment.py)
- [sre_agent/deploy/k8s/apply.sh](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/apply.sh)
- [sre_agent/deploy/helm/sre-agent-web/Chart.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/Chart.yaml)
- [sre_agent/deploy/helm/sre-agent-web/values.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/helm/sre-agent-web/values.yaml)

Docs:

- [sre_agent/README.md](/home/kevin/project/cube-studio/sre_agent/README.md)
- [sre_agent/docs/gitlab/README.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md)
- [sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)

**Runtime**

Snapshot tag rules:

- commit web image: `CI_COMMIT_SHORT_SHA-YYYYMMDDHHMM`
- weekly web image: `weekly-YYYYMMDDHHMM`
- manual release image: `VERSION-YYYYMMDDHHMM`

Base tag rule:

- `base-YYYYMMDDHHMM`

Preview runtime policy:

- unique container name
- automatic Host port conflict avoidance
- TTL cleanup

Failure notification:

- Feishu interactive card
- type-specific title, color, button label, and suggested next action
