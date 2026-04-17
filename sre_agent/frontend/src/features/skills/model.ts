import type { SkillDescriptor } from "../../api/types";

type SkillStatusTone = "success" | "warning";
type SkillChipTone = "accent" | "info" | "success";

export type SkillViewModel = SkillDescriptor & {
  lifecycle_status: "draft" | "published";
  file_name: string;
  markdown_content: string;
};

const HIDDEN_SKILL_SECTION_HEADERS = new Set(["环境配置", "前置约束"]);

function buildYamlList(items: string[]) {
  if (!items.length) {
    return ["  - none"];
  }

  return items.map((item) => `  - ${item}`);
}

export function buildSkillMarkdown(skill: SkillDescriptor) {
  return [
    "---",
    `name: ${skill.name}`,
    `description: ${skill.summary}`,
    "---",
    "",
    "## Runtime Metadata",
    "```yaml",
    `id: ${skill.id}`,
    `scope: ${skill.scope}`,
    `source: ${skill.source}`,
    "permissions:",
    ...buildYamlList(skill.permissions),
    "```",
    "",
    "## Usage",
    `- Match score: ${Math.round(skill.match_score * 100)}%`,
    `- Availability: ${skill.status === "unavailable" ? "unavailable" : "available"}`,
    "",
    "## Notes",
    "- Paste the full SKILL.md content here when the real skill file is available.",
  ].join("\n");
}

export function formatSkillScopeLabel(scope: SkillDescriptor["scope"]) {
  return scope === "builtin" ? "内置" : "自定义";
}

export function getSkillScopeTone(scope: SkillDescriptor["scope"]): SkillChipTone {
  return scope === "builtin" ? "info" : "accent";
}

export function formatSkillLifecycleLabel(status: SkillViewModel["lifecycle_status"]) {
  return status === "published" ? "已发布" : "草稿";
}

export function getSkillLifecycleTone(status: SkillViewModel["lifecycle_status"]): SkillChipTone {
  return status === "published" ? "success" : "accent";
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
      description: "当前仅提供技能说明和文件内容，尚未接入执行链路。",
    };
  }

  return {
    label: "可用",
    tone: "success",
    description: "已接入当前工作台，可在诊断与自动化流程中调用。",
  };
}

export function normalizeSkill(skill: SkillDescriptor): SkillViewModel {
  return {
    ...skill,
    status: skill.status ?? "available",
    lifecycle_status: skill.lifecycle_status ?? (skill.scope === "builtin" ? "published" : "draft"),
    file_name: skill.file_name?.trim() || "SKILL.md",
    markdown_content: skill.markdown_content?.trim() || buildSkillMarkdown(skill),
  };
}

export function normalizeSkills(skills: SkillDescriptor[]) {
  return skills.map(normalizeSkill);
}

export function filterSkillMarkdownForDisplay(markdown: string): string {
  if (!markdown.trim()) {
    return markdown;
  }
  const lines = markdown.split(/\r?\n/);
  const visible: string[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    const headingMatch = line.match(/^##\s+(.+?)\s*$/);
    if (headingMatch) {
      const headingText = (headingMatch[1] ?? "").trim();
      if (HIDDEN_SKILL_SECTION_HEADERS.has(headingText)) {
        index += 1;
        while (index < lines.length && !/^##\s+/.test(lines[index] ?? "")) {
          index += 1;
        }
        continue;
      }
    }
    visible.push(line);
    index += 1;
  }
  return visible.join("\n");
}
