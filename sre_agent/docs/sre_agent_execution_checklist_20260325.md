# SRE Agent 实施执行清单（仅计划，不实施代码）

日期：2026-03-25  
策略基线：Alertmanager 拉取、以后端契约为准、人工审批

## P0 Checklist（闭环打通）

| 任务 | 负责人 | 依赖 | 验收命令 | 状态 |
|---|---|---|---|---|
| 装配默认 diagnosis_runner（create_app 无注入可运行） | Backend | `agent.run_diagnosis`、ToolRegistry | `pytest sre_agent/tests/test_api.py -q` | TODO |
| 装配 re_diagnose_runner（Loop 再诊断可执行） | Backend | LoopOrchestrator | `pytest sre_agent/tests/test_loop_orchestrator.py -q` | TODO |
| 新增 `sre-agent serve` 启动命令 | Backend | config、server.create_app、uvicorn | `python -m sre_agent serve --config config.yaml` | TODO |
| 新增 Alert 拉取服务（后台轮询） | Backend | `lib.channels.alert.AlertChannel` | `pytest sre_agent/tests/test_api.py -q -k alerts` | TODO |
| 新增 `GET /api/alerts` 快照接口 | Backend | Alert 拉取服务、AlertCorrelator | `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/alerts` | TODO |
| 统一发布诊断与修复关键事件 | Backend | trace_publisher、WSEvent schema | `pytest sre_agent/tests/test_api.py -q -k websocket` | TODO |
| 前端迁移到后端主契约（sessions/remediate） | Frontend | API client、stores、pages | `npm --prefix sre_agent/frontend run test` | TODO |
| 前端 WS 拼接 token | Frontend | useWebSocket、env token | `npm --prefix sre_agent/frontend run test` | TODO |
| 关闭默认 MSW（改显式开启） | Frontend | `src/main.tsx` | `VITE_USE_MSW=false npm --prefix sre_agent/frontend run dev` | TODO |

## P1 Checklist（稳定性与可运维）

| 任务 | 负责人 | 依赖 | 验收命令 | 状态 |
|---|---|---|---|---|
| 启动依赖装配摘要日志（runner/channels/knowledge） | Backend | serve 启动命令 | `python -m sre_agent serve --config config.yaml` | TODO |
| 前端缺失接口页面降级策略（skills/baseline/documents） | Frontend | API client | `npm --prefix sre_agent/frontend run test` | TODO |
| WS 重连续传策略（last_event_id）验证 | Frontend + Backend | ws router + client | 人工断网重连演练 | TODO |
| 审批会话一致性校验增强 | Backend | approval gate + session store | `pytest sre_agent/tests/test_api.py -q -k approve` | TODO |

## 联调与演示 Checklist

| 任务 | 负责人 | 依赖 | 验收命令 | 状态 |
|---|---|---|---|---|
| 启动后端（真实模式） | Backend | serve 命令、JWT、channels | `python -m sre_agent serve --config config.lab.yaml` | TODO |
| 启动前端（关闭 MSW） | Frontend | Vite proxy、API token | `VITE_USE_MSW=false npm --prefix sre_agent/frontend run dev` | TODO |
| 验证 `/api/alerts` 实时告警可见 | QA | Alertmanager 可达 | 浏览器 + curl | TODO |
| 验证 `handle -> sessions -> loop` 全链路 | QA | P0 完成 | API 调用 + 页面联动 | TODO |
| 验证人工审批与修复结果回显 | QA | approval + remediation | 页面审批操作 | TODO |
| 产出 E2E 记录（日志/截图） | QA | 上述全部 | 演示记录归档 | TODO |

## 统一验收标准（摘要）

1. API 合约通过：
   - `/api/alerts`
   - `/api/handle`
   - `/api/sessions/{id}`
   - `/api/sessions/{id}/loop`
   - `/api/remediate/{id}/approve`
2. WebSocket 合约通过：
   - `/ws/alerts`
   - `/ws/thinking-trace/{id}`
