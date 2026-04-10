import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";

import { useTopologyExplorerStore } from "../features/topologyExplorer/store";
import TopologyPage from "./Topology";

describe("TopologyPage", () => {
  let createObjectUrlSpy: ReturnType<typeof vi.spyOn>;
  let revokeObjectUrlSpy: ReturnType<typeof vi.spyOn>;
  let anchorClickSpy: ReturnType<typeof vi.spyOn>;
  let exportedBlob: { text: () => Promise<string> } | null;
  const OriginalBlob = globalThis.Blob;

  class BlobMock extends OriginalBlob {
    private readonly payloadText: string;

    constructor(parts: BlobPart[], options?: BlobPropertyBag) {
      super(parts, options);
      this.payloadText = parts.map((part) => (typeof part === "string" ? part : String(part))).join("");
    }

    async text() {
      return this.payloadText;
    }
  }

  beforeEach(() => {
    exportedBlob = null;
    Object.defineProperty(globalThis, "Blob", {
      writable: true,
      value: BlobMock,
    });
    if (!window.URL.createObjectURL) {
      Object.defineProperty(window.URL, "createObjectURL", {
        writable: true,
        value: () => "blob:placeholder",
      });
    }
    if (!window.URL.revokeObjectURL) {
      Object.defineProperty(window.URL, "revokeObjectURL", {
        writable: true,
        value: () => undefined,
      });
    }
    createObjectUrlSpy = vi.spyOn(window.URL, "createObjectURL").mockImplementation((blob) => {
      exportedBlob = blob;
      return "blob:topology-export";
    });
    revokeObjectUrlSpy = vi.spyOn(window.URL, "revokeObjectURL").mockImplementation(() => undefined);
    anchorClickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    useTopologyExplorerStore.setState({
      data: undefined,
      isLoading: false,
      error: undefined,
      viewMode: "graph",
      selectedNodeId: undefined,
      activeImpactPathId: undefined,
      inspectorOpen: true,
      searchQuery: "",
      matchedNodeIds: [],
      searchFeedback: "idle",
      statusFilter: "all",
      layerFilter: "all",
      summaryFilter: "all",
      legendOpen: false,
      drawerOpen: false,
      layoutPreset: "layered",
      hoveredNodeId: undefined,
      inspectorTab: "overview",
      drawerTab: "paths",
    });
  });

  afterEach(() => {
    Object.defineProperty(globalThis, "Blob", {
      writable: true,
      value: OriginalBlob,
    });
    createObjectUrlSpy.mockRestore();
    revokeObjectUrlSpy.mockRestore();
    anchorClickSpy.mockRestore();
  });

  it("renders the page title and right-side inspector guide by default", async () => {
    render(<TopologyPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "\u8fd0\u884c\u62d3\u6251" })).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByTestId("topology-side-inspector")).toBeInTheDocument();
    });

    expect(
      screen.getByText("\u8bf7\u9009\u62e9\u56fe\u4e2d\u7684\u5bf9\u8c61\uff0c\u67e5\u770b\u5176\u5173\u7cfb\u3001\u72b6\u6001\u4e0e\u5c5e\u6027\u3002"),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "\u5bfc\u51fa\u62d3\u6251" })).toBeInTheDocument();
    });
  });

  it("searches and selects a matching object, then renders details in the side inspector", async () => {
    const user = userEvent.setup();
    render(<TopologyPage />);

    const input = await screen.findByPlaceholderText(
      "\u641c\u7d22\u673a\u67dc / \u8282\u70b9 / GPU / \u670d\u52a1 / \u4ea4\u6362\u673a",
    );
    await user.type(input, "svc-vllm-online");
    await user.click(screen.getByRole("button", { name: "\u5b9a\u4f4d\u5bf9\u8c61" }));

    await waitFor(() => {
      expect(screen.getByTestId("topology-side-inspector")).toBeInTheDocument();
    });

    const inspector = screen.getByTestId("topology-side-inspector");
    expect(within(inspector).getByRole("heading", { name: "svc-vllm-online" })).toBeInTheDocument();
    expect(within(inspector).getByRole("tab", { name: "\u6982\u89c8" })).toBeInTheDocument();
  });

  it("can hide the side inspector and show the canvas toggle", async () => {
    const user = userEvent.setup();
    render(<TopologyPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "\u5bfc\u51fa\u62d3\u6251" })).toBeInTheDocument();
    });

    const inspector = await screen.findByTestId("topology-side-inspector");
    await user.click(within(inspector).getByRole("button", { name: "\u9690\u85cf" }));

    await waitFor(() => {
      expect(screen.queryByTestId("topology-side-inspector")).not.toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "\u663e\u793a\u68c0\u67e5\u5668" })).toBeInTheDocument();
    });
  });

  it("does not expose impact-analysis entry points in the topology toolbar", async () => {
    render(<TopologyPage />);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "\u5173\u7cfb\u56fe" })).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: "\u5f71\u54cd\u8def\u5f84" })).not.toBeInTheDocument();
    expect(screen.queryByText("\u95ee\u9898\u5206\u6790\u89c6\u56fe")).not.toBeInTheDocument();
    expect(screen.queryByText(/\u5f53\u524d\u8def\u5f84 1\/1/)).not.toBeInTheDocument();
  });

  it("downloads the current graph topology as JSON for design reuse", async () => {
    const user = userEvent.setup();
    render(<TopologyPage />);

    const exportButton = await screen.findByRole("button", { name: "\u5bfc\u51fa\u62d3\u6251" });
    await waitFor(() => {
      expect(exportButton).toBeEnabled();
    });

    const input = screen.getByPlaceholderText(
      "\u641c\u7d22\u673a\u67dc / \u8282\u70b9 / GPU / \u670d\u52a1 / \u4ea4\u6362\u673a",
    );
    await user.type(input, "svc-vllm-online");
    await user.click(screen.getByRole("button", { name: "\u4ec5\u5f02\u5e38" }));
    await user.click(exportButton);

    expect(createObjectUrlSpy).toHaveBeenCalledTimes(1);
    expect(anchorClickSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrlSpy).toHaveBeenCalledWith("blob:topology-export");
    expect(exportedBlob).not.toBeNull();

    const exportedText = await exportedBlob!.text();
    const payload = JSON.parse(exportedText) as {
      meta: { source: string; viewMode: string; layoutPreset: string };
      filters: { statusFilter: string; searchQuery: string };
      nodes: Array<{ id: string; position: { x: number; y: number } }>;
      edges: Array<{ id: string }>;
    };

    expect(payload.meta.source).toBe("topology-modified-canvas");
    expect(payload.meta.viewMode).toBe("graph");
    expect(payload.meta.layoutPreset).toBe("layered");
    expect(payload.filters.statusFilter).toBe("abnormal");
    expect(payload.filters.searchQuery).toBe("svc-vllm-online");
    expect(payload.nodes.length).toBeGreaterThan(0);
    expect(payload.nodes[0]?.position).toEqual(
      expect.objectContaining({
        x: expect.any(Number),
        y: expect.any(Number),
      }),
    );
    expect(payload.edges.length).toBeGreaterThan(0);
  });

  it("disables topology export outside graph view and explains why", async () => {
    const user = userEvent.setup();
    render(<TopologyPage />);

    await user.click(await screen.findByRole("button", { name: "\u5c5e\u6027\u56fe" }));

    const exportButton = screen.getByRole("button", { name: "\u5bfc\u51fa\u62d3\u6251" });
    expect(exportButton).toBeDisabled();
    expect(screen.getByText("\u4ec5\u5173\u7cfb\u56fe\u89c6\u56fe\u652f\u6301\u5bfc\u51fa\u5f53\u524d\u753b\u5e03")).toBeInTheDocument();
  });
});
