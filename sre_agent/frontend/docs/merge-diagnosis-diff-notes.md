# Diagnosis Merge Diff Notes

## 1) 冲突文件与冲突块位置

- `src/pages/Diagnosis.tsx`
  - 冲突块：`buildThinkingSummary` vs `system/run detail helpers`，以及底部若干小冲突块（字符串/条件分支）
- `src/pages/diagnosisModel.ts`
  - 冲突块：`extractRecommendedPlan` 旧逻辑 vs `report/run timeline` 新逻辑
- `src/pages/diagnosisModel.test.ts`
  - 冲突块：`live thinking merge` 旧测试组 vs `report timeline` 新测试组
- `src/store/diagnosisStore.ts`
  - 冲突块1：常量区（`SESSION_BACKFILL_*` vs `APPROVAL_EVENT_POLL_MS`）
  - 冲突块2：大段 streaming/round 兼容函数 vs 简化版 `wait()`
- `src/components/ApprovalDialog.tsx`
  - 冲突块1：Modal 属性（`destroyOnHidden` vs `className + destroyOnClose`）
  - 冲突块2：摘要区样式（`mini-card` vs `approval-dialog__summary`）

## 2) HEAD vs dev 差异摘要

- `Diagnosis.tsx`
  - `dev`：新增 run/progress/approval 细粒度展示与系统事件压缩逻辑。
  - `HEAD`：保留部分旧的 thinking-summary 逻辑。
- `diagnosisModel.ts`
  - `dev`：新增 `report/run` 结构化时间线能力，强化执行过程建模。
  - `HEAD`：包含更多旧版 trace/next-action 兼容拼装路径。
- `diagnosisModel.test.ts`
  - `dev`：覆盖 report/run 新行为。
  - `HEAD`：覆盖 live thinking round_id/thought_key 稳定性。
- `diagnosisStore.ts`
  - `dev`：结构更简化。
  - `HEAD`：保留 pending session、backfill 节流、live thinking round 去重/排序等兼容能力。
- `ApprovalDialog.tsx`
  - `dev`：视觉样式更新。
  - `HEAD`：保留 `busy/disable/error` 交互防护。

## 3) 最终保留策略与原因

- `Diagnosis.tsx`：采用 `dev` 主体。
  - 原因：确保新设计交互完整落地。
- `diagnosisModel.ts`：采用 `dev` 主体。
  - 原因：report/run 时间线是新诊断设计核心。
- `diagnosisModel.test.ts`：采用 `dev` 主体。
  - 原因：测试基线跟随新 UI/模型。
- `diagnosisStore.ts`：保留 `HEAD` 兼容主干，并补入 `APPROVAL_EVENT_POLL_MS + wait()`。
  - 原因：保障旧后端/混合事件输入下稳定渲染（会话回填、tool_call 兜底、round 去重）。
- `ApprovalDialog.tsx`：合并两侧。
  - 保留 `dev` 样式（`approval-dialog-modal`, `approval-dialog__summary`）
  - 保留 `HEAD` 交互安全（busy 禁止关闭、按钮 loading/disabled、errorMessage 显示）

## 4) 可能引入的联调风险

- `Diagnosis.tsx` 与 `diagnosisStore.ts` 组合后，若 store 字段名未来继续变更，可能出现“页面依赖存在但语义变化”的隐性风险。
- `diagnosisModel.test.ts` 目前偏向新模型覆盖，旧的 live-round 稳定性回归需要后续补测。
- 审批流程依赖 `events/session` 轮询桥接，若后端事件延迟较高，UI 状态可能短暂滞后。

## 5) 验证用例与命令

在 `sre_agent/frontend` 目录执行：

- `npm run typecheck`
- `npm run build`
- `npm run test -- src/pages/diagnosisModel.test.ts src/pages/Diagnosis.test.tsx`
- `npm run test -- src/features/topologyExplorer/components/TopologyCanvas.labelVisibility.test.ts src/pages/TopologyObject.test.tsx`

建议联调检查：

- live session 下 `thinking_step/tool_call/tool_result` 混合流渲染
- `approval_required -> remediating -> session_closed` 状态链路展示
- topology 图例包含 `port/bmc` 且画布可渲染

## 6) 回退最小变更点

若诊断联调出现阻塞，建议最小回退顺序：

1. 先回退 `Diagnosis.tsx`（页面层）到合并前稳定版本，保留 `diagnosisStore.ts` 兼容逻辑。  
2. 再回退 `diagnosisModel.ts` 的 `report/run` 组装段，保留事件解析与基础时间线。  
3. 最后才考虑回退 `diagnosisStore.ts`（风险最高，影响面最大）。
