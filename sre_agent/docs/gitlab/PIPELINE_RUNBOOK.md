# SRE Agent 流水线使用手册

这份文档说明 `sre_agent` 在分支：

`feature/sre-c-core-infra`

上的 GitLab CI 工作流、阶段与 job 职责、使用方式，以及 GitLab 侧需要准备的配置。

## 1. 适用范围

这套流水线是严格按分支隔离的。

- 分支：`feature/sre-c-core-infra`
- 镜像地址格式：`10.11.4.5:5000/<namespace>/<image-name>:<tag>`
- 目标：
  自动构建 `sre_agent` Web 工程镜像，提供 preview 容器，执行环境与业务验收，将验证通过的镜像发布到 Nexus，并支持手动提升为正式 release

其他分支不会命中这套流程，入口限制由 [`.gitlab-ci.yml`](/home/kevin/project/cube-studio/.gitlab-ci.yml) 中的 `workflow: rules` 控制。

## 2. 工作流概览

流水线包含 4 个阶段：

1. `build`
2. `preview`
3. `release`
4. `cleanup`

普通提交流程：

1. 生成镜像元数据和 tag
2. 仅在依赖输入变化时重建基础镜像
3. 使用最新可用基础镜像构建业务镜像
4. 启动 preview 容器并输出访问地址
5. 执行环境验收和业务验收
6. 将验证通过的快照镜像推送到 Nexus
7. 如有需要，再手动把本次验证通过的版本提升为正式 release

普通提交默认只在“业务相关改动”发生时才会进入这套完整流程。

如果只是文档改动、说明补充或其他不会影响运行时行为的轻改动，流水线默认不会创建，从而避免无意义的镜像构建和 preview 资源消耗。

定时流程：

- `weekly_build`：构建周镜像，验证通过后发布
- `base_refresh`：强制刷新基础镜像，再重建业务镜像并验证发布
- `cleanup`：清理过期 preview 容器与陈旧 Docker 资源

## 3. 阶段与 Job 说明

### 3.1 Build 阶段

| Job | 作用 | 触发方式 |
| --- | --- | --- |
| `prepare_sre_agent_metadata` | 生成统一的时间戳、镜像 tag 和流水线元数据。 | 普通提交、`weekly_build`、`base_refresh` |
| `build_sre_agent_base` | 仅在依赖输入变化或显式强制时构建基础镜像。 | 依赖变化、`FORCE_BASE_BUILD=1`、`base_refresh` |
| `build_sre_agent_web` | 构建普通提交场景的业务镜像。 | 普通提交 |
| `weekly_build_sre_agent_web` | 构建周构建业务镜像。 | `weekly_build` 定时任务 |
| `refresh_build_sre_agent_web` | 在基础镜像刷新流程中重建业务镜像。 | `base_refresh` 定时任务 |

会触发基础镜像重建的依赖输入包括：

- [fault_injector/requirements.txt](/home/kevin/project/cube-studio/fault_injector/requirements.txt)
- [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt)
- [sre_agent/docker/Dockerfile.base](/home/kevin/project/cube-studio/sre_agent/docker/Dockerfile.base)
- [sre_agent/frontend/package.json](/home/kevin/project/cube-studio/sre_agent/frontend/package.json)
- [sre_agent/frontend/package-lock.json](/home/kevin/project/cube-studio/sre_agent/frontend/package-lock.json)

### 3.2 Preview 阶段

| Job | 作用 | 触发方式 |
| --- | --- | --- |
| `preview_sre_agent_web` | 启动 preview 容器，规避容器名和端口冲突，并等待健康检查通过。 | 普通提交 |

Preview 规则：

- 容器名按 pipeline/job 自动唯一化
- 后端和前端端口从可配置范围内随机选择
- preview TTL 默认 72 小时
- TTL 到期后由 cleanup 统一清理

### 3.3 Release 阶段

| Job | 作用 | 触发方式 |
| --- | --- | --- |
| `validate_sre_agent_runtime` | 验证环境可用性，包括容器启动、后端接口和前端页面。 | 普通提交、`weekly_build`、`base_refresh` |
| `validate_sre_agent_business` | 验证后端和故障注入模块的核心业务行为。 | 普通提交、`weekly_build`、`base_refresh` |
| `publish_sre_agent_snapshot` | 将验证通过的快照镜像推送到 Nexus，并输出 `docker pull` 地址。 | 两类验收全部通过后 |
| `promote_sre_agent_release` | 手动提升为正式 release 镜像。 | 手动触发 |