3. 前端闭环通过：
   - 告警进入、触发处理、诊断可视化、人工审批、修复结果回显。
4. E2E 通过：
   - 关闭 MSW，真实后端联调完整闭环。

---

## Wave 1 实施更新（2026-03-26）

| 任务 | 状态 | 实施结果 | 验证命令 | 证据 |
|---|---|---|---|---|
| 装配默认 diagnosis_runner（create_app 无注入可运行） | DONE | `create_app` 默认装配 `DefaultDiagnosisRunner`，显式注入优先；默认路径 fail-fast。 | `pytest sre_agent/tests/test_server_default_runner.py -q` | `sre_agent/docs/evidence/wave1_20260326_01/logs/pytest_wave1_additional.log` |
| 装配 re_diagnose_runner（Loop 再诊断可执行） | DONE | `create_app` 默认装配 `DefaultReDiagnoseRunner`，接入 LoopOrchestrator。 | `pytest sre_agent/tests/test_loop_orchestrator.py -q` | `sre_agent/docs/evidence/wave1_20260326_01/logs/pytest_test_loop_orchestrator.log` |
| 新增 `sre-agent serve` 启动命令 | DONE | CLI 新增 `serve` 子命令，统一加载 config + create_app + uvicorn。 | `pytest sre_agent/tests/test_cli_serve.py -q` | `sre_agent/docs/evidence/wave1_20260326_01/logs/pytest_wave1_additional.log` |
| 启动依赖装配摘要日志（runner/channels/knowledge/memory/auth） | DONE | `server.create_app` 启动时输出依赖摘要日志。 | `python -m sre_agent serve --config config.yaml --host 127.0.0.1 --port 18091` | `sre_agent/docs/evidence/wave1_20260326_01/logs/serve_netstat_18091.log` |

### Wave 1 验收结论
- 结论：通过（按本波次目标）
- 说明：仅完成 Wave 1（P0 装配）；Wave 2~5 仍按原计划推进。

### Agent 回归（真实链路）
- 命令：`python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
- 结果：通过，基线文件已更新。
- 基线：`sre_agent/scripts/agent_demo_test_result.json`
- 归档：`sre_agent/docs/evidence/wave1_20260326_01/agent_demo/agent_demo_test_result.json`

## Wave 2 实施更新（2026-03-26）
| 任务 | 状态 | 实施结果 | 验证命令 | 证据 |
|---|---|---|---|---|
| 前端迁移到后端主契约（sessions/remediate） | DONE | `apiClient` 切换到 `/api/handle`、`/api/sessions/{id}`、`/api/sessions/{id}/loop`、`/api/remediate/{id}/approve`；`alerts` 选中后先触发 `handle` 再携带 `session_id` 跳转诊断页 | `npm --prefix sre_agent/frontend run test` | `sre_agent/frontend/src/api/client.ts` |
| 统一 `SREResponse` envelope 解析 | DONE | 前端新增统一 `unwrapPayload`，主接口按 envelope 解析；保留对旧 mock 结构的兼容读取 | `npm --prefix sre_agent/frontend run test` | `sre_agent/frontend/src/api/client.ts` |
| 审批路径统一 | DONE | 审批路径统一为 `/api/remediate/{id}/approve`，并补充 `user` 字段 | `npm --prefix sre_agent/frontend run test` | `sre_agent/frontend/src/api/client.test.ts` |
| 默认关闭 MSW（显式开启） | DONE | 仅当 `VITE_USE_MSW=true` 且 `DEV` 时启用 mock worker | `npm --prefix sre_agent/frontend run build` | `sre_agent/frontend/src/main.tsx` |

### Wave 2 测试结论
- 前端：`npm --prefix sre_agent/frontend run test` 通过（4 passed）。
- 前端构建：`npm --prefix sre_agent/frontend run build` 通过。
- 后端回归：`pytest sre_agent/tests/test_api.py -q`（13 passed）；`pytest sre_agent/tests/test_loop_orchestrator.py -q`（6 passed）。

### Wave 2 Agent 回归
- 命令：`python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json`
- 结果：通过（`status=diagnosed`，`evidence_checks.pod_log_collected=true`）。
- 基线：`sre_agent/scripts/agent_demo_test_result.json` 已刷新。
- 备注：为降低真实环境空窗误报风险，`agent_demo_test.json` 的 `log_since` 调整为 `6h`。

## Wave 3 实施更新（2026-03-26）
| 任务 | 状态 | 实施结果 | 验证命令 | 证据 |
|---|---|---|---|---|
| 新增 `GET /api/alerts` 快照接口 | DONE | 增加 `alert_store` 快照并返回 `alerts/clusters` | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/api/routes.py` |
| `/ws/alerts` 广播与 `last_event_id` | DONE | `/ws/alerts` 支持 `last_event_id` 续传，新增 e2e 测试覆盖 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/api/websocket.py` |
| thinking-trace 事件序列完整性增强 | DONE | `IncidentHandler` 新增 `alert/tool_call/tool_result/diagnosis_result/approval_required/done/error` 事件发布 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/remediation/incident_handler.py` |
| WS 实时续传基础能力 | DONE | `trace_publisher` 升级为增量事件流（自动 event_id + 持续订阅） | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/server.py` |
| 真实 Alertmanager 后台拉取服务 | DONE* | `AlertPollingService` 已接入 `create_app` 生命周期并支持真实 `AlertChannel`；剩余问题为默认配置未绑定真实源 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/server.py` |

