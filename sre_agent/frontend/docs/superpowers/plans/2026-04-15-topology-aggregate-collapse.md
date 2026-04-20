# Topology Aggregate Expand/Collapse + Group Box Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 聚合节点点击展开后，在画布内展示“分组框 + 收回 icon”，支持点击框或 icon 收回回聚合节点。

**Architecture:** 使用现有 `expandedAggregateIds` 控制聚合展开状态；新增 `expandedAggregateMeta`（`aggregateId -> memberIds + label`）用于展开态分组框渲染。分组框通过 ReactFlow 的自定义 node（不可拖拽/不可连接）实现，位置与尺寸由成员节点的包围盒计算得到。

**Tech Stack:** React 18, TypeScript, ReactFlow (`@xyflow/react`), Zustand, CSS

---

### Task 1: Add expanded aggregate meta state

**Files:**
- Modify: `src/pages/Topology.tsx`

- [ ] **Step 1: Extend local state to keep aggregate metadata**

Add:

```ts
type ExpandedAggregateMeta = {
  aggregateId: string;
  label: string;
  memberIds: string[];
};
```

And state:

```ts
const [expandedAggregateMeta, setExpandedAggregateMeta] = useState<Record<string, ExpandedAggregateMeta>>({});
```

- [ ] **Step 2: Capture meta on expand**

In `handleToggleAggregateNode(aggregateId)`:
- When expanding (was not expanded), read `aggregateNode.name` and `aggregateNode.attributes.aggregateMemberIds`.
- Persist into `expandedAggregateMeta[aggregateId]`.

- [ ] **Step 3: Cleanup meta on collapse / data refresh**

When collapsing (was expanded), remove `expandedAggregateMeta[aggregateId]`.
When `data?.lastUpdated` changes for modified variant, clear both `expandedAggregateIds` and `expandedAggregateMeta`.

- [ ] **Step 4: Wire meta into `TopologyExplorer` and further to `TopologyCanvas`**

Pass two props:
- `expandedAggregateIds`
- `expandedAggregateMeta`

---

### Task 2: Render group box node inside ReactFlow

**Files:**
- Modify: `src/features/topologyExplorer/components/TopologyCanvas.tsx`
- Modify: `src/features/topologyExplorer/topologyExplorer.css`

- [ ] **Step 1: Add props to `TopologyCanvas`**

Add:

```ts
expandedAggregateIds?: string[];
expandedAggregateMeta?: Record<string, { aggregateId: string; label: string; memberIds: string[] }>;
onCollapseAggregate?: (aggregateId: string) => void;
```

- [ ] **Step 2: Create a `groupBox` node type**

Implement a `GroupBoxNode` component that:
- Renders a rounded rectangle “frame” + header line
- Shows `label` and member count
- Renders a collapse button using `AppIcon name="frameCollapse"` (or `chevronUp` if desired)
- Clicking the frame or icon calls `onCollapseAggregate(aggregateId)`

- [ ] **Step 3: Compute bounding boxes from member node positions**

From `flowNodeLookup` + metrics:
- For each expanded aggregate, compute minX/minY/maxX/maxY of its members’ node rects
- Inflate with padding (e.g. 18px) and ensure a minimum size

- [ ] **Step 4: Inject group box nodes into `ReactFlow` nodes list**

Create overlay nodes with:
- `type: "groupBox"`
- `position: { x: bbox.x, y: bbox.y }`
- `draggable: false`, `selectable: false`, `connectable: false`, `focusable: false`
- `style: { width: bbox.width, height: bbox.height, zIndex: 0 }`

Ensure normal entity nodes have higher zIndex (default in ReactFlow) so box stays behind.

- [ ] **Step 5: Add CSS styles**

Add styles for:
- Box frame: subtle border, light background blur, professional shadow
- Header: label + count
- Icon button: hover/focus states

---

### Task 3: Click behavior and state sync

**Files:**
- Modify: `src/features/topologyExplorer/components/TopologyExplorer.tsx`
- Modify: `src/pages/Topology.tsx`

- [ ] **Step 1: Add new callbacks/props to `TopologyExplorer`**

Add props to allow passing collapse handler down to `TopologyCanvas`.

- [ ] **Step 2: Implement collapse behavior**

When group box requests collapse:
- call the same `handleToggleAggregateNode(aggregateId)`
- optionally set selection to `aggregateId` after collapse (if aggregate node exists again). If not available immediately, skip selection.

---

### Task 4: Verification

**Files:**
- N/A

- [ ] **Step 1: Manual check in UI**

Run:
- Frontend: `npm run dev -- --port 5173 --strictPort`

Expected:
- Click a聚合节点 -> 成员展开 + 分组框出现
- 分组框点击 或 收回icon点击 -> 聚合节点恢复
- 多个聚合展开时，每个分组框独立显示与收回

- [ ] **Step 2: TypeScript build**

Run: `npm run build`
Expected: build succeeds

