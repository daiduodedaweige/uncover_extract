#!/usr/bin/env python3
"""Standalone URG checker; also available as --validate-urg on the main CLI."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from uncover_tool.common import ToolError
from uncover_tool.urg import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ToolError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
