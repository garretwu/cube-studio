# SRE Agent Release Notes

2026-04-10 19:20

**Scope**

- 基于现有飞书失败告警链路，新增 preview、snapshot、release 成功后的飞书卡片通知
- 成功卡片直接带出页面访问地址、镜像拉取命令和 release tag，降低团队反复翻流水线日志的成本
- 保留原有流水线日志摘要，同时让飞书成为更直接的“发布与预览入口”
- 为飞书成功通知补充可控开关 `FEISHU_NOTIFY_ON_SUCCESS`，默认开启，可按需关闭
- 优化成功卡片视觉效果，不同类型使用不同 header 颜色，并补充更直白的中文标题与摘要分隔
- 对飞书 webhook 响应增加业务码校验，避免 HTTP 200 但消息实际被飞书拒绝时误报“发送成功”
- 修复成功通知链路在 `source dist/*.env` 时被带空格的 `docker pull ...` 值打断的问题，改为 env 文件仅保存无空格镜像引用，再由通知脚本动态生成拉取命令

**Code**

GitLab CI:

- [/.gitlab-ci.yml](/home/kevin/project/cube-studio/.gitlab-ci.yml)
- [sre_agent/deploy/gitlab/ci/notify_feishu.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/notify_feishu.sh)
- [sre_agent/deploy/gitlab/ci/push_images.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/push_images.sh)
- [sre_agent/deploy/gitlab/ci/promote_release.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/promote_release.sh)

Docs:

- [sre_agent/docs/gitlab/README.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md)
- [sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)
- [sre_agent/docs/RELEASE_NOTES.md](/home/kevin/project/cube-studio/sre_agent/docs/RELEASE_NOTES.md)

**Runtime**

Feishu success cards:

- `preview` 卡片包含 `Frontend URL`、`Backend URL`、`Image Pull`
- `snapshot` 卡片包含候选镜像拉取地址，并尽量附带 preview 访问地址
- `release` 卡片包含 `Release Tag`、`Web Pull`、`Base Pull`

2026-04-10 18:40

**Scope**

- 优化 preview 容器保留策略，普通提交不再只保留单个 preview，而是默认保留最近 `3` 个实例，避免测试和评审中的版本被研发新提交立即替换
- 新增 `weekly_preview_sre_agent_web`，让 `weekly_build` 除了发布 weekly 镜像外，也会生成一个可直接访问的稳定 weekly preview
- 调整 cleanup 逻辑，普通提交 preview 默认保留最近 `3` 个，weekly preview 保留最新 `1` 个，兼顾资源控制与评审可用性
- 更新 GitLab 流水线文档，补充 weekly preview 的访问方式与新的 preview 保留策略

**Code**

GitLab CI:

- [/.gitlab-ci.yml](/home/kevin/project/cube-studio/.gitlab-ci.yml)
- [sre_agent/deploy/gitlab/ci/run_preview_container.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/run_preview_container.sh)
- [sre_agent/deploy/gitlab/ci/cleanup_docker_state.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/cleanup_docker_state.sh)

Docs:

- [sre_agent/docs/gitlab/README.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md)
- [sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)
- [sre_agent/docs/RELEASE_NOTES.md](/home/kevin/project/cube-studio/sre_agent/docs/RELEASE_NOTES.md)

**Runtime**

Preview behavior:

- 普通提交 preview 默认保留最近 `3` 个实例
- weekly preview 默认保留最新 `1` 个实例
- `weekly_build` 结束后可在 `weekly_preview_sre_agent_web` 日志中直接获取 `Frontend URL`

2026-04-10 17:05

**Scope**

- 修复 `weekly_build` 下 `validate_sre_agent_business` 在测试通过后仍误报失败的问题
- 将 `fault_injector list-scenarios` 的输出统一重定向到检查文件，避免 Rich 表格写入 `stderr` 时造成场景校验误判
- 修正 base 镜像回退拉取时的旧变量引用，确保按当前 lane 拆分后的镜像名正确解析与提示

**Code**

GitLab CI:

- [sre_agent/deploy/gitlab/ci/validate_business_suite.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_business_suite.sh)
- [sre_agent/deploy/gitlab/ci/resolve_latest_base_tag.sh](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/resolve_latest_base_tag.sh)

Docs:

- [sre_agent/docs/RELEASE_NOTES.md](/home/kevin/project/cube-studio/sre_agent/docs/RELEASE_NOTES.md)

**Runtime**

Weekly validation behavior:

- `validate_sre_agent_business` 现在会稳定校验 `rdma_link_flap` 场景是否出现在 CLI 输出中
- `weekly_build` 与 `base_refresh` 的业务验收 job 不再出现“日志看起来通过但 job 最终失败”的假阴性
- base 镜像不存在时，回退拉取逻辑会使用当前 lane 对应的镜像名


2026-04-10 14:10

**Scope**

- 修复 `weekly_build` / `base_refresh` 因依赖不存在的 `preview_sre_agent_web` 而导致 `yaml invalid` 的问题，确保 schedule 流程可以正常创建和执行
- 优化 preview 容器生命周期管理，每次创建新 preview 前会先清理当前分支历史 preview 容器，定时 `cleanup` 任务也只保留最新的 preview 容器，避免历史容器长期堆积并造成排障误判
- 调整分支流水线发布策略，`preview` 成功后自动上传 `snapshot` 镜像，便于内部测试和调试共享
- 保留正式 `release` 为手动 promotion，继续作为人工确认后的正式交付动作
- 镜像默认命名空间从 `cube-studio` 统一调整为 `sre_agent`
- 预览镜像发布 job 命名收敛为 `publish_preview_snapshot`，减少 UI 中的冗余项目前缀
- 在 `sre_agent` 命名空间下按用途拆分镜像目录，分别落到 `preview`、`weekly`、`release` 目录，避免与日常提交或正式发版镜像混在一起
- `publish_preview_snapshot` 和 `promote_sre_agent_release` 的日志 summary 均补充镜像获取地址，便于团队直接复制 `docker pull` 命令
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

- `sre_agent/sre-agent-base:<YYYYMMDDHHMM>`

Web image:

- `sre_agent/sre-agent-web:<YYYYMMDDHHMM>`

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