当前 release 门禁拆成两层：

- 环境验收：
  容器健康、后端 `/openapi.json`、前端 `/`
- 业务验收：
  启动脚本相关测试和故障注入场景相关测试

### 3.4 Cleanup 阶段

| Job | 作用 | 触发方式 |
| --- | --- | --- |
| `cleanup_sre_agent_runtime` | 清理过期 preview 容器、7 天前的旧镜像和旧缓存，同时保留关键最新版本。 | `cleanup` 定时任务 |

当前保留策略：

- 删除 TTL 已过期的 preview 容器
- 删除 7 天前的受管 Docker 资源
- 保留最新的基础镜像
- 保留最新的普通提交快照镜像
- 保留最新的 weekly 镜像
- 保留最新的正式 release 镜像

## 4. Tag 策略

| 镜像类型 | Tag 规则 | 示例 |
| --- | --- | --- |
| 基础镜像 | `base-YYYYMMDDHHMM` | `base-202604101730` |
| 普通提交业务镜像 | `CI_COMMIT_SHORT_SHA-YYYYMMDDHHMM` | `a1b2c3d4-202604101735` |
| 周构建业务镜像 | `weekly-YYYYMMDDHHMM` | `weekly-202604101700` |
| 手动正式发布镜像 | `VERSION-YYYYMMDDHHMM` | `1.0.1-202604101800` |

短 commit ID 使用 GitLab 预定义变量：

`CI_COMMIT_SHORT_SHA`

默认 release 版本来源：

- [sre_agent/VERSION](/home/kevin/project/cube-studio/sre_agent/VERSION)

如果手动触发时提供了 `RELEASE_VERSION`，就以传入值为准。

## 5. GitLab 配置清单

### 5.1 项目变量

请在 GitLab 的 `Settings -> CI/CD -> Variables` 中创建这些变量。

| 变量 | 是否必需 | 建议值 | 用途 |
| --- | --- | --- | --- |
| `NEXUS_REGISTRY` | 是 | `10.11.4.5:5000` | Nexus Docker 仓库地址 |
| `NEXUS_USERNAME` | 是 | `kevin` | Nexus 登录用户名 |
| `NEXUS_PASSWORD` | 是 | masked + protected | Nexus 登录密码或 token |
| `PREVIEW_DOCKER_HOST` | 推荐 | runner 可访问的 Docker 主机 | 让 preview 容器在 job 结束后仍可继续使用 |
| `PREVIEW_PUBLIC_HOST` | 推荐 | preview 主机 IP 或域名 | 用于输出对外访问地址 |
| `SRE_OPENAI_API_KEY` | 可选 | masked | 如果希望 preview 真正连通 LLM，可在这里配置运行时 key；未显式配置时允许 fallback 到 `sre_agent/conf/config.yaml` 的 `llm.api_key` |
| `FORCE_BASE_BUILD` | 可选 | `1` | 即使依赖未变化也强制重建基础镜像 |
| `FORCE_FULL_PIPELINE` | 可选 | `1` | 即使当前提交不在业务相关路径中，也强制创建并执行完整流水线 |
| `RELEASE_VERSION` | 可选 | 手动输入 | 手动 release promotion 时使用 |
| `FEISHU_WEBHOOK_URL` | 推荐 | masked + protected | 飞书群机器人 webhook 地址 |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | 可选 | `0` | 设为 `1` 时普通提交失败也发飞书通知 |

安全建议：

- 不要把 `NEXUS_PASSWORD` 写进仓库
- `NEXUS_PASSWORD` 和 `FEISHU_WEBHOOK_URL` 都建议维护为 masked + protected 变量
- 如果以后把 Nexus 密码替换成 token，优先复用同一个变量名，避免脚本再改一遍

变量规则补充：

