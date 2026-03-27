#!/usr/bin/env python3
"""Create standardized evidence archive folders and optionally capture command outputs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


VALID_CATEGORIES = {"api", "ws", "metrics", "ui"}


@dataclass
class CaptureResult:
    category: str
    name: str
    command: str
    exit_code: int
    log_path: str


def _parse_capture_spec(raw: str) -> tuple[str, str, str]:
    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise ValueError("capture spec must be '<category>:<name>:<command>'")
    category, name, command = (item.strip() for item in parts)
    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid category {category!r}, expected one of {sorted(VALID_CATEGORIES)}")
    if not name:
        raise ValueError("capture name must not be empty")
    if not command:
        raise ValueError("capture command must not be empty")
    return category, name, command


def _ensure_structure(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for category in sorted(VALID_CATEGORIES):
        (root / category).mkdir(parents=True, exist_ok=True)


def _run_capture(root: Path, category: str, name: str, command: str, timeout_seconds: int) -> CaptureResult:
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in name)
    log_path = root / category / f"{safe_name}.log"
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    content = (
        f"# command\n{command}\n\n"
        f"# exit_code\n{proc.returncode}\n\n"
        f"# stdout\n{proc.stdout}\n\n"
        f"# stderr\n{proc.stderr}\n"
    )
    log_path.write_text(content, encoding="utf-8")
    return CaptureResult(
        category=category,
        name=name,
        command=command,
        exit_code=proc.returncode,
        log_path=str(log_path),
    )


def _write_summary(root: Path, run_id: str, scenario: str, env_name: str, captures: list[CaptureResult]) -> None:
    summary_path = root / "summary.md"
    lines = [
        f"# Wave Evidence Summary ({run_id})",
        "",
        f"- 时间: {datetime.now(UTC).isoformat()}",
        f"- 场景: {scenario}",
        f"- 环境: {env_name}",
        "",
        "## Captures",
        "",
        "| Category | Name | Exit Code | Log |",
        "|---|---|---:|---|",
    ]
    for item in captures:
        lines.append(f"| {item.category} | {item.name} | {item.exit_code} | `{item.log_path}` |")
    if not captures:
        lines.append("| - | - | - | - |")
    lines.extend(
        [
            "",
            "## Checklist",
            "",
            "- [ ] UI 关键步骤截图归档到 `ui/`",
            "- [ ] WS 事件样本归档到 `ws/`",
            "- [ ] API 请求响应样本归档到 `api/`",
            "- [ ] 指标曲线或导出数据归档到 `metrics/`",
        ]
    )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create standard wave evidence folder and capture logs.")
    parser.add_argument("--run-id", default="", help="Evidence run id, default: YYYYMMDD-HHMM-wave5-local")
    parser.add_argument("--scenario", default="wave5", help="Scenario name for summary metadata.")
    parser.add_argument("--env", default="local", help="Environment label for summary metadata.")
    parser.add_argument("--root", default="sre_agent/docs/evidence", help="Evidence root directory.")
    parser.add_argument(
        "--capture",
        action="append",
        default=[],
        help="Capture spec '<category>:<name>:<command>', category in {api,ws,metrics,ui}.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=180, help="Per-capture timeout in seconds.")
    args = parser.parse_args(argv)

    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d-%H%M')}-{args.scenario}-{args.env}"
    target = Path(args.root) / run_id
    _ensure_structure(target)

    captures: list[CaptureResult] = []
    for raw in args.capture:
        category, name, command = _parse_capture_spec(raw)
        captures.append(_run_capture(target, category, name, command, args.timeout_seconds))

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scenario": args.scenario,
        "env": args.env,
        "captures": [asdict(item) for item in captures],
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_summary(target, run_id, args.scenario, args.env, captures)
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
