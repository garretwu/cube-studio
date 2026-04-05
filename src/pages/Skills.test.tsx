import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { SkillDescriptor } from "../api/types";
import { server } from "../test/server";
import SkillsPage from "./Skills";

const testSkills: SkillDescriptor[] = [
  {
    id: "builtin-topology-navigator",
    name: "Topology Navigator",
    scope: "builtin",
    summary: "Aggregate topology relations and surface the most relevant dependency path.",
    source: "builtin://topology",
    permissions: ["read:ontology"],
    match_score: 0.96,
    status: "available",
    updated_at: "2026-03-26T09:32:18Z",
    lifecycle_status: "published",
  },
  {
    id: "custom-release-window",
    name: "Release Window Review",
    scope: "custom",
    summary: "Evaluate whether a remediation action should run in the current release window.",
    source: "skills://release-window-review",
    permissions: ["read:schedule"],
    match_score: 0.83,
    status: "unavailable",
    updated_at: "2026-03-20T08:15:31Z",
    lifecycle_status: "draft",
  },
];

describe("SkillsPage", () => {
  it("renders skill rows with management metadata and filters by search query", async () => {
    const user = userEvent.setup();

    server.use(http.get("/api/skills", async () => HttpResponse.json(testSkills)));

    render(
      <MemoryRouter>
        <SkillsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "技能管理" })).toBeInTheDocument();
    expect(screen.getByText("技能总数")).toBeInTheDocument();
    expect(screen.getByText("筛选条件")).toBeInTheDocument();
    expect(screen.queryByText("统一管理技能元数据与文件内容")).not.toBeInTheDocument();
    expect(screen.queryByText("请直接粘贴完整的 SKILL.md 原文")).not.toBeInTheDocument();
    expect(await screen.findByText("Topology Navigator")).toBeInTheDocument();
    expect(screen.getByText("builtin-topology-navigator")).toBeInTheDocument();
    expect(screen.getAllByText("已发布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("草稿").length).toBeGreaterThan(0);
    expect(screen.getAllByText("自定义").length).toBeGreaterThan(0);

    await user.type(screen.getByPlaceholderText("搜索技能名称、ID 或描述"), "release");

    await waitFor(() => {
      expect(screen.queryByText("Topology Navigator")).not.toBeInTheDocument();
    });

    expect(screen.getByText("Release Window Review")).toBeInTheDocument();
    expect(screen.getByText("custom-release-window")).toBeInTheDocument();
  });

  it("navigates to the detail route when the detail action is clicked", async () => {
    const user = userEvent.setup();

    server.use(http.get("/api/skills", async () => HttpResponse.json(testSkills)));

    render(
      <MemoryRouter initialEntries={["/skills"]}>
        <Routes>
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/skills/:skillId" element={<div>detail route reached</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Topology Navigator");
    await user.click(screen.getByRole("button", { name: "查看技能详情 Topology Navigator" }));

    expect(await screen.findByText("detail route reached")).toBeInTheDocument();
  });
});
