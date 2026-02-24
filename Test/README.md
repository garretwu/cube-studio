# Fault Injector 测试方案总览

本目录用于集中管理 `fault_injector` 的测试方案、步骤和验收标准，覆盖：

1. 本地单元测试（不依赖真实服务器）
2. 本地模拟注入/回滚测试（simulate 模式）
3. 远端真实注入/回滚测试（ssh 模式）
4. 失败场景与恢复演练
5. 上线前灰度与回归

## 目录说明

- `Test/test_plan.md`：完整测试计划（范围、环境、测试矩阵、通过标准）
- `Test/test_cases.md`：逐条测试用例（前置条件、步骤、预期结果、失败处理）
- `Test/test_execution_runbook.md`：执行手册（按顺序执行命令、记录结果、问题回滚）
- `Test/remote_config_template.conf.json`：远端联调配置模板（便于复制填写）

## 建议执行顺序

1. 先执行单元测试（快速发现回归）
2. 再执行 simulate 模式端到端流程
3. 最后在测试服务器执行 ssh 模式流程
4. 对失败路径进行一次强制演练（注入成功后中断，再做 rollback）

## 结果归档建议

- 每次执行在测试报告中记录：
  - Git commit
  - 测试人、测试时间
  - 配置文件版本
  - 命令执行结果（stdout/stderr）
  - 是否触发回滚以及回滚耗时

