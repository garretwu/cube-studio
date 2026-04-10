# SRE Agent Web 发布检查清单

## 1. 构建前检查

- [ ] 确认本地代码改动已经完成并经过检查。
- [ ] 确认 Python 版本 `>= 3.11`。
- [ ] 确认 Node 版本 `>= 20`。
- [ ] 确认 frontend 依赖能够正常安装。
- [ ] 确认 [sre_agent/requirements.txt](/home/kevin/project/cube-studio/sre_agent/requirements.txt) 中的 Python 依赖能够正常安装。
- [ ] 确认 [start_frontend_backend.py](/home/kevin/project/cube-studio/sre_agent/scripts/start_frontend_backend.py) 在本地仍可正常启动。
- [ ] 确认 [SOP.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/SOP.md) 和 [FAQ.md](/home/kevin/project/cube-studio/sre_agent/docs/deploy/FAQ.md) 已与当前交付流程保持一致。

## 2. 本地验证

- [ ] 创建或激活本地 Python 虚拟环境。
- [ ] 执行 `uv pip install -r sre_agent/requirements.txt`。
- [ ] 执行 `cd sre_agent/frontend && npm install`。
- [ ] 执行 `python sre_agent/scripts/start_frontend_backend.py` 启动本地服务。
- [ ] 确认 backend 的 `/openapi.json` 可访问。
- [ ] 确认 frontend 页面可以正常打开。

## 3. 镜像构建

- [ ] 执行 `bash sre_agent/docker/build_images.sh`。
- [ ] 确认只生成了两个带时间戳的镜像：
- [ ] `sre-agent-base:$YYYYMMDDHHMM`
- [ ] `sre-agent-web:$YYYYMMDDHHMM`
- [ ] 确认 [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env) 已更新为最新时间戳 tag。
- [ ] 确认业务镜像确实以基础镜像为 parent。

## 4. 容器验证

- [ ] 如果目标环境不会自动注入 `SRE_OPENAI_API_KEY`，先手动导出该变量。
- [ ] 执行 `bash sre_agent/docker/run_container.sh`。
- [ ] 确认容器启动成功。
- [ ] 确认 `docker inspect --format '{{json .State.Health}}' sre-agent-web` 最终变成 `healthy`。
- [ ] 确认 frontend 在映射后的 Host 端口上可访问。
- [ ] 确认 backend 在映射后的 Host 端口上可访问。
- [ ] 检查完成后，停止这个验证容器。

## 5. Registry 推送

- [ ] `source` [image.env](/home/kevin/project/cube-studio/sre_agent/docker/image.env)。
- [ ] 将 `${BASE_TAGGED}` 推送到目标 registry。
- [ ] 将 `${APP_TAGGED}` 推送到目标 registry。
- [ ] 确认目标 registry 中已经出现新的时间戳 tag。

## 6. Kubernetes 原始 YAML 发布

- [ ] 确认 [secret.example.yaml](/home/kevin/project/cube-studio/sre_agent/deploy/k8s/secret.example.yaml) 已被替换成真实 `Secret`，或集群中已存在等价 `Secret`。
- [ ] 确认 deployment 中引用的镜像 tag 与已推送的时间戳 tag 一致。
- [ ] 执行 `bash sre_agent/deploy/k8s/apply.sh`。
- [ ] 确认 `Deployment` rollout 成功。
- [ ] 确认 Pods 进入 `Running` 和 `Ready`。
- [ ] 确认 `Service` 创建成功。
- [ ] 确认 frontend 可通过 `Service`、`Ingress` 或 `port-forward` 访问。

## 7. Helm 发布

- [ ] 确认 Helm values 中的镜像仓库地址和时间戳 tag 都正确。
- [ ] 确认 `Secret` 策略正确：
- [ ] `secret.create=true` 并直接传值，或
- [ ] `secret.create=false` 并引用已有 `Secret`
- [ ] 执行 `helm upgrade --install`。
- [ ] 确认 `helm status` 健康。
- [ ] 确认 Pods 进入 `Running` 和 `Ready`。
- [ ] 确认 `Service` 暴露方式符合预期。

## 8. 发布后验证

- [ ] 确认目标环境中的 backend `/openapi.json` 可访问。
- [ ] 确认目标环境中的 frontend 首页可访问。
- [ ] 确认没有立即出现重启循环。
- [ ] 确认健康检查在首次启动窗口结束后保持绿色。
- [ ] 确认日志中没有缺少依赖的报错。
- [ ] 确认日志中没有缺少配置或 `Secret` 的报错。

## 9. 回滚准备

- [ ] 保留上一版已知可用的时间戳 tag 记录。
- [ ] 确认回滚命令或 manifest 可以快速切回上一版镜像 tag。
- [ ] 在正式发布前确认上一版镜像仍然存在于 registry 中。
