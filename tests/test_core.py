"""Native-free regression tests for facts, identity, mapping and CLI boundaries."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from uncover_tool.cli import parser, configuration
from uncover_tool.common import ToolError, descriptor, in_scope, location, resolve, state, walk_bins
from uncover_tool.exclusions import parse_records, render_records
from uncover_tool.mapping import SourceMapper
from uncover_tool.native import Inventory, NativeSession, PeerComparison, scan


class Node:
    def __init__(self, name, kind="npiCovToggleBin", covered=0, coverable=1, raw=0,
                 source="/old/rtl/a.sv", line=3, children=()):
        self.values = name, kind, covered, coverable, raw, source, line, children

    def name(self): return self.values[0]
    def type(self): return self.values[1]
    def covered(self, _): return self.values[2]
    def coverable(self, _): return self.values[3]
    def status(self, _): return self.values[4]
    def file_name(self): return self.values[5]
    def line_no(self): return self.values[6]
    def child_handles(self): return list(self.values[7])


class StateTests(unittest.TestCase):
    def test_state_flags_are_independent_of_display_priority(self):
        value = state(Node("b", coverable=0, raw=1 | 4 | 64), None)
        self.assertEqual(value["status"], "excluded")
        self.assertTrue(value["unreachable"])
        self.assertTrue(value["flags"]["excluded_at_compile_time"])
        self.assertTrue(value["flags"]["excluded_at_report_time"])

    def test_raw_zero_only_becomes_hole_when_scored(self):
        for raw, total, status in [(0, 1, "uncovered"), (0, 0, "not_scored"),
                                   (4, 0, "excluded"), (1, 0, "unreachable"), (2, 1, "illegal")]:
            self.assertEqual(state(Node("x", raw=raw, coverable=total), None)["status"], status)

    def test_direction_and_same_line_bins_keep_separate_locators(self):
        children = [Node("0 -> 1", covered=1), Node("1 -> 0")]
        root = Node("signal", kind="npiCovSignalBit", covered=1, coverable=2, children=children)
        found = list(walk_bins(root, "npiCovToggleBin", None, missing_only=True))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1][0]["index"], 1)
        self.assertIs(resolve(root, found[0][1])[0], children[1])
        wrong = Node("signal", kind="npiCovSignalBit", children=[children[0], Node("changed")])
        with self.assertRaises(ToolError): resolve(wrong, found[0][1])

    def test_location_is_an_actual_file_line_pair(self):
        parent = Node("parent", source="parent.sv", line=4)
        child = Node("child", source=None, line=99)
        self.assertEqual(location([parent, child]), ("parent.sv", 4))

    def test_instance_prefix_has_component_boundary(self):
        self.assertTrue(in_scope("top.dut.u", ["top.dut"]))
        self.assertFalse(in_scope("top.dut2.u", ["top.dut"]))

    def test_native_child_handles_released_when_iteration_is_closed(self):
        children=[Node("0 -> 1"),Node("1 -> 0")]
        root=Node("signal",kind="npiCovSignal",covered=0,coverable=2,children=children)
        released=[]
        iterator=walk_bins(root,"npiCovToggleBin",None,release=released.append)
        next(iterator);iterator.close()
        self.assertEqual(released,children)

    def test_walk_state_snapshot_reads_each_leaf_once_and_keeps_legacy_shape(self):
        reads = []
        class Counted(Node):
            def covered(self, test):
                reads.append(self.name())
                return super().covered(test)
        leaves = [Counted("hole"), Counted("hit", covered=1), Counted("excluded", coverable=0, raw=64)]
        root = Node("metric", kind="metric", covered=1, coverable=2, children=leaves)
        for missing_only, expected in [(True, ["hole"]), (False, ["hole", "hit", "excluded"])]:
            reads.clear()
            rows = list(walk_bins(root, "npiCovToggleBin", None, missing_only=missing_only, with_state=True))
            self.assertEqual([row[0].name() for row in rows], expected)
            self.assertEqual(reads, ["hole", "hit", "excluded"])
            self.assertTrue(all(row[3] == state(row[0], None) for row in rows))
        self.assertTrue(all(len(row) == 3 for row in walk_bins(root, "npiCovToggleBin", None)))

    def test_descriptor_locations_match_native_fallback_without_repeated_child_reads(self):
        for root_file, root_line in [("root.sv", 7), (None, 0)]:
            for parent_file, parent_line in [("parent.sv", 4), ("parent.sv", 0), (None, 99)]:
                for child_file, child_line in [("child.sv", 5), ("child.sv", -1), (None, 99)]:
                    nodes = [Node("root", source=root_file, line=root_line),
                             Node("parent", source=parent_file, line=parent_line),
                             Node("child", source=child_file, line=child_line)]
                    trail = [{"index": 0, **descriptor(n)} for n in nodes[1:]]
                    expected = location(nodes)
                    def duplicate(): raise AssertionError("Repeated child source read")
                    for node in nodes[1:]:
                        node.file_name = node.line_no = duplicate
                    self.assertEqual(location(nodes, trail), expected)

    def test_scan_consumes_snapshot_once_in_pruned_and_complete_modes(self):
        for complete in (False, True):
            reads = []
            class Counted(Node):
                def covered(self, test):
                    reads.append(self.name())
                    return super().covered(test)
            children = [Counted("hole", kind="npiCovStmtBin"),
                        Counted("hit", kind="npiCovStmtBin", covered=1),
                        Counted("excluded", kind="npiCovStmtBin", coverable=0, raw=64)]
            root = Node("line", kind="metric", covered=1, coverable=2, children=children)
            with tempfile.TemporaryDirectory() as tmp:
                session = NativeSession({}, Path(tmp))
                session.instances = {"top.u": Node("u")}
                session.test, session.selected_tests = None, ["test"]
                session.cov = SimpleNamespace(release_handle=lambda node: None)
                session.handle = lambda path, metric: root
                session.info = lambda path, metric: {"module_name": "unit", "model_key": "model"}
                inventory = Inventory(Path(tmp) / "inventory.sqlite")
                try:
                    summary, _ = scan(session, {"metrics": ["line"], "instance_roots": [], "limit": 1,
                        "include_other_states": complete}, inventory, SourceMapper([]), "database")
                    self.assertEqual(reads, ["hole", "hit", "excluded"])
                    self.assertEqual(summary["line"]["uncovered_candidates"], 1)
                    self.assertEqual(summary["line"]["visited_atomic_bins"], 3 if complete else 1)
                    self.assertEqual(len(list(inventory.candidates("line"))), 1)
                finally:
                    inventory.db.close()


class MappingTests(unittest.TestCase):
    def test_duplicate_basename_is_ambiguous_and_missing_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ["a", "b"]:
                (root / sub).mkdir()
                (root / sub / "ctrl.sv").write_text("module ctrl; endmodule")
            mapper = SourceMapper([root])
            self.assertEqual(mapper.map("/old/ctrl.sv")["status"], "ambiguous")
            self.assertEqual(mapper.map("/old/a/ctrl.sv")["path"], str(root / "a/ctrl.sv"))
            self.assertEqual(mapper.map("/old/missing.sv")["status"], "missing")

    def test_explicit_mapping_does_not_fall_back_to_old_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "old", root / "new"
            old.mkdir(); new.mkdir()
            (old / "a.sv").write_text("old")
            mapper = SourceMapper([], [(str(old), str(new))])
            self.assertEqual(mapper.map(str(old / "a.sv"))["status"], "missing")
            (new / "a.sv").write_text("new")
            value = SourceMapper([], [(str(old), str(new))]).map(str(old / "a.sv"))
            self.assertEqual(value["path"], str(new / "a.sv"))
            self.assertEqual(value["correspondence"], "unconfirmed")

    def test_conflicting_equal_length_maps_do_not_pick_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for sub in ["a", "b"]:
                (root/sub).mkdir(); (root/sub/"x.sv").write_text(sub)
            self.assertEqual(SourceMapper([], [("/old",root/"a"),("/old",root/"b")]).map("/old/x.sv")["status"], "ambiguous")


class ExclusionTests(unittest.TestCase):
    def test_native_numbers_multiple_records_and_scope_are_preserved(self):
        text = 'CHECKSUM: "11 22"\nINSTANCE: top.u\nBranch 0 "44" "x" (0) "x 1"\nCondition 1 "55" "x 1 -1" (2 "1")\nToggle 1to0 data [2] [7] "net data[3:0][31:0]"\n'
        self.assertEqual(set(parse_records(render_records(parse_records(text)))), set(parse_records(text)))
        self.assertEqual(len(parse_records(text)), 3)

    def test_unrecognized_context_is_not_silently_dropped(self):
        with self.assertRaises(ToolError): parse_records('Block 1 "9" "x"')


class ComparisonTests(unittest.TestCase):
    def test_include_source_can_find_peers_through_model_identity(self):
        leaf=Node("7",kind="npiCovStmtBin",source="leaf.svh",line=7)
        root=Node("Line",kind="npiCovLineMetric",children=[leaf])
        class Session:
            instances={"top.a":Node("a",source="wrapper.sv"),"top.b":Node("b",source="wrapper.sv")}
            definitions={p:{"sources":["wrapper.sv"]} for p in instances}
            test=None
            class cov:
                @staticmethod
                def release_handle(_): pass
            def info(self,path,metric): return {"model_key":"same-definition-model"}
            def handle(self,path,metric): return root
            def release_parents(self,parents): pass
        row={"metric":"line","instance_path":"top.a","source_file":"leaf.svh",
             "native_locator":list(next(walk_bins(root,"npiCovStmtBin",None))[1])}
        PeerComparison(Session()).attach(row)
        self.assertEqual(row["same_rtl_coverage"]["comparable_instance_count"],2)


class ConfigurationTests(unittest.TestCase):
    def test_conflicting_test_options_and_nonpositive_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vdb=root/"x.vdb";(vdb/"snps/coverage/db").mkdir(parents=True)
            args=parser().parse_args(["--vdb",str(vdb),"--out",str(root/"out"),"--limit", "0"])
            with self.assertRaises(ToolError): configuration(args)
            args=parser().parse_args(["--vdb",str(vdb),"--out",str(vdb/"out")])
            with self.assertRaises(ToolError): configuration(args)

    def test_overwrite_does_not_replace_unowned_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vdb=root/"x.vdb";(vdb/"snps/coverage/db").mkdir(parents=True)
            out=root/"out";out.mkdir();(out/"precious").write_text("keep")
            args=parser().parse_args(["--vdb",str(vdb),"--out",str(out),"--overwrite"])
            with self.assertRaises(ToolError): configuration(args)
            self.assertEqual((out/"precious").read_text(),"keep")

    def test_inventory_limit_is_prefix_of_full_stable_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv=Inventory(Path(tmp)/"inventory.sqlite")
            try:
                for number in [12,2,1]:
                    inv.add({"metric":"line","instance_path":"new.project", "source_file":"other.sv", "start_line":7,
                             "native_locator":[{"index":number}]})
                all_rows=list(inv.candidates("line"))
                self.assertEqual([r["native_locator"][0]["index"] for r in all_rows],[1,2,12])
                self.assertEqual(list(inv.candidates("line",2)),all_rows[:2])
                self.assertEqual(list(inv.candidates("tgl")),[])
            finally: inv.db.close()


if __name__ == "__main__": unittest.main()
