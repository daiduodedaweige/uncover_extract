"""Native-free regression tests for facts, identity, mapping and CLI boundaries."""
import json
from pathlib import Path
import tempfile
import unittest

from uncover_extract.cli import parser, configuration
from uncover_extract.common import ToolError, descriptor, in_scope, location, state, walk_bins
from uncover_extract.mapping import SourceMapper
from uncover_extract.output import source_cells


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
        found = list(walk_bins(root, "npiCovToggleBin"))
        self.assertEqual([row[1][0]["index"] for row in found], [0, 1])
        self.assertEqual([row[1][0]["name"] for row in found], ["0 -> 1", "1 -> 0"])

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
        iterator=walk_bins(root,"npiCovToggleBin",release=released.append)
        next(iterator);iterator.close()
        self.assertEqual(released,children)

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


class MappingTests(unittest.TestCase):
    def test_source_links_open_files_with_plain_line_numbers(self):
        source = "/old/a [1].sv"
        mapping = {"status": "resolved", "path": "/new/a [1]#x.sv"}
        linked, line = source_cells(source, 42, mapping)
        self.assertEqual(linked, r"[/new/a \[1\]#x.sv](/new/a%20%5B1%5D%23x.sv)")
        self.assertEqual(line, "42")
        relative, line = source_cells(source, 42, mapping, report_dir="/new/results/coverage")
        self.assertEqual(relative, r"[/new/a \[1\]#x.sv](../../a%20%5B1%5D%23x.sv)")
        self.assertEqual(line, "42")
        self.assertEqual(source_cells(source, None, mapping)[1], "未知")
        self.assertEqual(source_cells(source, 42, {"status": "missing"}), (source, "42"))

    def test_configured_rtl_root_takes_priority_over_existing_build_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, rtl = root / "old-build", root / "rtl"
            old.mkdir(); rtl.mkdir()
            source = old / "queue.sv"
            source.write_text("old")
            (rtl / "queue.sv").write_text("current")
            mapped = SourceMapper([rtl]).map(str(source))
            self.assertEqual(mapped["path"], str(rtl / "queue.sv"))
            self.assertEqual(mapped["method"], "relative_suffix")
            self.assertEqual(mapped["original_path"], str(source))
            self.assertEqual(SourceMapper().map(str(source))["path"], str(source))

    def test_missing_or_ambiguous_rtl_root_never_falls_back_to_build_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, rtl = root / "old-build", root / "rtl"
            old.mkdir(); rtl.mkdir()
            source = old / "queue.sv"
            source.write_text("old")
            self.assertEqual(SourceMapper([rtl]).map(str(source))["status"], "missing")
            for directory in (rtl / "a", rtl / "b"):
                directory.mkdir()
                (directory / "queue.sv").write_text("current")
            self.assertEqual(SourceMapper([rtl]).map(str(source))["status"], "ambiguous")

    def test_explicit_mapping_takes_priority_over_rtl_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, rtl, mapped = root / "old-build", root / "rtl", root / "mapped"
            for directory in (old, rtl, mapped):
                directory.mkdir()
                (directory / "queue.sv").write_text(str(directory))
            value = SourceMapper([rtl], [(str(old), str(mapped))]).map(str(old / "queue.sv"))
            self.assertEqual(value["path"], str(mapped / "queue.sv"))
            self.assertEqual(value["method"], "explicit_prefix_map")

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


class ConfigurationTests(unittest.TestCase):
    def test_batch_bounds_and_input_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vdb = root / "x.vdb"; (vdb / "snps/coverage/db").mkdir(parents=True)
            command = ["--vdb", str(vdb), "--out", str(root / "out")]
            for size in (0, -1, 501):
                with self.subTest(size=size), self.assertRaises(ToolError):
                    configuration(parser().parse_args(command + ["--batch-size", str(size)]))
            with self.assertRaises(ToolError):
                configuration(parser().parse_args(["--vdb", str(vdb), "--out", str(vdb / "out")]))
            # New scratch directories can be siblings of an input VDB under /tmp.
            self.assertEqual(configuration(parser().parse_args(command + ["--work-dir", str(root)]))["work_dir"], str(root))
            with self.assertRaises(ToolError):
                configuration(parser().parse_args(command + ["--work-dir", str(vdb / "scratch")]))

    def test_overwrite_does_not_replace_unowned_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vdb=root/"x.vdb";(vdb/"snps/coverage/db").mkdir(parents=True)
            out=root/"out";out.mkdir();(out/"precious").write_text("keep")
            args=parser().parse_args(["--vdb",str(vdb),"--out",str(out),"--overwrite"])
            with self.assertRaises(ToolError): configuration(args)
            self.assertEqual((out/"precious").read_text(),"keep")


if __name__ == "__main__": unittest.main()
