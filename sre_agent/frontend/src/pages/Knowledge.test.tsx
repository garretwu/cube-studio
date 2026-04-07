import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { KnowledgeBaseSummary } from "../api/types";
import { server } from "../test/server";
import KnowledgePage from "./Knowledge";

const testKnowledgeBases: KnowledgeBaseSummary[] = [
  {
    id: "shared-industry-kb",
    name: "工业通用知识库",
    code: "shared_industry_kb",
    scope: "shared",
    document_count: 3,
    storage_bytes: 654336,
    index_status: "ready",
    status: "enabled",
    updated_at: "2026-03-24T10:00:00Z",
    description: "覆盖值班 SOP 和通用故障定位资料。",
  },
  {
    id: "shared-rnd-kb",
    name: "研发设计规范库",
    code: "shared_rnd_spec_kb",
    scope: "shared",
    document_count: 3,
    storage_bytes: 1753088,
    index_status: "indexing",
    status: "enabled",
    updated_at: "2026-03-24T09:12:00Z",
    description: "覆盖设计规范和发布检查项。",
  },
  {
    id: "private-ops-kb",
    name: "团队值班手册库",
    code: "private_ops_playbook_kb",
    scope: "private",
    document_count: 3,
    storage_bytes: 283648,
    index_status: "ready",
    status: "enabled",
    updated_at: "2026-03-24T07:45:00Z",
    description: "团队内部值班与交接资料。",
  },
];

describe("KnowledgePage", () => {
  it("renders tabs, metrics, and knowledge rows for the selected scope", async () => {
    server.use(http.get("/api/knowledge/bases", async () => HttpResponse.json({ items: testKnowledgeBases })));

    render(
      <MemoryRouter>
        <KnowledgePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "知识库" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "公共知识库" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("文档总数")).toBeInTheDocument();
    expect(await screen.findByText("工业通用知识库")).toBeInTheDocument();
    expect(screen.getByText("研发设计规范库")).toBeInTheDocument();
    expect(screen.queryByText("团队值班手册库")).not.toBeInTheDocument();
  });

  it("filters by query and resets to the default result set", async () => {
    const user = userEvent.setup();

    server.use(http.get("/api/knowledge/bases", async () => HttpResponse.json({ items: testKnowledgeBases })));

    render(
      <MemoryRouter>
        <KnowledgePage />
      </MemoryRouter>,
    );

    await screen.findByText("工业通用知识库");
    await user.type(screen.getByPlaceholderText("搜索知识库名称、编码或描述"), "研发");
    await user.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => {
      expect(screen.queryByText("工业通用知识库")).not.toBeInTheDocument();
    });

    expect(screen.getByText("研发设计规范库")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重置" }));

    expect(await screen.findByText("工业通用知识库")).toBeInTheDocument();
    expect(screen.getByText("研发设计规范库")).toBeInTheDocument();
  });

  it("navigates to the detail route when the detail button is clicked", async () => {
    const user = userEvent.setup();

    server.use(http.get("/api/knowledge/bases", async () => HttpResponse.json({ items: testKnowledgeBases })));

    render(
      <MemoryRouter initialEntries={["/knowledge"]}>
        <Routes>
          <Route path="/knowledge" element={<KnowledgePage />} />
          <Route path="/knowledge/:knowledgeBaseId" element={<div>detail route reached</div>} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("工业通用知识库");
    await user.click(screen.getByRole("button", { name: "查看详情 工业通用知识库" }));

    expect(await screen.findByText("detail route reached")).toBeInTheDocument();
  });
});
