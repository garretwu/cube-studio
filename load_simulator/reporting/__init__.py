"""Reporting helpers for HTML and historical comparison."""

from load_simulator.reporting.bundle import write_report_bundle
from load_simulator.reporting.comparison import compare_sessions
from load_simulator.reporting.html_report import generate_html_report
from load_simulator.reporting.session_output import build_json_payload

__all__ = ["build_json_payload", "compare_sessions", "generate_html_report", "write_report_bundle"]
