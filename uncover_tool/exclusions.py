"""Export native exclusions and validate them in a fresh database session."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from .common import METRICS, ToolError, locator_key, require, resolve, state, walk_bins


def parse_records(text):
    """Keep native records opaque, including multiline/unsupported grammars."""
    checksum, scope = None, None
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("CHECKSUM:"):
            checksum, scope = line, None
        elif line.startswith(("INSTANCE:", "MODULE:")):
            scope = line
        else:
            require(checksum is not None and scope is not None, f"Unrecognized native exclusion context: {line}")
            result.append((checksum, scope, line))
    return result


def render_records(records):
    groups = defaultdict(list)
    for checksum, scope, record in records:
        groups[checksum, scope].append(record)
    lines = ["// Candidate exclusions exported by Verdi NPI. Existing exclusions are not duplicated."]
    for (checksum, scope), records in sorted(groups.items()):
        lines.extend([checksum, scope, *dict.fromkeys(records)])
    return "\n".join(lines) + "\n"


def snapshot_scopes(session, scopes):
    result = {}
    for metric, path in sorted(scopes):
        root = session.handle(path, metric)
        if root is None:
            continue
        result[metric, path] = {locator_key(trail): state(node, session.test)
            for node, trail, _ in session.bins(path, metric)}
    return result


def export(session, inventory, output, work):
    items = list(inventory.selected())
    scopes = {(item["metric"], item["instance_path"]) for item in items}
    for item in items:
        scopes.update((item["metric"], peer["instance_path"])
                      for peer in item.get("same_rtl_coverage", {}).get("instances", []))
    before = snapshot_scopes(session, scopes)
    baseline_path = work / "baseline.el"
    require(session.test.save_exclude_file(str(baseline_path), "w") == 1, "Cannot save existing exclusion baseline")
    baseline = set(parse_records(baseline_path.read_text(encoding="utf-8")))
    expected = defaultdict(set)
    for item in items:
        key = item["metric"], item["instance_path"]
        node, parents = resolve(session.handle(item["instance_path"], item["metric"]), item["native_locator"], session.cov.release_handle)
        if item["metric"] == "line":
            block_index = next((i for i, step in enumerate(item["native_locator"]) if step["type"] == "npiCovBlock"), None)
            require(block_index is not None, "Native Line bin has no parent Block; cannot determine exclusion impact")
            prefix = locator_key(item["native_locator"][:block_index + 1])
            expected[key].update(loc for loc in before[key] if loc[:len(prefix)] == prefix and before[key][loc]["status"] == "uncovered")
        else:
            expected[key].add(locator_key(item["native_locator"]))
        try:
            require(node.set_status_excluded(session.test, 1) == 1, f"Native exclusion failed for {item['target_id']}")
        finally:
            session.release_parents(parents)
    union_path = work / "with_candidates.el"
    require(session.test.save_exclude_file(str(union_path), "w") == 1, "Cannot export candidate exclusions")
    union = set(parse_records(union_path.read_text(encoding="utf-8")))
    require(baseline <= union, "Native export dropped existing exclusion records")
    added = sorted(union - baseline)
    selected_paths = {item["instance_path"] for item in items}
    require(all(scope.startswith("INSTANCE:") and scope.split(":", 1)[1].strip() in selected_paths
                for _, scope, _ in added), "Native export changed a module/unselected instance scope")
    if items:
        require(added, "Native export added no records for selected uncovered targets")
    output.write_text(render_records(added), encoding="utf-8")
    # Record exact native output without inventing numbering or checksums.
    for seq, row in inventory.db.execute("SELECT seq,record FROM selected").fetchall():
        item = json.loads(row)
        scoped = [list(rec) for rec in added if rec[1] == "INSTANCE: " + item["instance_path"]]
        item["exclusion"] = {"native_records_for_instance": scoped,
            "record_scope": "all exported candidates in this instance; not necessarily only this target",
            "expected_affected_native_locators": [list(loc) for loc in sorted(expected[item["metric"], item["instance_path"]])],
            "affected_scope": "selected candidate union in this instance/metric"}
        inventory.db.execute("UPDATE selected SET record=? WHERE seq=?", (json.dumps(item), seq))
    inventory.db.commit()
    return before, expected, {"status": "exported", "native_record_count": len(added),
        "existing_native_record_count": len(baseline), "method": "native cumulative export minus existing baseline; no unload_exclusion"}


def verify(session, path, before, expected):
    baseline = snapshot_scopes(session, set(before))
    require(baseline == before, "Fresh validation session does not reproduce original states; existing exclusions/test selection differ")
    if any(expected.values()):
        require(session.test.load_exclude_file(str(path)) == 1, "Native candidate exclusion reload failed")
    after = snapshot_scopes(session, set(before))
    changed = 0
    for key, states in before.items():
        require(states.keys() == after[key].keys(), f"Native model changed after exclusion in {key}")
        for loc, old in states.items():
            new = after[key][loc]
            if loc in expected[key]:
                require(old["status"] == "uncovered" and new["excluded"] and new["coverable"] == 0,
                        f"Expected atomic exclusion did not apply to {key}/{loc}")
                changed += 1
            else:
                require(new == old, f"Unselected/existing/peer bin changed at {key}/{loc}")
    return {"status": "passed", "method": "fresh VDB copy/session, actual candidate .el reload and per-bin state comparison",
        "checked_scopes": len(before), "checked_atomic_bins": sum(map(len, before.values())),
        "newly_excluded_atomic_bins": changed, "existing_and_unselected_bins_in_checked_scopes_unchanged": True,
        "scope": "selected instances and compatible peers; not a proof about every bin in the entire design"}
