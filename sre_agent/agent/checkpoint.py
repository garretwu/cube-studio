from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import messages_to_dict
from langgraph.checkpoint.memory import MemorySaver


def create_checkpointer() -> MemorySaver:
    return MemorySaver()


def persist_state_snapshot(checkpoint_dir: str | None, session_id: str, node_name: str, state: dict[str, Any]) -> None:
    if not checkpoint_dir:
        return
    base_dir = Path(checkpoint_dir).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    payload = _serialize_state(state)
    snapshot_path = base_dir / f"{session_id}-{node_name}.json"
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    payload = dict(state)
    messages = payload.get("messages")
    if isinstance(messages, list):
        payload["messages"] = messages_to_dict(messages)
    return payload
