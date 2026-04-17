import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { SkillDescriptor } from "../api/types";
import { server } from "../test/server";
import SkillDetailPage from "./SkillDetail";

const testSkill: SkillDescriptor = {
  id: "builtin-topology-navigator",
  name: "Topology Navigator",
  scope: "builtin",
  summary: "Aggregate topology relations and surface the most relevant dependency path.",
  source: "builtin://topology",
  permissions: ["read:ontology", "read:events"],
  match_score: 0.96,
  status: "available",
  updated_at: "2026-03-26T09:32:18Z",
  lifecycle_status: "published",
  file_name: "SKILL.md",
  markdown_content: [
    "---",
    "name: Topology Navigator",
    "description: Aggregate topology relations and surface the most relevant dependency path.",
    "---",
    "",
    "## Runtime Metadata",
    "```yaml",
    "id: builtin-topology-navigator",
    "scope: builtin",
    "```",
    "",
    "## 环境配置",
    "- should hide this section",
    "",
    "## 前置约束",
    "- should hide this section too",
    "",
    "## Usage",
    "- keep this section visible",
  ].join("\n"),
};

describe("SkillDetailPage", () => {
  it("renders a unified read-only skill detail canvas", async () => {
    server.use(http.get("/api/skills/:skillId", async () => HttpResponse.json(testSkill)));

    render(
      <MemoryRouter initialEntries={["/skills/builtin-topology-navigator"]}>
        <Routes>
          <Route path="/skills/:skillId" element={<SkillDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "技能详情" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回技能列表" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "文档预览" })).toBeInTheDocument();
    expect(screen.getByText("Topology Navigator")).toBeInTheDocument();
    expect(screen.getByText("Skill ID")).toBeInTheDocument();
    expect(screen.getByText("builtin-topology-navigator")).toBeInTheDocument();
    expect(screen.getByText("只读")).toBeInTheDocument();
    expect(screen.getByText("name: Topology Navigator")).toBeInTheDocument();
    expect(screen.getByText("id: builtin-topology-navigator")).toBeInTheDocument();
    expect(screen.getByText("## Usage")).toBeInTheDocument();
    expect(screen.queryByText("## 环境配置")).not.toBeInTheDocument();
    expect(screen.queryByText("## 前置约束")).not.toBeInTheDocument();
    expect(screen.queryByText("should hide this section")).not.toBeInTheDocument();
    expect(screen.getByText("read:ontology")).toBeInTheDocument();
    expect(screen.queryByText("刷新详情")).not.toBeInTheDocument();
    expect(screen.queryByText(/保存或发布/)).not.toBeInTheDocument();
  });
});