- 只有 `SRE_OPENAI_API_KEY` 支持在未显式声明时，从 [config.yaml](/home/kevin/project/cube-studio/sre_agent/conf/config.yaml) 的 `llm.api_key` fallback
- `NEXUS_USERNAME`、`NEXUS_PASSWORD`、`FEISHU_WEBHOOK_URL` 等 CI/CD 凭证必须始终显式配置在 GitLab Variables 中
- 如果已配置 `PREVIEW_PUBLIC_HOST`，即使 preview 因 `PREVIEW_DOCKER_HOST` 不可达而回退到 runner 本地 Docker，流水线日志也会优先输出 `PREVIEW_PUBLIC_HOST:随机端口` 作为浏览器访问地址

### 5.2 Runner 要求

这条流水线默认要求 Runner 至少具备：

- Docker-in-Docker 或等价的 Docker 构建能力
- 能访问 `10.11.4.5:5000`
- 如果启用了 `PREVIEW_DOCKER_HOST`，还需要能访问对应的 preview Docker 主机

如果 preview 希望在 job 结束后继续给团队使用，建议不要只依赖默认 dind，而是配置独立的持久 Docker 主机。

## 6. 定时任务清单

请在 GitLab 的 `Build -> Pipeline schedules` 中创建这些 schedule，并全部指向：

`feature/sre-c-core-infra`

| 定时任务名称 | 建议时间 | 变量 |
| --- | --- | --- |
| `weekly_build` | 每周五 17:00 | `PIPELINE_KIND=weekly_build` |
| `base_refresh` | 每月一次或每两周一次 | `PIPELINE_KIND=base_refresh` |
| `cleanup` | 每周低峰期执行 | `PIPELINE_KIND=cleanup` |

建议实践：

- `weekly_build` 放在固定、业务可感知的时间点
- `cleanup` 放在 preview 使用较少的时间段
- `base_refresh` 放在低峰时段，因为它会触发更多镜像层重建
- 在 GitLab 项目中开启 `Auto-cancel redundant pipelines`，配合当前 job 的 `interruptible: true` 使用，可减少短时间连续提交造成的资源浪费

## 7. 失败通知

这条流水线支持通过飞书群机器人发送失败通知。

推荐的第一版策略：

- `weekly_build` 失败时通知
- `base_refresh` 失败时通知
- `cleanup` 失败时通知
- release 阶段里的验证失败、快照发布失败、手动发版失败都通知
- 普通 commit 流水线默认不通知，除非设置 `FEISHU_NOTIFY_ON_COMMIT_FAILURE=1`

通知内容包括：

- 项目名
- 分支
- pipeline 类型
- stage
- job
- 状态
- commit short SHA
- 触发人
- pipeline 链接
- job 链接

通知形式：

- 飞书交互卡片
- 根据失败类型区分标题、颜色和按钮文案
- 包含建议动作，方便收到通知后直接排障

配置方式：

1. 在飞书群里创建机器人并获取 webhook
2. 在 GitLab 项目变量中配置 `FEISHU_WEBHOOK_URL`
3. 把该变量设成 masked + protected
4. 如需连普通提交失败也通知，增加 `FEISHU_NOTIFY_ON_COMMIT_FAILURE=1`

### 7.1 飞书机器人配置清单

推荐维护步骤：

1. 选择或创建一个接收 CI 通知的飞书群
2. 在飞书群中添加自定义机器人并复制 webhook 地址
3. 打开 GitLab 项目 `Settings -> CI/CD -> Variables`
4. 创建变量 `FEISHU_WEBHOOK_URL`
5. 粘贴 webhook 地址
6. 勾选 `Masked`
7. 如果你们的保护分支策略允许，再勾选 `Protected`
8. 保存变量并跑一次测试流水线

相关变量建议：

| 变量 | 建议值 | 用途 |
| --- | --- | --- |
| `FEISHU_WEBHOOK_URL` | 真实 webhook 地址 | 用于发送流水线失败通知 |
| `FEISHU_NOTIFY_ON_COMMIT_FAILURE` | `0` 或 `1` | 控制普通提交失败是否也通知飞书 |

维护说明：

- 不要把 webhook 地址提交进仓库
- 如果机器人被重建或轮换，只需要更新 GitLab 变量，不需要改代码
- 如果群里噪音太大，保持 `FEISHU_NOTIFY_ON_COMMIT_FAILURE=0`
- 如果团队后续希望扩大可见性，再改成 `1`

