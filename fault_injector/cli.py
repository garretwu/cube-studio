from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from fault_injector.config import load_config
from fault_injector.injector import FaultInjector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cube Studio fault injector")
    parser.add_argument("--config", required=True, help="Path to injector conf json config")
    parser.add_argument("--session-id", default="demo-session", help="Session id for WAL")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inject-roce-mtu-mismatch")
    subparsers.add_parser("rollback")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    injector = FaultInjector(config=config, session_id=args.session_id)

    if args.command == "inject-roce-mtu-mismatch":
        report = injector.inject_roce_mtu_mismatch()
    else:
        report = injector.rollback()
    print(json.dumps([asdict(item) for item in report], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
