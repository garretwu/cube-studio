## 多根因诊断改造方案（`root_cause` 直接升级为主结构）

### 摘要
- 本次不做旧 `root_cause: string` 的兼容，直接把 `root_cause` 升级为多根因主结构。
- 采用的最终合同是：`root_cause` 从字符串改为 `RootCause[]`，每个元素都是一个完整根因项，首项代表当前主根因，其余项代表并存根因或共同致因。
- `ranked_candidates` 不再作为诊断主结构，计划从后端合同、前端类型、页面消费和测试中一起退场，避免两套语义并存。
- 修复链路本次仍维持“首个根因优先”的单计划策略，但会明确写在实现和注释里，说明这是阶段性约束而不是最终目标。
- 本次改造必须同步补注释，且注释合规是验收项。

### 统一开发规范
- 每个新增函数必须写函数级注释。
- 每个被修改的已有函数，也必须补函数级注释，不能只给新函数补。
- 函数级注释必须写清楚 4 类信息：目的、输入输出、兼容逻辑、为什么这样做。
- 复杂分支前补 1 到 2 行行内注释，重点覆盖：多结构转换、老逻辑拆除、单根因兜底为单元素数组、修复计划暂时只取首根因。
- Python 函数优先使用 docstring。
- TypeScript/TSX 函数使用块注释或 JSDoc 风格注释。
- 本次需要统一术语：`root_cause` 指正式多根因数组；“首根因”指 `root_cause[0]`；“主根因优先修复”指当前阶段只消费首根因计划。

### 公共接口与数据结构
- 后端 `DiagnosisResult.root_cause` 直接改为 `DiagnosedRootCause[]`，不再是字符串。
- 新的 `DiagnosedRootCause` 固定字段为：`id`、`title`、`layer`、`entities`、`confidence`、`certainty`、`status`、`evidence_summary`、`impact_summary`、`distinguishing_verification`、`recommended_fix`。
- `DiagnosisResult.root_cause_layer`、`root_cause_entities`、`ranked_candidates` 从正式合同中移除，不再保留为兼容字段。
- `DiagnosisResult` 保留 `confidence`、`next_action`、`hypotheses`、`propagation_chain`、`impact_summary`、`affected_services`、`triage_priority`、`diagnosis_certainty`、`recommended_fix`；其中顶层 `recommended_fix` 明确定义为“首根因对应的主修复建议”。
- 新的约束固定为：`root_cause` 至少 1 项；`root_cause[0]` 是主根因；`DiagnosisResult.diagnosis_certainty` 代表整体诊断结论，而不是某个单根因项；单个根因项自己的确信程度用 `certainty + confidence` 表达。
- Prompt、API、前端类型、页面转换全部以 `root_cause[]` 为唯一根因来源，不再从其他字段回填主根因信息。

### 后端实现计划
- 在 `sre_agent/models/diagnosis.py` 新增 `DiagnosedRootCause` 模型，并把 `DiagnosisResult.root_cause` 改成 `list[DiagnosedRootCause]`。同步清理 `RankedRootCause` 和 `ranked_candidates` 的核心依赖，避免后续继续被误用。
- 调整 `DiagnosisResult` 的校验逻辑：要求 `root_cause` 非空；首项必须有完整标题、层级、证据摘要和置信度；如果顶层 `recommended_fix` 存在，则必须与 `root_cause[0].recommended_fix` 一致或由后处理统一回填。
- 在 `sre_agent/agent/prompts.py` 重写最终 JSON shape。`diagnosis.root_cause` 明确要求输出数组对象，每个对象都包含根因标题、层级、实体、证据摘要、区分性验证和可选修复建议；prompt 中不再出现“单个 `root_cause` 字符串”的示例。
- 在 `sre_agent/agent/nodes.py` 增加统一归一化函数，建议固定拆为：`_normalize_root_cause_array_payload()`、`_normalize_root_cause_item_payload()`、`_sync_primary_recommended_fix_from_root_cause()`。这些函数负责把模型输出强制整理成 `root_cause[]` 主结构。
- 归一化规则固定为：若模型已返回 `root_cause[]`，逐项清洗并校验；若模型错误地仍返回单字符串 `root_cause`，立即包成单元素数组并记录这是兜底映射；若模型给出旧 `ranked_candidates` 但没有 `root_cause[]`，不做长期兼容设计，但允许在本次实现里作为临时兜底输入转成 `root_cause[]`，同时在注释里标明这是为了避免模型过渡期输出导致会话失败，不属于正式合同。
- `reason_node()` 的最终收敛逻辑改为：所有最终诊断、fallback diagnosis、超时降级 diagnosis 都必须生成 `root_cause[]`；任何地方都不再直接构造顶层字符串根因。
- `plan completion` 和 `tc consistency retry` 等二次修正逻辑全部改为围绕 `root_cause[0]` 运作，不再从 `ranked_candidates[0]` 或旧 `root_cause` 字符串取值。
- `sre_agent/server.py` 中 `_diagnosis_session_from_state()` 改为只认新的 `DiagnosisResult` 结构；fallback 结果也要构造单元素 `root_cause[]`。
- `sre_agent/server.py`、`sre_agent/diagnosis_start.py`、`sre_agent/api/routes.py` 中所有 `_extract_recommended_fix()` 统一调整为：先取顶层 `recommended_fix`，否则取 `root_cause[0].recommended_fix`，不再读 `ranked_candidates`。
- `re_diagnose` 的 query 文案和结果解析一起切换到 `root_cause[]`。文案明确要求模型“return updated root causes array”。
- SSE / WebSocket 继续透传完整 `diagnosis_result`，但事件里不再依赖 `ranked_candidates` 的存在。