## 8. 如何使用这条流水线

### 8.1 普通提交流程

适用于日常在 `feature/sre-c-core-infra` 上开发时的自动构建与发布。

预期行为：

1. 向该分支提交代码
2. 如果改动命中业务相关路径，流水线自动启动
3. 构建业务快照镜像
4. 创建 preview 容器
5. 执行环境与业务验收
6. 把验证通过的快照镜像推送到 Nexus

如果只是文档改动或轻量无关改动，这条重流水线默认不会创建。

如需强制执行完整流程，可以在手动触发 pipeline 时设置：

```text
FORCE_FULL_PIPELINE=1
```

快照镜像拉取形式：

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:<shortsha-yyyymmddhhmm>
```

### 8.2 周构建流程

适用于沉淀每周稳定版本。

预期行为：

1. GitLab schedule 触发 `PIPELINE_KIND=weekly_build`
2. 生成 `weekly-YYYYMMDDHHMM` 业务镜像
3. 执行验收
4. 推送到 Nexus

周构建镜像拉取形式：

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:weekly-<yyyymmddhhmm>
```

### 8.3 基础镜像刷新流程

适用于即使仓库文件未变化，也希望周期性刷新系统包和依赖层的场景。

预期行为：

1. GitLab schedule 触发 `PIPELINE_KIND=base_refresh`
2. 重建基础镜像
3. 在新基础镜像上重建业务镜像
4. 执行验收
5. 推送到 Nexus

### 8.4 手动正式发布流程

适用于把某次已验证通过的快照版本提升为正式 release。

操作方式：

1. 打开 `feature/sre-c-core-infra` 上一条成功的 pipeline
2. 手动执行 `promote_sre_agent_release`
3. 可选地传入 `RELEASE_VERSION`
4. 如果不传，CI 会读取 [sre_agent/VERSION](/home/kevin/project/cube-studio/sre_agent/VERSION)

正式发布镜像拉取形式：

```bash
docker pull 10.11.4.5:5000/cube-studio/sre-agent-web:<version-yyyymmddhhmm>
```

## 9. 成功运行后应看到什么

正常成功的流水线日志里，应该至少能看到：

- 本次生成的镜像 tag
- preview 的后端和前端访问地址
- 验证通过日志
- 发布后的 `docker pull` 地址

如果手动 release promotion 成功，还应该看到正式 release 镜像的拉取命令。

如果启用了飞书通知，且被监控的 job 失败，对应飞书卡片里还应该带有 pipeline 和 job 的跳转链接。

## 10. 运行与维护建议

- 如果 preview 不要求在 job 结束后继续访问，可以不配置 `PREVIEW_DOCKER_HOST`
- 如果 preview 要供团队共享，建议同时配置 `PREVIEW_DOCKER_HOST` 和 `PREVIEW_PUBLIC_HOST`
- 如果基础依赖变化频繁，优先继续使用“依赖变化触发重建”，`base_refresh` 作为周期兜底
- 如果后续发布策略继续演进，建议在 `publish_sre_agent_snapshot` 之后追加环境部署 job，而不是把部署逻辑混进 build job

## 11. 相关文件索引

| 文件 | 用途 |
| --- | --- |
| [`.gitlab-ci.yml`](/home/kevin/project/cube-studio/.gitlab-ci.yml) | 分支专用 GitLab 流水线定义 |
| [`README.md`](/home/kevin/project/cube-studio/sre_agent/docs/gitlab/README.md) | 这条流水线的简明说明页 |
| [`common.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/common.sh) | 通用 CI 辅助函数 |
| [`run_preview_container.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/run_preview_container.sh) | Preview 容器启动逻辑 |
| [`validate_runtime_smoke.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_runtime_smoke.sh) | 环境验收脚本 |
| [`validate_business_suite.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/validate_business_suite.sh) | 业务验收脚本 |
| [`promote_release.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/promote_release.sh) | 手动正式发版脚本 |
| [`notify_feishu.sh`](/home/kevin/project/cube-studio/sre_agent/deploy/gitlab/ci/notify_feishu.sh) | 飞书失败通知脚本 |
