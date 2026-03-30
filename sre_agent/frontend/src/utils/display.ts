import type { AlertStatus, ChatMessage, IncidentRecord, Severity, VerificationConfig } from "../api/types";

function formatByMap(value: string | null | undefined, map: Record<string, string>, fallback = "未知") {
  if (!value) {
    return fallback;
  }

  return map[value] ?? value;
}

const severityLabels: Record<Severity | "all", string> = {
  all: "全部级别",
  critical: "严重",
  warning: "警告",
  info: "提示",
};

const alertStatusLabels: Record<AlertStatus, string> = {
  firing: "触发中",
  resolved: "已恢复",
  silenced: "已静默",
};

const workflowStatusLabels: Record<string, string> = {
  approved: "已批准",
  awaiting_approval: "待审批",
  builtin: "内置",
  closed: "已断开",
  confirmed: "已确认",
  connecting: "连接中",
  custom: "自定义",
  degraded: "降级",
  eliminated: "已排除",
  error: "异常",
  execution_failed: "执行失败",
  execution_started: "执行中",
  execution_succeeded: "执行完成",
  failed: "失败",
  firing: "触发中",
  healthy: "正常",
  high: "高",
  hot: "过热",
  impacted: "受影响",
  low: "低",
  medium: "中",
  open: "已连接",
  partially_resolved: "部分恢复",
  pending: "待执行",
  probable: "较大概率",
  proposed_fix_ready: "修复方案已就绪",
  re_diagnosed: "已复核",
  rejected: "已驳回",
  rollback_failed: "回滚失败",
  rollback_started: "回滚中",
  rollback_succeeded: "回滚完成",
  resolved: "已解决",
  testing: "验证中",
  validating: "验证中",
  approval_rejected: "审批驳回",
};

const entityTypeLabels: Record<string, string> = {
  gpu: "GPU",
  inference_service: "推理服务",
  node: "节点",
  rack: "机柜",
  switch: "交换机",
};

const actionTypeLabels: Record<string, string> = {
  conclude: "得出结论",
  remediate: "执行修复",
  tool_call: "工具调用",
};

const verificationMethodLabels: Record<VerificationConfig["method"], string> = {
  promql: "指标校验",
  tool_call: "工具校验",
  wait: "等待观察",
};

const roleLabels: Record<ChatMessage["role"], string> = {
  assistant: "助手",
  tool: "工具",
  user: "用户",
};

const categoryLabels: Record<string, string> = {
  hardware: "硬件",
  runbook: "运行手册",
};

const propertyLabels: Record<string, string> = {
  bandwidth: "带宽",
  model: "型号",
  namespace: "命名空间",
  rack: "所在机柜",
  role: "角色",
  utilization: "利用率",
  vendor: "厂商",
  zone: "区域",
};

const layerLabels: Record<string, string> = {
  application: "应用",
  hardware: "硬件",
  network: "网络",
  platform: "平台",
};

export function formatSeverity(value?: Severity | "all" | null) {
  if (!value) {
    return "未知";
  }

  return severityLabels[value] ?? value;
}

export function formatAlertStatus(value?: AlertStatus | null) {
  if (!value) {
    return "未知";
  }

  return alertStatusLabels[value] ?? value;
}

export function formatWorkflowStatus(value?: string | null, fallback = "未知") {
  return formatByMap(value, workflowStatusLabels, fallback);
}

export function formatEntityType(value?: string | null) {
  return formatByMap(value, entityTypeLabels);
}

export function formatActionType(value?: string | null) {
  return formatByMap(value, actionTypeLabels);
}

export function formatVerificationMethod(value?: VerificationConfig["method"] | null) {
  if (!value) {
    return "未知";
  }

  return verificationMethodLabels[value] ?? value;
}

export function formatRole(value?: ChatMessage["role"] | null) {
  if (!value) {
    return "未知";
  }

  return roleLabels[value] ?? value;
}

export function formatSkillScope(value?: "builtin" | "custom" | null) {
  return formatWorkflowStatus(value);
}

export function formatKnowledgeCategory(value?: string | null) {
  return formatByMap(value, categoryLabels);
}

export function formatPropertyLabel(value: string) {
  return propertyLabels[value] ?? value;
}

export function formatLayer(value?: string | null) {
  return formatByMap(value, layerLabels);
}

export function formatIncidentOutcome(value?: IncidentRecord["outcome"] | null) {
  return formatWorkflowStatus(value);
}