### 前端实现计划
- 在 `sre_agent/frontend/src/api/types.ts` 和生成契约中，把 `DiagnosisResult.root_cause` 改为 `DiagnosedRootCause[]`，删除 `root_cause_layer`、`root_cause_entities`、`ranked_candidates` 类型依赖。
- 在 `diagnosisModel.ts` 和 `diagnosisModifiedModel.ts` 新增统一函数 `getNormalizedRootCauses()`，但这次它不做旧字段兼容，只做数组标准化和空值兜底。页面所有根因相关摘要、卡片、主计划入口全部从这个函数读取。
- `buildSummary()` 改成只从 `root_cause[0]` 生成主摘要，主摘要中的标题、层级、实体、证据来源都以首个根因项为准。
- `buildCandidates()` 改成直接映射 `root_cause[]` 生成 `DiagnosisCandidateView[]`。`DiagnosisCandidateView` 的 `title/summary/layer/entities/distinguishingVerification/isPrimary` 都从单个根因项直接得出，不再依赖 `ranked_candidates` 和 hypothesis 的字符串匹配。
- hypothesis 视图继续保留，但语义改成“推理过程中的假设与排除路径”，不再承担多根因主展示。
- `buildPlan()`、`extractRecommendedPlan()`、`diagnosisResultHasRecommendedPlan()` 全部改成只看顶层 `recommended_fix` 和 `root_cause[0].recommended_fix`。
- `Diagnosis.tsx` 和 `DiagnosisModified.tsx` 页面将“候选根因”区域改名为“多根因分析”，并把 `root_cause[]` 作为正式卡片来源。每张卡片展示标题、状态、层级、置信度、关联实体、证据摘要、区分性验证。首项卡片高亮为主根因。
- 页面头部摘要仍只展示首根因，但旁边增加“共 N 个根因项”的摘要信息，明确这是多根因诊断结果。
- demo/mock 数据统一切到新结构：把原来样例中的字符串 `root_cause` 改成数组对象；移除样例里的 `ranked_candidates`，避免开发时继续沿用旧结构。

### 测试计划
- 后端模型测试：验证 `root_cause[]` 非空约束；验证单个根因项字段完整性；验证单字符串兜底映射成单元素数组；验证顶层 `recommended_fix` 与首根因计划的一致性。
- 后端解析测试：验证模型直接输出 `root_cause[]` 时能通过；验证模型错误输出旧单字符串时能被兜底为单元素数组；验证所有 fallback diagnosis 和 timeout diagnosis 都能生成合法 `root_cause[]`。
- 后端服务测试：验证 `_diagnosis_session_from_state()` 能持久化新的多根因结构；验证 `_extract_recommended_fix()` 只走顶层和首根因计划；验证 stream 结束后落盘的 session 不再依赖 `ranked_candidates`。
- 前端类型与视图测试：验证 `buildSummary()` 从首根因取值；验证 `buildCandidates()` 正确渲染多个根因项；验证没有 `recommended_fix` 的次级根因不会破坏页面；验证只存在单元素 `root_cause[]` 时页面仍然正常。
- 前端页面测试：验证页面能渲染多张根因卡片；验证首项高亮正确；验证主计划入口只由首根因驱动；验证 propagation chain 和 hypothesis 仍能共存显示。
- 回归测试：覆盖单根因新结构输入，确保“单元素数组”路径可完全替代旧字符串路径。
- 注释验收：检查所有新增函数和修改函数是否有函数级注释；检查关键复杂分支是否补了行内注释。

### 实施顺序
- 第一步先改后端模型和 `DiagnosisResult` 合同，把 `root_cause` 升级成数组对象。
- 第二步改 prompt 和 `reason_node()` 归一化逻辑，保证所有成功和失败路径都产出 `root_cause[]`。
- 第三步改服务层、计划提取、session 落盘和 re-diagnose。
- 第四步改前端类型、store、视图模型，把所有消费逻辑切到 `root_cause[]`。
- 第五步改页面和 demo 数据，完成“多根因分析”展示。
- 第六步补测试和注释收口，逐个检查实现是否符合统一开发规范。
- 最后做一轮单元素 `root_cause[]` 回归，确认旧单根因场景在新结构下工作正常。

### 假设与默认选择
- 默认这是一次允许 breaking change 的改造，前后端合同同步修改，不保留旧 `root_cause: string` 兼容层。
- 默认 `ranked_candidates` 退出正式实现，不再继续维护。
- 默认 `root_cause[0]` 就是主根因，且主修复建议只由它驱动。
- 默认不新增新的修复计划数组字段，本次诊断先解决“多根因表达”和“多根因展示”。
- 默认为了避免模型过渡期输出导致服务失败，可以在后端解析里保留对旧单字符串形态的临时兜底输入处理，但这只是解析容错，不属于正式 API 兼容承诺，代码注释里必须写明。

### Multi-Factor Split Principle (2026-04-23)
- When multiple abnormal factors are observed, prefer separating them into distinct ranked candidates if they can be independently validated, independently remediated, or independently observed after remediation.
- Only merge multiple abnormal factors into one candidate when they form one inseparable causal chain or share the same remediation action.
- 中文约束：当存在多个异常因素时，若这些因素可以独立验证、独立修复或独立观察修复效果，应优先拆分为独立 candidate；只有在它们构成不可分割的同一因果链，或共享同一个修复动作时，才合并为一个候选根因。

### Update 2026-04-23: Multi-Plan Candidates
- For TTFT multi-root-cause sessions, each independently remediable `root_cause[i]` may carry its own `recommended_fix` proposal as a candidate plan.
- Top-level `diagnosis.recommended_fix` remains the primary approval path and stays aligned with `root_cause[0].recommended_fix`.
- This keeps approval-gated execution compatible with the existing flow while exposing independent remediation options for factors such as `fi_gpu_burn` and `load_simulator`.
