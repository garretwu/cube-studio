#!/usr/bin/env python3
"""Sync frontend-backend contract markdown from backend schema."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from starlette.routing import WebSocketRoute

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sre_agent.models.events import EventType
from sre_agent.server import create_app

DEFAULT_DOC_PATH = REPO_ROOT / "sre_agent" / "docs" / "frontend_backend_contract_current.md"
MARKER_BEGIN = "<!-- CONTRACT:BEGIN -->"
MARKER_END = "<!-- CONTRACT:END -->"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync current frontend-backend contract markdown.")
    parser.add_argument("--doc", default=str(DEFAULT_DOC_PATH), help="Output markdown document path.")
    parser.add_argument("--check", action="store_true", help="Check mode: fail when document is out of date.")
    return parser


def ensure_auth_env() -> None:
    os.environ.setdefault("JWT_SECRET", "contract-doc-sync-secret")


def collect_contract_data() -> tuple[dict[str, Any], list[str], list[str]]:
    ensure_auth_env()
    app = create_app()
    schema = app.openapi()
    ws_paths = sorted(route.path for route in app.routes if isinstance(route, WebSocketRoute))
    ws_events = [member.value for member in EventType]
    return schema, ws_paths, ws_events


def _response_shape(operation: dict[str, Any]) -> str:
    responses = operation.get("responses", {})
    if not isinstance(responses, dict):
        return "-"
    for code in ("200", "201", "202", "default"):
        response = responses.get(code)
        if not isinstance(response, dict):
            continue
        content = response.get("content", {})
        if not isinstance(content, dict):
            continue
        app_json = content.get("application/json", {})
        if not isinstance(app_json, dict):
            continue
        schema = app_json.get("schema", {})
        if isinstance(schema, dict) and "$ref" in schema:
            return str(schema["$ref"]).split("/")[-1]
        if isinstance(schema, dict) and schema.get("type"):
            return str(schema["type"])
    return "-"


def _auth_mode(operation: dict[str, Any]) -> str:
    security = operation.get("security")
    if isinstance(security, list) and security:
        return "Bearer JWT"
    return "公开"


def render_matrix(schema: dict[str, Any], ws_paths: list[str], ws_events: list[str]) -> str:
    paths = schema.get("paths", {})
    http_lines = [
        "### REST 接口矩阵（自动生成）",
        "",
        "| Method | Path | 鉴权 | 响应主体 |",
        "| --- | --- | --- | --- |",
    ]
    if isinstance(paths, dict):
        for path in sorted(paths.keys()):
            operations = paths[path]
            if not isinstance(operations, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete", "options"):
                operation = operations.get(method)
                if not isinstance(operation, dict):
                    continue
                http_lines.append(
                    f"| `{method.upper()}` | `{path}` | {_auth_mode(operation)} | `{_response_shape(operation)}` |"
                )

    ws_lines = [
        "",
        "### WS 路径矩阵（自动生成）",
        "",
        "| Path | 用途 |",
        "| --- | --- |",
    ]
    for path in ws_paths:
        usage = "实时事件流"
        if "thinking-trace" in path:
            usage = "诊断思维链路流"
        elif "alerts" in path:
            usage = "告警实时流"
        elif "chat" in path:
            usage = "对话流"
        elif "topology" in path:
            usage = "拓扑同步与增量更新流"
        ws_lines.append(f"| `{path}` | {usage} |")

    event_lines = [
        "",
        "### WS 事件类型（自动生成）",
        "",
        "| EventType | 说明 |",
        "| --- | --- |",
    ]
    for event_name in ws_events:
        description = "通用事件"
        if event_name == "topology":
            description = "拓扑同步与节点/边增量事件"
        elif event_name == "alert":
            description = "告警变更事件"
        elif event_name == "thinking_step":
            description = "诊断思维步骤"
        elif event_name == "tool_call":
            description = "工具调用请求"
        elif event_name == "tool_result":
            description = "工具调用结果"
        elif event_name == "diagnosis_result":
            description = "诊断结论事件"
        elif event_name == "remediation_progress":
            description = "修复进度事件"
        elif event_name == "done":
            description = "会话结束事件"
        elif event_name == "error":
            description = "错误事件"
        event_lines.append(f"| `{event_name}` | {description} |")

    return "\n".join([*http_lines, *ws_lines, *event_lines]).rstrip() + "\n"


def render_default_doc(generated_block: str) -> str:
    return f"""# 前后端契约（Current）

> 该文档是当前生效的前后端契约主入口，历史版本请见 `frontend_backend_contracts.md`。

## 1. 鉴权与通用信封

- 鉴权方式：`Authorization: Bearer <JWT>`
- 追踪头：`x-trace-id`
- 响应信封：`{{ success, data, error, trace_id, timestamp }}`

## 2. REST / WS 矩阵

{MARKER_BEGIN}
{generated_block.rstrip()}
{MARKER_END}

## 3. 关键 Payload 示例

### 3.1 拓扑快照 `GET /api/topology`

```json
{{
  "success": true,
  "data": {{
    "nodes": [],
    "edges": [],
    "active_alerts": 0,
    "recent_events": [],
    "snapshot_id": "snapshot-1",
    "last_synced_at": "2026-03-30T10:00:00Z",
    "sync_state": "ready"
  }},
  "trace_id": "trace-1",
  "timestamp": "2026-03-30T10:00:00Z"
}}
```

### 3.2 拓扑 WS 事件 `WS /ws/topology`

```json
{{
  "schema_version": "1.0",
  "type": "topology",
  "session_id": "topology",
  "timestamp": "2026-03-30T10:00:00Z",
  "data": {{
    "event_id": "12",
    "action": "node_upsert",
    "node": {{
      "id": "worker-01",
      "entity_type": "node"
    }}
  }}
}}
```

## 4. 前端绑定关系

- API Client：`sre_agent/frontend/src/api/client.ts`
- 类型定义：`sre_agent/frontend/src/api/types.ts`
- 拓扑状态管理：`sre_agent/frontend/src/store/topologyStore.ts`
- 拓扑页面：`sre_agent/frontend/src/pages/Topology.tsx`
- WS 管理：`sre_agent/frontend/src/api/ws.ts`

## 5. 变更记录

- 2026-03-30：新增 topology 运行时发现状态与手工触发接口；新增 `/ws/topology`；契约矩阵改为自动同步。
"""


def merge_generated_block(existing: str, generated_block: str) -> str:
    if MARKER_BEGIN not in existing or MARKER_END not in existing:
        return render_default_doc(generated_block)
    prefix, remain = existing.split(MARKER_BEGIN, 1)
    _, suffix = remain.split(MARKER_END, 1)
    middle = f"{MARKER_BEGIN}\n{generated_block.rstrip()}\n{MARKER_END}"
    return f"{prefix.rstrip()}\n\n{middle}\n{suffix.lstrip()}".rstrip() + "\n"


def main() -> int:
    args = build_parser().parse_args()
    target_path = Path(args.doc).expanduser()
    schema, ws_paths, ws_events = collect_contract_data()
    generated_block = render_matrix(schema, ws_paths, ws_events)

    existing = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    merged = merge_generated_block(existing, generated_block)

    if args.check:
        if existing != merged:
            print(f"[mismatch] contract doc is out of date: {target_path}")
            return 1
        print(f"[ok] contract doc is up to date: {target_path}")
        return 0

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(merged, encoding="utf-8")
    print(f"[ok] wrote contract doc: {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

