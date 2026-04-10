# `feature/sre-c-core-infra` 分支 GitLab CI 说明

这份文档汇总了 `sre_agent` Web 工程在当前分支上的 GitLab CI 资产与关键规则。

主要使用说明：

- [PIPELINE_RUNBOOK.md](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/PIPELINE_RUNBOOK.md)

## 文件一览

| 文件 | 用途 |
| --- | --- |
| `ci/common.sh` | 提供日志、仓库认证、镜像命名、时间戳解析等公共函数。 |
| `ci/generate_metadata.sh` | 生成镜像时间戳、tag 和 `dist/pipeline.env` 元数据文件。 |
| `ci/resolve_latest_base_tag.sh` | 从 Nexus 查询最新的 `base-YYYYMMDDHHMM` 基础镜像 tag。 |
| `ci/build_base_image.sh` | 构建基础镜像。 |
| `ci/build_web_image.sh` | 基于最新可用基础镜像构建业务镜像。 |
| `ci/run_preview_container.sh` | 启动 preview 容器，自动规避端口和容器名冲突，并等待健康检查通过。 |
| `ci/validate_runtime_smoke.sh` | 执行容器启动、健康检查、后端 OpenAPI 和前端首页的环境验收。 |
| `ci/validate_business_suite.sh` | 在基础镜像环境中执行选定的后端与故障注入业务测试。 |
| `ci/push_images.sh` | 将生成的镜像推送到 Nexus，并输出 `docker pull` 地址。 |
| `ci/promote_release.sh` | 手动把已验证通过的业务镜像提升为正式 release tag。 |
| `ci/notify_feishu.sh` | 在满足条件时向飞书群机器人发送失败通知卡片。 |
| `ci/cleanup_docker_state.sh` | 清理过期 preview 容器、旧镜像和旧缓存，并保留关键最新版本。 |

## 流水线范围

根目录 [`.gitlab-ci.yml`](/home/kevin/project/cube-studio/.gitlab-ci.yml) 中和 `sre_agent` 相关的 job 只作用于分支：

`feature/sre-c-core-infra`

其他分支不会命中这套新的 SRE Agent 流水线规则。

普通提交场景下，这条流水线默认只在“业务相关改动”发生时创建和执行。

以下类型的改动会触发完整构建与 preview：

- `sre_agent/frontend/**`
- `sre_agent/agent/**`
- `sre_agent/api/**`
- `sre_agent/runtime/**`
- `sre_agent/remediation/**`
- `sre_agent/docker/**`
- `sre_agent/conf/**`
- `fault_injector/**`
- `lib/**`
- `sre_agent/requirements.txt`
- `sre_agent/scripts/start_frontend_backend.py`
- `.gitlab-ci.yml`

如果只是文档变更、无关轻改动或短时间重复提交，默认不会创建这条重流水线。

如果确实需要强制跑完整流水线，可以在手动触发时设置：

- `FORCE_FULL_PIPELINE=1`

## 必要 CI 变量

| 变量 | Visibility | 用途 |
| --- | --- | --- |
| `NEXUS_REGISTRY` | `Visible` | Nexus Docker 仓库地址，例如 `10.11.4.5:5000`。 |
| `NEXUS_USERNAME` | `Visible` | Docker 登录和 tag 查询 API 使用的用户名。 |
| `NEXUS_PASSWORD` | `Masked and hidden` | Docker 登录和 tag 查询 API 使用的密码或 token。 |
| `PREVIEW_DOCKER_HOST` | `Visible` | 可选，用于承载 preview 容器的远端 Docker 主机，例如 `tcp://10.10.10.20:2375`。 |
| `PREVIEW_PUBLIC_HOST` | `Visible` | 可选，用于在日志中输出对外可访问的 preview 地址。 |
| `SRE_OPENAI_API_KEY` | `Masked and hidden` | 可选，preview 运行时使用的 LLM key；未显式配置时允许从 `sre_agent/conf/config.yaml` 的 `llm.api_key` fallback。 |
| `FORCE_BASE_BUILD` | `Visible` | 可选，设为 `1` 时即使依赖未变化也强制重建基础镜像。 |
| `FORCE_FULL_PIPELINE` | `Visible` | 可选，设为 `1` 时即使当前改动不在业务相关路径中，也强制创建并执行完整流水线。 |
| `RELEASE_VERSION` | `Visible` | 可选，手动发版时指定版本号，例如 `1.0.1`；未指定时读取 `sre_agent/VERSION`。 |
| `FEISHU_WEBHOOK_URL` | `Masked and hidden` | 可选，飞书群机器人 webhook 地址。 |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | `Visible` | 可选，设为 `1` 时普通 commit 流水线失败也通知飞书；默认只通知非 commit 流程。 |

