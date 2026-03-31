import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { SkillDescriptor } from "../api/types";
import { server } from "../test/server";
import SkillDetailPage from "./SkillDetail";

const testSkills: SkillDescriptor[] = [
  {
    id: "builtin-topology-navigator",
    name: "Topology Navigator",
    scope: "builtin",
    summary: "Aggregate topology relations and surface the most relevant dependency path.",
    source: "builtin://topology",
    permissions: ["read:ontology", "read:events"],
    match_score: 0.96,
    status: "available",
    updated_at: "2026-03-26T09:32:18Z",
  },
];

describe("SkillDetailPage", () => {
  it("renders core metadata and preview capability cards", async () => {
    server.use(
      http.get("/api/skills", async () => {
        return HttpResponse.json(testSkills);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/skills/builtin-topology-navigator"]}>
        <Routes>
          <Route path="/skills/:skillId" element={<SkillDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findAllByRole("heading", { name: "拓扑导航器" })).toHaveLength(2);
    expect(screen.getByText("Skill ID")).toBeInTheDocument();
    expect(screen.getByText("builtin-topology-navigator")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "能力预览" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "依赖链路聚焦" })).toBeInTheDocument();
    expect(screen.getAllByText("触发时机").length).toBeGreaterThan(0);
    expect(screen.getAllByText("预览产出").length).toBeGreaterThan(0);
  });
});
