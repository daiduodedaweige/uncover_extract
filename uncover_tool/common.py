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


def display_status(covered, coverable, raw):
    bits = raw if raw >= 0 else 0
    if bits & (1 << STATUS_BITS["illegal"]):
        return "illegal"
    if bits & ((1 << STATUS_BITS["excluded_at_compile_time"]) | (1 << STATUS_BITS["excluded_at_report_time"])):
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


def walk_bins(root, bin_type, test, missing_only=False, trail=(), parents=(), release=None, with_state=False):
    """Indices are local locators, always checked against the object descriptors."""
    if root.type() == bin_type:
        current = state(root, test) if missing_only or with_state else None
        if not missing_only or current["status"] == "uncovered":
            result = root, trail, parents + (root,)
            yield (*result, current) if with_state else result
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
                                     trail + ({"index": index, **descriptor(child)},), parents + (root,), release, with_state)
            finally:
                if release:
                    release(child)
                children[index] = None
    finally:
        if release:
            for child in children:
                if child is not None:
                    release(child)


def resolve(root, trail, release=None, counters=None, progress=None):
    parents = [root]
    try:
        for step in trail:
            if progress is not None:
                progress()
            children = root.child_handles()
            if counters is not None:
                counters["child_handles_calls"] += 1
                counters["returned_child_handles"] += len(children)
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


def read_targets(root, targets, test, release, counters=None, progress=None, strategy="auto"):
    """Read snapshots through a descriptor-checked trie, owning only its children.

    Call only when no other traversal owns aliases of these handles. Results
    contain plain data; every returned child handle is released on all exits.
    """
    require(strategy in ("auto", "single", "batch"), "Unknown locator strategy")
    if strategy == "single" or (strategy == "auto" and len(targets) == 1):
        if counters is None:
            release_owned = release
        else:
            def release_owned(node):
                release(node)
                counters["released_child_handles"] += 1
        results = {}
        for key, trail in targets.items():
            try:
                node, parents = resolve(root, trail, release_owned, counters, progress)
            except ToolError as exc:
                results[key] = {"error": str(exc)}
                continue
            try:
                results[key] = state(node, test)
            finally:
                for parent in reversed(parents[1:]):
                    release_owned(parent)
        return results

    tree = {"ends": [], "children": {}}
    for key, trail in targets.items():
        branch = tree
        for step in trail:
            expected = tuple(sorted((k, v) for k, v in step.items() if k != "index"))
            variants = branch["children"].setdefault(step["index"], {})
            branch = variants.setdefault(expected, {"ends": [], "children": {}})
        branch["ends"].append(key)
    results = {}

    def fail(branch, reason):
        for key in branch["ends"]:
            results[key] = {"error": reason}
        for variants in branch["children"].values():
            for sub in variants.values():
                fail(sub, reason)

    def visit(node, branch):
        if progress is not None:
            progress()
        if branch["ends"]:
            value = state(node, test)
            for key in branch["ends"]:
                results[key] = value
        if not branch["children"]:
            return
        children = node.child_handles()
        if counters is not None:
            counters["child_handles_calls"] += 1
            counters["returned_child_handles"] += len(children)
        try:
            for index, variants in branch["children"].items():
                if not 0 <= index < len(children):
                    for sub in variants.values():
                        fail(sub, "Incompatible model: native child count changed")
                    continue
                child = children[index]
                actual = tuple(sorted(descriptor(child).items()))
                for expected, sub in variants.items():
                    if expected != actual:
                        fail(sub, "Incompatible model: native target descriptor changed")
                    else:
                        visit(child, sub)
        finally:
            for child in children:
                release(child)
                if counters is not None:
                    counters["released_child_handles"] += 1

    visit(root, tree)
    return results


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