补充说明：

- 只有 `SRE_OPENAI_API_KEY` 支持在未显式声明时 fallback 到 [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 中的 `llm.api_key`
- `NEXUS_USERNAME`、`NEXUS_PASSWORD`、`FEISHU_WEBHOOK_URL` 这类 CI/CD 凭证仍然必须显式配置在 GitLab Variables 中
- 如果设置了 `PREVIEW_PUBLIC_HOST`，即使 preview 因 `PREVIEW_DOCKER_HOST` 不可达而回退到 runner 本地 Docker，日志中仍优先输出 `PREVIEW_PUBLIC_HOST:随机端口` 作为浏览器访问地址
- 默认镜像命名空间为 `sre_agent`，并按用途分目录：
  - preview：`10.11.4.5:5000/sre_agent/sre-agent-web-preview:<tag>`
  - weekly：`10.11.4.5:5000/sre_agent/sre-agent-web-weekly:<tag>`
  - release：`10.11.4.5:5000/sre_agent/sre-agent-web-release:<tag>`
  - 对应基础镜像同理，分别使用 `sre-agent-base-preview`、`sre-agent-base-weekly`、`sre-agent-base-release`

变量填写说明：

- `NEXUS_REGISTRY`：填写仓库地址本身，例如 `10.11.4.5:5000`
- `NEXUS_USERNAME`：填写真实仓库用户名，例如 `kevin`
- `NEXUS_PASSWORD`：填写真实仓库密码或 token 明文值
- `PREVIEW_PUBLIC_HOST`：填写浏览器可访问的主机 IP 或域名，例如 `10.11.4.5`
- `PREVIEW_DOCKER_HOST`：仅在需要通过远端 Docker API 起 preview 容器时填写，例如 `tcp://10.11.4.5:2375`
- `SRE_OPENAI_API_KEY`：填写真实 LLM `API key` 明文值，不是字段名，不是路径，也不是 `config.yaml` 中的键名
- `RELEASE_VERSION`：手动正式发版时填写版本号，例如 `1.0.1`

## Schedule 变量

这些定时任务需要在 GitLab UI 中创建，并指向同一个分支。

| 定时任务 | 分支 | 变量 |
| --- | --- | --- |
| 每周五 17:00 的周构建 | `feature/sre-c-core-infra` | `PIPELINE_KIND=weekly_build` |
| 每月或每两周一次的基础镜像兜底刷新 | `feature/sre-c-core-infra` | `PIPELINE_KIND=base_refresh` |
| 每周固定时间的清理任务 | `feature/sre-c-core-infra` | `PIPELINE_KIND=cleanup` |

## Tag 规则

| 镜像类型 | Tag 规则 |
| --- | --- |
| 基础镜像 | `base-YYYYMMDDHHMM` |
| 普通提交业务镜像 | `CI_COMMIT_SHORT_SHA-YYYYMMDDHHMM` |
| 周构建业务镜像 | `weekly-YYYYMMDDHHMM` |
| 手动正式发布镜像 | `VERSION-YYYYMMDDHHMM` |

## 发布策略

当前流水线把“快照发布”和“正式发版”拆成两层：

- `publish_preview_snapshot`：在 preview 成功后自动把 commit 或 weekly 镜像推送到 Nexus，供内部测试和联调共享。
- `promote_sre_agent_release`：手动执行，把已验证通过的业务镜像重新打成 `VERSION-YYYYMMDDHHMM`。
- 如果手动触发时没有提供 `RELEASE_VERSION`，就读取 `sre_agent/VERSION` 里的默认版本。

镜像获取说明：

- 预览对应的候选镜像：查看 `publish_preview_snapshot` job 日志中的 `docker pull ...`
- 正式 release 镜像：查看 `promote_sre_agent_release` job 日志末尾的发布 summary，其中会直接输出 `Base Pull` 和 `Web Pull`
- `weekly_build` 对应镜像：同样查看 `publish_preview_snapshot` job 日志，其中会输出 `weekly` 目录下的 `docker pull ...`

## 验证范围

release 门禁目前拆成两类 job：

- 环境验收：
  容器启动、Docker 健康检查、后端 `/openapi.json`、前端 `/`
- 业务验收：
  `sre_agent/tests/test_start_frontend_backend.py`、`fault_injector/tests/unit/features/scenarios/test_scenarios.py`、`python -m fault_injector list-scenarios`

GitLab 的短 commit ID 使用预定义变量：

`CI_COMMIT_SHORT_SHA`

官方定义是 `CI_COMMIT_SHA` 的前 8 位。
