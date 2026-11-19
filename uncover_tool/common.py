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
    flags = {name: bool(raw >= 0 and raw & (1 << bit)) for name, bit in STATUS_BITS.items()}
    excluded = flags["excluded_at_compile_time"] or flags["excluded_at_report_time"]
    if flags["illegal"]:
        status = "illegal"
    elif excluded:
        status = "excluded"
    elif flags["unreachable"]:
        status = "unreachable"
    elif coverable == 1 and covered in (0, 1):
        status = "covered" if covered else "uncovered"
    elif coverable == 0:
        status = "not_scored"
    else:
        status = "unknown"
    return {"covered": covered, "coverable": coverable, "raw_status": raw,
            "flags": flags, "excluded": excluded, "unreachable": flags["unreachable"], "status": status}


def descriptor(node):
    return {"type": node.type(), "name": node.name(), "source_file": node.file_name(),
            "line": node.line_no() if node.line_no() > 0 else None}


def location(nodes):
    # File and line must come from the same native object.
    for node in reversed(nodes):
        file, line = node.file_name(), node.line_no()
        if file and line > 0:
            return file, line
    for node in reversed(nodes):
        if node.file_name():
            return node.file_name(), None
    return None, None


def walk_bins(root, bin_type, test, missing_only=False, trail=(), parents=(), release=None):
    """Indices are local locators, always checked against the object descriptors."""
    if root.type() == bin_type:
        if not missing_only or state(root, test)["status"] == "uncovered":
            yield root, trail, parents + (root,)
        return
    if missing_only:
        total, covered = root.coverable(test), root.covered(test)
        if total == 0 or (total > 0 and covered == total):
            return
    children = root.child_handles()
    try:
        for index, child in enumerate(children):
            try:
                yield from walk_bins(child, bin_type, test, missing_only,
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


def resolve(root, trail, release=None):
    parents = [root]
    try:
        for step in trail:
            children = root.child_handles()
            index = step["index"]
            if not 0 <= index < len(children):
                if release:
                    for child in children: release(child)
                raise ToolError("Incompatible model: native child count changed")
            root = children[index]
            if release:
                for i, child in enumerate(children):
                    if i != index: release(child)
            parents.append(root)
            require(descriptor(root) == {k: v for k, v in step.items() if k != "index"},
                    "Incompatible model: native target descriptor changed")
    except BaseException:
        if release:
            for parent in reversed(parents[1:]): release(parent)
        raise
    return root, parents


def locator_key(trail):
    return tuple(step["index"] for step in trail)


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
