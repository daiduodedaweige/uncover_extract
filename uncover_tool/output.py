"""Formatting, paths and ordering for per-instance coverage reports."""

import os
import re
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


def natural_key(value):
    """A deterministic natural key also usable by SQLite's binary ordering."""
    return re.sub(r"\d+", lambda m: "\x01" + str(len(m[0].lstrip('0') or '0')).zfill(6)
                  + (m[0].lstrip('0') or '0') + "\x02", str(value or ""))


def locator_sort(trail):
    return natural_key(" / ".join(str(step.get("name", "")) for step in trail))


def instance_parts(path):
    """Dots inside Verilog escaped identifiers are not hierarchy separators."""
    parts, current, escaped = [], [], False
    for char in path:
        if char == "\\":
            escaped = True
        if char.isspace():
            escaped = False
        if char == "." and not escaped:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    return parts + ["".join(current)]


def instance_directory(path):
    from pathlib import Path
    # Encode each component reversibly; dots are encoded to avoid page-name collisions.
    return Path("reports/instances").joinpath(*(quote(part, safe="-_").replace(".", "%2E")
                                                for part in instance_parts(path)))


def relative_link(label, target, directory):
    return f"[{cell(label)}]({quote(os.path.relpath(target, directory), safe='/')})"
