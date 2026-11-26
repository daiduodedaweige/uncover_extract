"""Portable identity, state and filesystem helpers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

METRICS = {
    "line": ("line_metric_handle", "npiCovStmtBin"),
    "tgl": ("toggle_metric_handle", "npiCovToggleBin"),
    "branch": ("branch_metric_handle", "npiCovBranchBin"),
    "cond": ("condition_metric_handle", "npiCovConditionBin"),
}
STATUS_BITS = {
    "unreachable": 0, "illegal": 1, "excluded_at_compile_time": 2,
    "covered": 3, "proven": 5, "excluded_at_report_time": 6,
    "attempted": 7, "partially_excluded": 8, "partially_attempted": 9,
}


class ToolError(Exception):
    """An input, compatibility or native operation error with a useful message."""


def require(condition, message):
    if not condition:
        raise ToolError(message)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def fingerprint(directory):
    directory = Path(directory)
    files = {}
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), f"VDB contains a symbolic link: {path}; materialize it before extraction")
        if path.is_file():
            files[str(path.relative_to(directory))] = digest_file(path)
    require(files, f"Empty input directory: {directory}")
    identity = stable_id(files)
    return {"method": "sha256_all_files", "sha256": identity, "file_count": len(files), "files": files}


def stable_id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def in_scope(path, roots):
    return not roots or any(path == root or path.startswith(root + ".") for root in roots)


def state(node, test):
    covered, coverable, raw = node.covered(test), node.coverable(test), node.status(test)
    return state_from_facts(covered, coverable, raw)


def is_excluded(raw):
    return raw >= 0 and bool(raw & ((1 << STATUS_BITS["excluded_at_compile_time"]) |
                                    (1 << STATUS_BITS["excluded_at_report_time"])))


def display_status(covered, coverable, raw):
    bits = raw if raw >= 0 else 0
    if bits & (1 << STATUS_BITS["illegal"]):
        return "illegal"
    if is_excluded(raw):
        return "excluded"
    if bits & (1 << STATUS_BITS["unreachable"]):
        return "unreachable"
    if coverable == 1 and covered in (0, 1):
        return "covered" if covered else "uncovered"
    return "not_scored" if coverable == 0 else "unknown"


def state_from_facts(covered, coverable, raw):
    """Reconstruct all independent flags from lossless native facts."""
    flags = {name: bool(raw >= 0 and raw & (1 << bit)) for name, bit in STATUS_BITS.items()}
    excluded = flags["excluded_at_compile_time"] or flags["excluded_at_report_time"]
    return {"covered": covered, "coverable": coverable, "raw_status": raw,
            "flags": flags, "excluded": excluded, "unreachable": flags["unreachable"],
            "status": display_status(covered, coverable, raw)}


def descriptor(node):
    line = node.line_no()
    return {"type": node.type(), "name": node.name(), "source_file": node.file_name(),
            "line": line if line > 0 else None}


def location(nodes, trail=None):
    # File and line must come from the same native object.
    if trail is not None and len(trail) == len(nodes) - 1:
        for step in reversed(trail):
            if step["source_file"] and step["line"] is not None:
                return step["source_file"], step["line"]
        file, line = nodes[0].file_name(), nodes[0].line_no()
        if file and line > 0:
            return file, line
        for step in reversed(trail):
            if step["source_file"]:
                return step["source_file"], None
        return file or None, None
    for node in reversed(nodes):
        file, line = node.file_name(), node.line_no()
        if file and line > 0:
            return file, line
    for node in reversed(nodes):
        if node.file_name():
            return node.file_name(), None
    return None, None


def walk_bins(root, bin_type, trail=(), parents=(), release=None):
    """Walk full native descriptors; child handles stay local to this traversal."""
    if root.type() == bin_type:
        yield root, trail, parents + (root,)
        return
    children = root.child_handles()
    try:
        for index, child in enumerate(children):
            try:
                yield from walk_bins(child, bin_type,
                                     trail + ({"index": index, **descriptor(child)},), parents + (root,), release)
            finally:
                if release:
                    release(child)
                children[index] = None
    finally:
        if release:
            for child in children:
                if child is not None:
                    release(child)


def native_totals(handle, test, release=None):
    total, covered = handle.coverable(test), handle.covered(test)
    if total >= 0 and covered >= 0:
        return covered, total
    values = []
    for child in handle.child_handles():
        try:
            values.append(native_totals(child, test, release))
        finally:
            if release: release(child)
    return sum(c for c, _ in values), sum(t for _, t in values)
