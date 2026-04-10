# SRE Agent Release Notes

## 2026-04-10

这次迭代从容器化交付、云原生部署能力和分支级 GitLab CI 流水线三个方向，完成了 `sre_agent` 的一轮系统性增强。

### 1. 容器化交付

- 增加了基础镜像和业务镜像两层 `Dockerfile`
- 基础镜像统一安装 `apt`、Python 和 npm 依赖
- 业务镜像只保留运行时代码与启动脚本，减少无关内容进入镜像
- 增加了容器启动入口、健康检查脚本和本地运行脚本
- 本地运行容器时支持：
  `SRE_OPENAI_API_KEY` 优先读取环境变量，未显式提供时 fallback 到 `sre_agent/conf/config.yaml`
- 本地容器默认 Host 端口改为高位端口，并在启动前自动检查端口占用，尽量避免常见端口冲突

### 2. 云原生部署

- 补齐了原始 Kubernetes YAML：
  `Deployment`、`Service`、`ConfigMap`、`Secret`
- 增加了渲染与应用脚本，支持基于镜像 tag 的部署
- 增加了 Helm chart，便于多环境参数化安装与升级
- 补齐了 Host、Docker、Kubernetes、Helm 四类运行方式的操作说明

### 3. 分支级 GitLab CI 流水线

- 为 `feature/sre-c-core-infra` 分支单独设计了 GitLab CI 流水线
- 增加了 `workflow: rules`，避免影响其他分支
- 完整覆盖以下阶段：
  `build`、`preview`、`release`、`cleanup`
- 基础镜像只在依赖变化或 `base_refresh` 定时任务中重建
- 业务镜像支持：
  普通提交 tag、`weekly_build` tag、手动 release tag
- 增加了 preview 容器启动与 TTL 管理
- 增加了环境验收和业务验收
- 增加了快照发布与手动正式发布提升流程
- 增加了定时清理策略与保留策略

### 4. 飞书通知

- 集成了飞书群机器人失败通知
- 通知形式升级为交互卡片
- 按失败类型区分：
  构建失败、预览失败、验证失败、发布失败、清理失败
- 根据场景区分卡片标题、颜色、按钮文案
- 卡片中附带建议动作，方便收到通知后快速定位问题

### 5. 文档整理

- 新增了 `sre_agent` 总 README
- 补齐了部署说明、FAQ、发布检查清单
- 增加了 GitLab 流水线说明和使用手册
- 将文档统一整理到 `sre_agent/docs/` 下，部署资源和脚本仍保留在 `sre_agent/deploy/`
- 文档说明统一改为中文，代码路径、变量名、job 名、stage 名、命令和配置项等技术术语保留英文

### 6. 交付原则

- 正式交付优先使用 GitLab 流水线构建并发布到 Nexus 的镜像
- 本地手动执行 `build_images.sh` 主要用于功能验证、容器调试和页面效果确认
- 本地手动构建的镜像不应直接作为正式发布版本使用
