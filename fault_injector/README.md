# fault_injector

实现了 `RoCE 网络 MTU 不一致` 故障注入示例，支持：

- 统一 conf 文件维护远端服务器登录信息（`fault_injector/conf/fault_injector.conf.json`）
- 注入前先写 WAL（`fault_injector/rollback.wal`）
- 支持回滚命令自动执行
- 支持 `simulate`（本地模拟）与 `ssh`（远端真实执行）模式

## 命令

```bash
python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id demo inject-roce-mtu-mismatch
python -m fault_injector --config fault_injector/conf/fault_injector.conf.json --session-id demo rollback
```

把配置中的 `mode` 改为 `ssh` 后即可远端执行。建议先在 `simulate` 模式验证。
