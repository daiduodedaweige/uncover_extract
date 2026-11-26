#!/usr/bin/env bash
# Stop this results/ directory's extraction tasks, then remove their outputs.
set -euo pipefail
tool_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

exec python3 - "$tool_dir" "$@" <<'PY'
import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

parser = argparse.ArgumentParser(
    prog="clean.sh",
    description="终止使用本工具 results/ 的提取任务及子进程，再清空 results/（含隐藏残留）。保留程序、validation/ 和安装包。",
)
parser.add_argument("--dry-run", action="store_true", help="只显示将终止的进程和清理范围，不发信号、不删除")
args = parser.parse_args(sys.argv[2:])
root = Path(sys.argv[1])
target = root / "results"


def fail(message):
    print("清理失败：" + message, file=sys.stderr)
    raise SystemExit(1)


def inside(path):
    return path == target or target in path.parents


def option(tokens, name):
    value = None
    for index, token in enumerate(tokens):
        if token == name and index + 1 < len(tokens):
            value = tokens[index + 1]
        if token.startswith(name + "="):
            value = token.split("=", 1)[1]
    return value


def active_users():
    # Match exact output/workspace paths, then include their subprocess trees.
    # Never kill by a broad command substring or a shared shell process group.
    result = subprocess.run(
        ["ps", "-ww", "-u", str(os.getuid()), "-o", "pid=,ppid=,stat=,args="],
        text=True, capture_output=True, check=True,
    )
    table, selected = {}, set()
    for line in result.stdout.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4:
            continue
        pid, ppid, status, command = fields
        if status.startswith("Z"):
            continue
        pid = int(pid)
        table[pid] = {"pid": pid, "ppid": int(ppid), "status": status,
                      "command": command, "frontend": False}
    for pid, task in table.items():
        command = task["command"]
        if not any(name in command for name in (
            "uncover_extract.py", "uncover-extract", "uncover_extract.worker",
            "uncover_extract.cli", "uncover_extract.urg"
        )):
            continue
        # ps does not quote arguments with spaces; /proc preserves NUL boundaries.
        try:
            tokens = [os.fsdecode(token) for token in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if token]
        except FileNotFoundError:
            # A restricted PID namespace can expose host ps output but hide /proc.
            # Do not mistake that for a successfully stopped task.
            state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                   text=True, capture_output=True, check=False).stdout.strip()
            if state and not state.startswith("Z"):
                fail(f"当前环境无法读取 /proc/{pid}；请在能查看该进程的终端运行 clean.sh。")
            continue
        except PermissionError:
            fail(f"无法读取可能相关的进程 {pid}；请先退出该进程。")
        if not tokens:
            continue
        if "-m" in tokens:
            index = tokens.index("-m")
            module = tokens[index + 1] if index + 1 < len(tokens) else ""
        else:
            index, module = -1, ""
        native = module == "uncover_extract.worker"
        urg = module == "uncover_extract.urg"
        frontend = module == "uncover_extract.cli" or any(
            Path(token).name in ("uncover_extract.py", "uncover-extract")
            for token in tokens[:3]
        )
        if not native and not frontend and not urg:
            continue
        task["frontend"] = frontend
        paths = tokens[index + 2:index + 4] if native else [
            option(tokens, "--out"), option(tokens, "--result" if urg else "--work-dir")
        ]
        for value in paths:
            if value is None:
                continue
            path = Path(value)
            if not path.is_absolute():
                try:
                    path = Path(os.readlink(f"/proc/{pid}/cwd")) / path
                except FileNotFoundError:
                    # The task may have exited since the ps snapshot.
                    if not Path(f"/proc/{pid}").exists():
                        continue
                    fail(f"无法确定提取进程 {pid} 的工作目录；请先退出该进程。")
                except PermissionError:
                    fail(f"无法查看提取进程 {pid} 的工作目录；请先退出该进程。")
            if inside(path.resolve()):
                selected.add(pid)
                break
    while True:
        children = {pid for pid, task in table.items() if task["ppid"] in selected} - selected
        if not children:
            break
        selected.update(children)
    active = []
    for pid in sorted(selected):
        task = table[pid]
        identity = process_identity(pid)
        if identity is not None:
            task["started"] = identity[1]
            active.append(task)
    return active


