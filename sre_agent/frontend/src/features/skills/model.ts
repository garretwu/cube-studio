import type { SkillDescriptor } from "../../api/types";

type SkillStatusTone = "success" | "warning";

export type SkillPreviewCapability = {
  title: string;
  description: string;
  trigger: string;
  output: string;
};

export type SkillViewModel = SkillDescriptor & {
  audience: string;
  scenario: string;
  guardrail: string;
  previewCapabilities: SkillPreviewCapability[];
};

const curatedSkillMeta: Record<string, Partial<SkillViewModel>> = {
  "builtin-topology-navigator": {
    name: "拓扑导航器",
    summary: "聚合实体关系、影响范围和上下游依赖，帮助诊断链路上的拓扑异常。",
    status: "available",
    updated_at: "2026-03-26T09:32:18Z",
    audience: "值班 SRE、拓扑诊断和值守分析台。",
    scenario: "适合在告警刚触发时快速定位受影响对象、依赖链路和关键路径。",
    guardrail: "当前预览只展示拓扑分析能力，不会直接执行修复动作。",
    previewCapabilities: [
      {
        title: "依赖链路聚焦",
        description: "从当前告警对象向上下游展开，快速圈出最关键的实体关系。",
        trigger: "选中服务、节点或告警对象时触发。",
        output: "返回一条重点影响路径和核心对象摘要。",
      },
      {
        title: "影响半径预览",
        description: "沿着拓扑关系向外扩散，展示可能被波及的集群、节点和服务。",
        trigger: "需要判断影响范围时使用。",
        output: "生成受影响对象清单和优先关注目标。",
      },
      {
        title: "对象上下文采样",
        description: "汇总对象标签、状态、位置和基础指标，减少人工切换页面。",
        trigger: "查看拓扑对象详情时触发。",
        output: "生成适合放入诊断面板的对象快照。",
      },
    ],
  },
  "builtin-alert-correlator": {
    name: "告警关联器",
    summary: "将同一时间窗内的基础设施与服务告警聚类，生成可直接进入诊断流程的事件上下文。",
    status: "available",
    updated_at: "2026-03-25T16:48:55Z",
    audience: "值班 SRE、告警分诊和值守班长。",
    scenario: "适合在高噪声场景下快速判断哪些告警属于同一条故障链路。",
    guardrail: "当前预览只展示关联结果和证据，不会自动静默或关闭告警。",
    previewCapabilities: [
      {
        title: "时间窗聚类",
        description: "按时间、实体和症状对同时段告警做聚合，降低噪声。",
        trigger: "同一时间窗出现多条相关告警时使用。",
        output: "返回聚类结果、主告警候选和关联证据。",
      },
      {
        title: "根因候选排序",
        description: "基于实体关系和症状相似度，为根因对象给出优先级。",
        trigger: "进入诊断前需要先确定最值得展开的告警时触发。",
        output: "生成根因候选列表和排序理由。",
      },
    ],
  },
  "builtin-remediation-guard": {
    name: "修复执行护栏",
    summary: "在自动修复前检查审批、回滚策略和灰度条件，降低高风险操作的误执行概率。",
    status: "available",
    updated_at: "2026-03-24T21:06:12Z",
    audience: "自动修复编排链路和高风险变更审批环节。",
    scenario: "适合在准备执行脚本、回滚或灰度操作前先做安全校验。",
    guardrail: "详情页只展示校验能力，不会在这里直接发起执行。",
    previewCapabilities: [
      {
        title: "审批条件检查",
        description: "核对是否满足审批人、值班角色和变更窗口等前置条件。",
        trigger: "修复计划进入待执行阶段时触发。",
        output: "给出通过、阻塞或需补充审批的结果。",
      },
      {
        title: "回滚路径验证",
        description: "确认每个修复步骤是否有对应回滚动作和验证条件。",
        trigger: "计划包含变更动作时使用。",
        output: "返回可回滚性结论和缺失项提示。",
      },
    ],
  },
  "custom-runbook-rdma": {
    name: "RDMA 手册匹配器",
    summary: "将网络类告警映射到 RDMA、RoCE 与 ECN 处置手册，生成更聚焦的排障建议。",
    status: "available",
    updated_at: "2026-03-22T11:40:08Z",
    audience: "网络值班、RDMA 专项排障和值班升级场景。",
    scenario: "适合在网络抖动、丢包、拥塞或 RDMA 链路异常时快速匹配手册。",
    guardrail: "当前预览只展示手册匹配和建议摘要，不会直接修改网络配置。",
    previewCapabilities: [
      {
        title: "告警到手册映射",
        description: "将网络类告警和指标症状映射到最相关的 RDMA 运行手册。",
        trigger: "检测到 RoCE、ECN 或拥塞控制异常时使用。",
        output: "返回手册名称、匹配原因和优先阅读章节。",
      },
      {
        title: "参数检查清单",
        description: "根据命中手册列出需要核查的交换机、网卡和系统参数。",
        trigger: "准备进入人工排障或远程协同时触发。",
        output: "生成一份可直接执行的检查项列表。",
      },
    ],
  },
  "custom-k8s-release-window": {
    name: "发布窗口审查",
    summary: "结合业务高峰、值班排班和变更策略，对修复动作是否应立即执行给出窗口建议。",
    status: "unavailable",
    updated_at: "2026-03-20T08:15:31Z",
    audience: "变更经理、值班 SRE 和修复审批链路。",
    scenario: "适合在修复计划涉及变更发布、回滚或大范围重启时做时间窗口判断。",
    guardrail: "该 Skill 当前未接入执行链路，详情页仅提供能力说明和预览样例。",
    previewCapabilities: [
      {
        title: "高峰期识别",
        description: "结合业务日历与历史流量判断当前是否处在敏感时段。",
        trigger: "准备执行会影响线上容量的动作时使用。",
        output: "返回高峰标记和风险提示。",
      },
      {
        title: "窗口建议摘要",
        description: "把是否立即执行、延后执行或转人工审批压缩成清晰结论。",
        trigger: "审批前需要一份简洁建议时使用。",
        output: "生成推荐窗口、风险因子和补充条件。",
      },
    ],
  },
  "custom-service-impact-brief": {
    name: "服务影响摘要",
    summary: "把受影响服务、SLO 变化和用户面信息压缩成可直接同步给值班同学的简报卡片。",
    status: "available",
    updated_at: "2026-03-18T19:24:47Z",
    audience: "值班 SRE、业务接口人和事件同步场景。",
    scenario: "适合在诊断阶段快速生成对内对外同步的服务影响摘要。",
    guardrail: "当前预览用于查看摘要能力，不会自动发送通知或更新外部系统。",
    previewCapabilities: [
      {
        title: "受影响服务归并",
        description: "将关联实体映射到服务视角，快速看清哪些业务面正在波动。",
        trigger: "诊断结果已定位到受影响对象时使用。",
        output: "返回服务列表、影响等级和当前状态。",
      },
      {
        title: "同步话术草案",
        description: "用面向值班和业务方的语言组织一句话摘要与下一步计划。",
        trigger: "需要发送简报或同步状态更新时使用。",
        output: "产出一份可直接复用的简报文案。",
      },
    ],
  },
};