### Wave 3 测试结论
- 后端：`pytest sre_agent/tests/test_api.py -q` 通过（25 passed）。
- 前端：`npm --prefix sre_agent/frontend run test -- --run` 通过（5 files, 11 passed）。
- Agent 回归：`python sre_agent/scripts/Agent_demo.py --config-json sre_agent/scripts/agent_demo_test.json` 通过，基线已更新。

### Wave 3 运行时约束（新增）
- 真实告警联调必须使用包含 `global.alertmanager_url` 的配置启动后端：
  - `config.lab.yaml` 或 `config.lab.test.yaml`
  - 示例：`python -m sre_agent serve --config config.lab.yaml --host 127.0.0.1 --port 8000`

## Wave 4 实施更新（2026-03-27）
| 任务 | 状态 | 实施结果 | 验证命令 | 证据 |
|---|---|---|---|---|
| 审批会话状态机严格校验 | DONE | `/api/remediate/{id}/approve` 仅允许 `approval_required` 会话；重复审批/终态审批返回 `VALIDATION_ERROR` | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/api/routes.py` |
| 审批异常映射收敛 | DONE | plan 缺失返回 `REM_PLAN_INVALID`，执行异常返回 `REM_EXEC_FAILED`，避免 500 裸异常 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/api/routes.py` |
| 修复阶段事件补齐 | DONE | 增加 `remediation_progress` 阶段事件：执行开始/成功/失败、回滚开始/成功/失败 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/api/routes.py` |
| rollback API E2E 覆盖 | DONE | 新增 `/api/remediate/{id}/rollback` 成功与未知会话错误路径测试 | `pytest sre_agent/tests/test_api.py -q` | `sre_agent/tests/test_api.py` |

### Wave 4 测试结论
- `pytest sre_agent/tests/test_server_default_runner.py -q`：2 passed
- `pytest sre_agent/tests/test_cli_serve.py -q`：1 passed
- `pytest sre_agent/tests/test_loop_orchestrator.py -q`：6 passed
- `pytest sre_agent/tests/test_api.py -q`：25 passed
- `pytest sre_agent/tests/test_remediation.py -q`：7 passed
- `npm --prefix sre_agent/frontend run test -- --run`：5 files, 11 passed
- `npm --prefix sre_agent/frontend run build`：通过
