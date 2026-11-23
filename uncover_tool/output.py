"""Formatting helpers for grouped coverage reports."""

import os
from urllib.parse import quote


def cell(value):
    return str(value if value is not None else "未知").replace("|", "&#124;").replace("\n", " ").replace("`", "&#96;")


def source_cells(source, line, mapping, report_dir=None):
    source_text, line_text = cell(source), cell(line)
    if mapping.get("status") != "resolved" or not mapping.get("path"):
        return source_text, line_text
    path = mapping["path"]
    target = quote(os.path.relpath(path, report_dir) if report_dir is not None else path, safe="/:")
    label = cell(path).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"[{label}]({target})", line_text


def target_description(metric, trail):
    """Line targets are located by their existing line column, not block names."""
    return "" if metric == "line" else " / ".join(str(step["name"]) for step in trail)
