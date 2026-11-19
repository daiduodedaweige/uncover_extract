"""Resolve source paths without silently picking a same-name file."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .common import digest_file


class SourceMapper:
    def __init__(self, roots=(), path_maps=()):
        self.roots = [Path(p).resolve() for p in roots]
        self.maps = sorted([(str(Path(a)), Path(b).resolve()) for a, b in path_maps],
                           key=lambda pair: -len(pair[0]))
        self.cache = {}
        self.by_name = defaultdict(set)
        for root in self.roots:
            for path in root.rglob("*"):
                if path.is_file():
                    self.by_name[path.name].add(path.resolve())

    def map(self, source):
        if source in self.cache:
            return self.cache[source]
        result = {"original_path": source, "path": None, "status": "missing",
                  "method": None, "sha256": None, "correspondence": "unconfirmed", "candidates": []}
        if not source:
            result["status"] = "no_source_location"
            self.cache[source] = result
            return result
        original = Path(source)
        candidates, method = set(), None
        # Explicit maps take priority; equally specific conflicting maps are ambiguous.
        matches = [(a, b) for a, b in self.maps if source == a or source.startswith(a.rstrip("/") + "/")]
        if matches:
            longest = len(matches[0][0])
            for old, new in matches:
                if len(old) == longest:
                    path = new / source[len(old):].lstrip("/")
                    if path.is_file():
                        candidates.add(path.resolve())
            method = "explicit_prefix_map"
        else:
            if original.is_absolute() and original.is_file():
                candidates.add(original.resolve())
                method = "original_path"
            if not candidates:
                # Prefer the longest matching relative suffix under configured roots.
                parts = [part for part in original.parts if part not in ("/", ".", "..")]
                for length in range(len(parts), 0, -1):
                    for root in self.roots:
                        path = root.joinpath(*parts[-length:])
                        if path.is_file():
                            candidates.add(path.resolve())
                    if candidates:
                        method = "relative_suffix"
                        break
            if not candidates:
                candidates = self.by_name.get(original.name, set()).copy()
                method = "unique_basename_candidate"
        result["candidates"] = [str(p) for p in sorted(candidates)]
        result["method"] = method
        if len(candidates) == 1:
            path = next(iter(candidates))
            result.update(path=str(path), status="resolved", sha256=digest_file(path))
        elif len(candidates) > 1:
            result["status"] = "ambiguous"
        self.cache[source] = result
        return result

    def report(self):
        return [self.cache[key] for key in sorted(self.cache, key=lambda x: x or "")]
