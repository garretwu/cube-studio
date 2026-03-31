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
  },
];

describe("SkillsPage", () => {
  it("renders skill cards with core metadata and filters by search query", async () => {
    const user = userEvent.setup();

    server.use(
      http.get("/api/skills", async () => {
        return HttpResponse.json(testSkills);
      }),
    );

    render(
      <MemoryRouter>
        <SkillsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "\u6280\u80fd\u4e2d\u5fc3" })).toBeInTheDocument();
    expect(await screen.findByText("\u62d3\u6251\u5bfc\u822a\u5668")).toBeInTheDocument();
    expect(screen.getByText("Skill ID: builtin-topology-navigator")).toBeInTheDocument();
    expect(screen.getByText("\u53ef\u7528")).toBeInTheDocument();
    expect(screen.getByText("\u4e0d\u53ef\u7528")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("\u641c\u7d22 Skill \u540d\u79f0\u6216 ID"), "release");

    await waitFor(() => {
      expect(screen.queryByText("\u62d3\u6251\u5bfc\u822a\u5668")).not.toBeInTheDocument();
    });

    expect(screen.getByText("Release Window Review")).toBeInTheDocument();
    expect(screen.getByText("Skill ID: custom-release-window")).toBeInTheDocument();
  });

  it("navigates to the detail route when a skill card is clicked", async () => {
    const user = userEvent.setup();

    server.use(
      http.get("/api/skills", async () => {
        return HttpResponse.json(testSkills);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/skills"]}>
        <Routes>
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/skills/:skillId" element={<div>detail route reached</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("拓扑导航器");
    await user.click(screen.getByRole("button", { name: "查看技能详情 拓扑导航器" }));

    expect(await screen.findByText("detail route reached")).toBeInTheDocument();
  });
});