function buildDefaultPreviewCapabilities(skill: SkillDescriptor): SkillPreviewCapability[] {
  const permissionSummary = skill.permissions.length > 0 ? skill.permissions.join(" / ") : "基础上下文";

  return [
    {
      title: "上下文采集",
      description: `围绕 ${skill.name} 拉取当前链路需要的核心上下文。`,
      trigger: `调用后会优先读取 ${permissionSummary} 等相关信息。`,
      output: "生成一份适合继续分析的结构化上下文快照。",
    },
    {
      title: "策略判断",
      description: "根据当前 Skill 的摘要与权限边界，对输入对象做快速判断。",
      trigger: "在值班同学需要快速决策或筛选时使用。",
      output: "返回推荐动作、限制条件和关注点。",
    },
  ];
}

export function formatSkillScopeLabel(scope: SkillDescriptor["scope"]) {
  return scope === "builtin" ? "内置技能" : "自定义技能";
}

export function getSkillStatusMeta(skill: Pick<SkillDescriptor, "status">): {
  label: string;
  tone: SkillStatusTone;
  description: string;
} {
  if (skill.status === "unavailable") {
    return {
      label: "不可用",
      tone: "warning",
      description: "当前只展示能力说明，暂未接入实际执行链路。",
    };
  }

  return {
    label: "可用",
    tone: "success",
    description: "已接入当前工作台，可用于诊断与自动化流程。",
  };
}

export function normalizeSkill(skill: SkillDescriptor): SkillViewModel {
  const curated = curatedSkillMeta[skill.id];

  return {
    ...skill,
    ...curated,
    status: skill.status ?? curated?.status ?? "available",
    updated_at: skill.updated_at ?? curated?.updated_at,
    audience: curated?.audience ?? "面向值班 SRE 与自动化运维流程。",
    scenario: curated?.scenario ?? "适合在当前工作台中快速查看技能的输入、边界与可预览结果。",
    guardrail: curated?.guardrail ?? "当前页面仅提供技能说明与预览，不会直接触发执行动作。",
    previewCapabilities: curated?.previewCapabilities ?? buildDefaultPreviewCapabilities(skill),
  };
}

export function normalizeSkills(skills: SkillDescriptor[]) {
  return skills.map(normalizeSkill);
}
