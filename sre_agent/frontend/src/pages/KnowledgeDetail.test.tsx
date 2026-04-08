import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { KnowledgeBaseDetail } from "../api/types";
import { server } from "../test/server";
import KnowledgeDetailPage from "./KnowledgeDetail";

const knowledgeBaseDetail: KnowledgeBaseDetail = {
  id: "shared-industry-kb",
  knowledge_base_id: "kb_shared_industry_001",
  name: "工业通用知识库",
  code: "shared_industry_kb",
  scope: "shared",
  document_count: 3,
  storage_bytes: 654336,
  index_status: "ready",
  status: "enabled",
  updated_at: "2026-03-24T10:00:00Z",
  created_at: "2026-03-12T08:30:00Z",
  indexed_at: "2026-03-24T10:00:00Z",
  description: "面向值班与基础设施排障的通用知识沉淀。",
  documents: [
    {
      id: "doc-shared-001",
      title: "告警处理 SOP",
      source_type: "file",
      source_label: "平台上传文件",
      file_name: "alarm_sop.pdf",
      size_bytes: 524288,
      index_status: "ready",
      status: "enabled",
      updated_at: "2026-03-24T10:00:00Z",
      preview_summary: "聚合值班接警和升级链路。",
      tags: ["值班", "SOP"],
      preview: {
        title: "告警处理 SOP",
        description: "统一说明告警接收后的判断顺序。",
        source_label: "platform://shared/alarm_sop.pdf",
        updated_at: "2026-03-24T10:00:00Z",
        tags: ["值班", "SOP"],
        sections: [
          {
            id: "sec-1",
            heading: "适用范围",
            body: "适用于自动化告警与人工巡检发现异常场景。",
          },
        ],
      },
    },
    {
      id: "doc-shared-002",
      title: "值班升级流程",
      source_type: "manual",
      source_label: "控制台手录",
      file_name: "incident_escalation_playbook",
      size_bytes: 16384,
      index_status: "ready",
      status: "enabled",
      updated_at: "2026-03-23T17:16:00Z",
      preview_summary: "梳理 P1/P0 事件升级顺序。",
      tags: ["升级", "流程"],
      preview: {
        title: "值班升级流程",
        description: "团队值班升级指引。",
        source_label: "manual://shared/incident-escalation-playbook",
        updated_at: "2026-03-23T17:16:00Z",
        tags: ["升级", "流程"],
        sections: [
          {
            id: "sec-1",
            heading: "升级触发条件",
            body: "当核心链路持续退化超过 10 分钟时立即升级。",
          },
        ],
      },
    },
    {
      id: "doc-shared-003",
      title: "BMC 故障排查指南",
      source_type: "link",
      source_label: "设备厂商门户",
      file_name: "bmc-troubleshooting",
      size_bytes: 113664,
      index_status: "pending",
      status: "enabled",
      updated_at: "2026-03-23T18:30:00Z",
      preview_summary: "用于定位节点带外管理异常。",
      tags: ["BMC", "硬件"],
      preview: {
        title: "BMC 故障排查指南",
        description: "厂商文档摘要。",
        source_label: "https://vendor.example.com/bmc-troubleshooting",
        source_uri: "https://vendor.example.com/bmc-troubleshooting",
        updated_at: "2026-03-23T18:30:00Z",
        tags: ["BMC", "硬件"],
        sections: [
          {
            id: "sec-1",
            heading: "常见异常",
            body: "包括 BMC 页面不可达和固件升级中断。",
          },
        ],
        warning: "该文档仍在等待索引完成。",
      },
    },
  ],
};

describe("KnowledgeDetailPage", () => {
  it("renders the detail page and updates preview when a row is selected", async () => {
    const user = userEvent.setup();

    server.use(
      http.get("/api/knowledge/bases/:knowledgeBaseId", async ({ params }) => {
        if (params.knowledgeBaseId === knowledgeBaseDetail.id) {
          return HttpResponse.json(knowledgeBaseDetail);
        }
        return HttpResponse.json({ message: "not found" }, { status: 404 });
      }),
    );

    render(
      <MemoryRouter initialEntries={[`/knowledge/${knowledgeBaseDetail.id}`]}>
        <Routes>
          <Route path="/knowledge/:knowledgeBaseId" element={<KnowledgeDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("工业通用知识库")).toBeInTheDocument();
    expect(screen.getByText("选择一条文档记录查看详情")).toBeInTheDocument();

    await user.click(screen.getByText("值班升级流程"));

    expect(await screen.findByText("团队值班升级指引。")).toBeInTheDocument();
    expect(screen.getByText("manual://shared/incident-escalation-playbook")).toBeInTheDocument();
    expect(screen.getByText("升级触发条件")).toBeInTheDocument();
  });

  it("filters documents and keeps the preview empty until a document is selected", async () => {
    const user = userEvent.setup();

    server.use(http.get("/api/knowledge/bases/:knowledgeBaseId", async () => HttpResponse.json(knowledgeBaseDetail)));

    render(
      <MemoryRouter initialEntries={[`/knowledge/${knowledgeBaseDetail.id}`]}>
        <Routes>
          <Route path="/knowledge/:knowledgeBaseId" element={<KnowledgeDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("告警处理 SOP");
    await user.type(screen.getByPlaceholderText("搜索文档标题、文件名或来源地址"), "BMC");
    await user.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => {
      expect(screen.queryByText("告警处理 SOP")).not.toBeInTheDocument();
    });

    expect(screen.getByText("BMC 故障排查指南")).toBeInTheDocument();
    expect(screen.getByText("选择一条文档记录查看详情")).toBeInTheDocument();
  });

  it("shows an error card when the knowledge base cannot be found", async () => {
    server.use(http.get("/api/knowledge/bases/:knowledgeBaseId", async () => HttpResponse.json({ message: "not found" }, { status: 404 })));

    render(
      <MemoryRouter initialEntries={["/knowledge/missing-kb"]}>
        <Routes>
          <Route path="/knowledge/:knowledgeBaseId" element={<KnowledgeDetailPage />} />
          <Route path="/knowledge" element={<div>knowledge list route</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("知识库详情暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回列表" })).toBeInTheDocument();
  });
});
