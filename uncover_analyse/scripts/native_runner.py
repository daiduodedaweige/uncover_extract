"""Run proprietary APIs out of process; retain failures and logs in the point directory."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys

from runtime import locked, read_json, require, write_json


def run_worker(point, operation, options, seconds):
    with locked(point) as (directory, manifest):
        require(manifest["status"] == "in_progress", "请先 begin 新运行")
        name = datetime.now(timezone.utc).strftime("query-%Y%m%dT%H%M%S%fZ")
        work = directory / "logs" / name
        work.mkdir(parents=True)
        request, response = work / "request.json", work / "response.json"
        write_json(request, {"point": point, "operation": operation, "options": options})
    worker = Path(__file__).resolve().with_name("native_worker.py")
    log_path = work / "backend.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(worker), str(request), str(response)], cwd=work,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return {"status": "unsupported_or_failed", "error": "后端查询达到时间预算", "log": str(log_path), "seconds": seconds}
    if not response.exists():
        return {"status": "unsupported_or_failed", "returncode": process.returncode, "log": str(log_path),
                "error": "后端未返回结构化结果；检查许可证、共享库或原生崩溃日志"}
    return {**read_json(response), "log": str(log_path), "returncode": process.returncode}
