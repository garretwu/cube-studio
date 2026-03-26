import { useEffect, useMemo, useRef } from "react";
import * as d3 from "d3";

import type { OntologyEdge, OntologyNode } from "../api/types";

type GraphNode = d3.SimulationNodeDatum & OntologyNode;
type GraphLink = d3.SimulationLinkDatum<GraphNode> & OntologyEdge;

const palette: Record<string, string> = {
  rack: "#6555ff",
  node: "#503dff",
  gpu: "#f04438",
  switch: "#0ea5e9",
  inference_service: "#8b5cf6",
};

type TopologyGraphProps = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  selectedId?: string;
  onSelect?: (id: string) => void;
};

function TopologyGraph({ nodes, edges, selectedId, onSelect }: TopologyGraphProps) {
  const svgRef = useRef<SVGSVGElement | null>(null);

  const preparedNodes = useMemo<GraphNode[]>(() => nodes.map((node) => ({ ...node })), [nodes]);
  const preparedEdges = useMemo<GraphLink[]>(
    () => {
      const nodeIds = new Set(nodes.map((node) => node.id));
      return edges
        .filter((edge) => nodeIds.has(edge.source_id) && nodeIds.has(edge.target_id))
        .map((edge) => ({ ...edge, source: edge.source_id, target: edge.target_id }));
    },
    [edges, nodes],
  );

  useEffect(() => {
    if (!svgRef.current || preparedNodes.length === 0) {
      return undefined;
    }

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const width = svgRef.current.clientWidth || 900;
    const height = 420;

    const simulation = d3
      .forceSimulation(preparedNodes)
      .force("link", d3.forceLink<GraphNode, GraphLink>(preparedEdges).id((d: GraphNode) => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2));

    const link = svg
      .append("g")
      .attr("stroke", "rgba(80, 61, 255, 0.22)")
      .attr("stroke-width", 1.5)
      .selectAll("line")
      .data(preparedEdges)
      .join("line");

    const node = svg
      .append("g")
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .data(preparedNodes)
      .join("circle")
      .attr("r", 13)
      .attr("fill", (d: GraphNode) => palette[d.entity_type] ?? "#64748b")
      .attr("stroke", (d: GraphNode) => (d.id === selectedId ? "#f59e0b" : "#ffffff"))
      .attr("stroke-width", (d: GraphNode) => (d.id === selectedId ? 4 : 2))
      .style("cursor", "pointer")
      .call(
        d3
          .drag<SVGCircleElement, GraphNode>()
          .on("start", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, datum: GraphNode) => {
            if (!event.active) {
              simulation.alphaTarget(0.3).restart();
            }
            datum.fx = datum.x;
            datum.fy = datum.y;
          })
          .on("drag", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, datum: GraphNode) => {
            datum.fx = event.x;
            datum.fy = event.y;
          })
          .on("end", (event: d3.D3DragEvent<SVGCircleElement, GraphNode, GraphNode>, datum: GraphNode) => {
            if (!event.active) {
              simulation.alphaTarget(0);
            }
            datum.fx = null;
            datum.fy = null;
          }),
      )
      .on("click", (_event: MouseEvent, datum: GraphNode) => onSelect?.(datum.id));

    const label = svg
      .append("g")
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(preparedNodes)
      .join("text")
      .attr("font-size", 12)
      .attr("fill", "#0d0d12")
      .attr("text-anchor", "middle")
      .text((d: GraphNode) => d.name ?? d.id);

    simulation.on("tick", () => {
      link
        .attr("x1", (d: GraphLink) => (d.source as GraphNode).x ?? 0)
        .attr("y1", (d: GraphLink) => (d.source as GraphNode).y ?? 0)
        .attr("x2", (d: GraphLink) => (d.target as GraphNode).x ?? 0)
        .attr("y2", (d: GraphLink) => (d.target as GraphNode).y ?? 0);

      node.attr("cx", (d: GraphNode) => d.x ?? 0).attr("cy", (d: GraphNode) => d.y ?? 0);

      label.attr("x", (d: GraphNode) => d.x ?? 0).attr("y", (d: GraphNode) => (d.y ?? 0) + 28);
    });

    return () => {
      simulation.stop();
    };
  }, [onSelect, preparedEdges, preparedNodes, selectedId]);

  return (
    <div className="topology-graph">
      <svg ref={svgRef} className="topology-svg" viewBox="0 0 900 420" preserveAspectRatio="xMidYMid meet" />
    </div>
  );
}

export default TopologyGraph;