def process_identity(pid):
    try:
        # comm can contain spaces and parentheses; fields start after its final ).
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return None
    if fields[0] == "Z":
        return None
    return fields[0], fields[19]  # state, starttime (protect against PID reuse)


def send(task, number):
    identity = process_identity(task["pid"])
    if identity is None or identity[1] != task["started"]:
        return
    try:
        os.kill(task["pid"], number)
        # Stopped tasks must run to deliver pending termination signals.
        if number in (signal.SIGTERM, signal.SIGINT) and identity[0] in ("T", "t"):
            os.kill(task["pid"], signal.SIGCONT)
    except ProcessLookupError:
        pass


def wait_exited(tasks, seconds):
    deadline = time.monotonic() + seconds
    while True:
        tasks = [task for task in tasks
                 if (identity := process_identity(task["pid"])) is not None
                 and identity[1] == task["started"]]
        if not tasks or time.monotonic() >= deadline:
            return
        time.sleep(0.1)


def remaining_tasks(tasks):
    # Retain identities of children reparented after their parent exits.
    active = {(task["pid"], task["started"]): task for task in active_users()}
    for task in tasks:
        identity = process_identity(task["pid"])
        if identity is not None and identity[1] == task["started"]:
            active.setdefault((task["pid"], task["started"]), task)
    return list(active.values())


def stop_tasks(tasks):
    print("正在终止使用 results/ 的提取任务及其子进程……", flush=True)
    # Stop workers first; resume paused frontends so they can reap their children.
    for task in tasks:
        if not task["frontend"]:
            send(task, signal.SIGTERM)
    for task in tasks:
        if task["frontend"]:
            send(task, signal.SIGCONT)
    wait_exited(tasks, 3)
    remaining = remaining_tasks(tasks)
    if remaining:
        for task in remaining:
            send(task, signal.SIGTERM)
        wait_exited(remaining, 3)
    remaining = remaining_tasks(remaining)
    if remaining:
        print("部分进程未退出，正在强制终止（SIGKILL）……", flush=True)
        for task in remaining:
            send(task, signal.SIGKILL)
        wait_exited(remaining, 3)
    remaining = remaining_tasks(remaining)
    if remaining:
        fail("进程仍未退出，未删除 results/：" + ", ".join(str(task["pid"]) for task in remaining))
    print("相关提取进程已退出。", flush=True)


try:
    if target.is_symlink():
        fail(f"{target} 是符号链接，拒绝清理链接指向的目录。")
    if target.exists() and not target.is_dir():
        fail(f"{target} 不是目录。")
    active = active_users()
    if active:
        for task in active:
            print(f"{'将终止' if args.dry_run else '发现'}提取进程：PID {task['pid']} [{task['status']}] {task['command']}", flush=True)
        if not args.dry_run:
            stop_tasks(active)
    if not target.exists():
        print(f"无需删除：{target} 不存在。")
        raise SystemExit(0)
    file_count, allocated = 0, 0
    for parent, dirs, files in os.walk(target, followlinks=False):
        for name in files + dirs:
            path = Path(parent) / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue  # Live tasks can remove files during --dry-run.
            allocated += info.st_blocks * 512
            if path.is_symlink() or not path.is_dir():
                file_count += 1
    print(f"清理范围：{target}\n文件/链接：{file_count}，当前磁盘占用约 {allocated / 1024**3:.2f} GiB。")
    if args.dry_run:
        print("预览完成，未终止进程、未删除任何内容。")
    else:
        shutil.rmtree(target)
        print("已删除 results/ 及其全部内容；下次提取会自动创建输出目录。")
except (OSError, subprocess.SubprocessError) as exc:
    fail(str(exc))
PY
