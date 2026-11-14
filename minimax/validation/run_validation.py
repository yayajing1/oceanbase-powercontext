"""End to end validation of the SDD context module.

The runner executes every step as a **separate process**, so that the
"restart the module and read the stored state back" step is a real restart.

Every step records the command, the working directory, the exit code, the
expected result and the code version (the git commit of the repository).
The result is written to ``records/logs/validation-<timestamp>.json`` and a
human readable summary is printed.

Usage::

    python validation/run_validation.py
    python validation/run_validation.py --skip-agent   # reuse the recorded patch
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MINIMAX_ROOT.parent
TASK_FILE = MINIMAX_ROOT / "validation" / "sample_task.json"
LOG_DIR = MINIMAX_ROOT / "records" / "logs"


class Step:
    """One executed step of the validation run."""

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.command: list[str] = []
        self.cwd: str = ""
        self.exit_code: int | None = None
        self.expected = ""
        self.passed = False
        self.detail = ""
        self.stdout = ""
        self.stderr = ""
        self.started = ""
        self.finished = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "command": " ".join(self.command),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "expected": self.expected,
            "passed": self.passed,
            "detail": self.detail,
            "started_at": self.started,
            "finished_at": self.finished,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-2000:],
        }


def _git_version() -> str:
    """Return the commit the run belongs to.

    The git executable is used when it is available; otherwise the commit is
    resolved by reading the repository metadata directly, so that the log
    always carries the code version.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    except Exception:
        pass
    return _git_version_without_binary()


def _git_version_without_binary() -> str:
    git_path = REPO_ROOT / ".git"
    try:
        if git_path.is_file():
            pointer = git_path.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                git_dir = Path(pointer.split(":", 1)[1].strip())
                if not git_dir.is_absolute():
                    git_dir = (REPO_ROOT / git_dir).resolve()
            else:
                return "unknown"
        elif git_path.is_dir():
            git_dir = git_path
        else:
            return "unknown"

        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return "unknown"
        head = head_file.read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            common = git_dir
            common_file = git_dir / "commondir"
            if common_file.is_file():
                relative = common_file.read_text(encoding="utf-8").strip()
                common = (git_dir / relative).resolve()
            ref_file = common / ref
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()
            packed = common / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
            return "unknown"
        return head
    except Exception:
        return "unknown"


def run_step(
    name: str,
    description: str,
    command: list[str],
    cwd: Path,
    env: dict,
    expected: str,
    check=None,
) -> Step:
    """Execute one command and evaluate it."""
    step = Step(name, description)
    step.command = command
    step.cwd = str(cwd)
    step.expected = expected
    step.started = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    step.exit_code = int(completed.returncode)
    step.stdout = completed.stdout or ""
    step.stderr = completed.stderr or ""
    step.finished = datetime.now(timezone.utc).isoformat()
    if check is not None:
        ok, detail = check(step)
        step.passed = ok
        step.detail = detail
    else:
        step.passed = step.exit_code == 0
        step.detail = f"exit_code={step.exit_code}"
    return step


def check_exit(expected_code: int = 0):
    def _check(step: Step) -> tuple[bool, str]:
        ok = step.exit_code == expected_code
        return ok, f"exit_code={step.exit_code} (expected {expected_code})"

    return _check


def check_output(predicate, description: str):
    def _check(step: Step) -> tuple[bool, str]:
        ok = predicate(step.stdout, step.stderr)
        return ok, description

    return _check


def _pytest_command(paths: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", "--no-header", "-v", *paths]


def _context_sources(stdout: str) -> dict:
    """Parse the JSON output of ``sdd_context context --json``."""
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(stdout[start:end + 1])
    except ValueError:
        return {}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="run the SDD context validation")
    parser.add_argument("--skip-agent", action="store_true", help="do not call MiniMax again")
    args = parser.parse_args(argv)

    task = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    expected = task.get("expected", {})

    env = dict(os.environ)
    env["PYTHONPATH"] = str(MINIMAX_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    version = _git_version()
    steps: list[Step] = []
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"validation-{stamp}.json"

    def cli(*extra: str) -> list[str]:
        return [sys.executable, "-m", "sdd_context", *extra]

    # ---------------------------------------------------------------- 1
    steps.append(
        run_step(
            "01-doctor",
            "检查数据库、PowerContext 与 MiniMax 配置",
            cli("doctor"),
            MINIMAX_ROOT,
            env,
            "数据库可达且 PowerContext 可用",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 2
    steps.append(
        run_step(
            "02-reset-before",
            "把业务工程恢复到修改前的快照",
            cli("reset", "--to", "before"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 3
    steps.append(
        run_step(
            "03-target-tests-before",
            "修改前运行目标测试：必须因为目标行为未实现而失败",
            _pytest_command(task["target_tests"]),
            MINIMAX_ROOT / "demo_project",
            env,
            "exit_code!=0 且失败原因是功能未实现",
            check_output(
                lambda out, err: "unexpected keyword argument 'min_amount'" in (out + err),
                "目标测试因 filter_orders 不支持 min_amount 而失败",
            ),
        )
    )

    # ---------------------------------------------------------------- 4
    steps.append(
        run_step(
            "04-regression-tests-before",
            "修改前运行回归测试：必须通过",
            _pytest_command(task["regression_tests"]),
            MINIMAX_ROOT / "demo_project",
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 5
    steps.append(
        run_step(
            "05-state-reset",
            "清空任务状态，从空状态开始",
            cli("state", "reset"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 6
    steps.append(
        run_step(
            "06-db-init",
            "在 seekdb / OceanBase 中建表并创建全文索引",
            cli("init"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 7
    steps.append(
        run_step(
            "07-db-import",
            "把规格、代码与测试导入数据库",
            cli("import"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 8
    def _check_context(step: Step) -> tuple[bool, str]:
        payload = _context_sources(step.stdout)
        if not payload:
            return False, "上下文的 JSON 输出无法解析"
        spec_locators = {item["locator"] for item in payload.get("spec_entries", [])}
        code_locators = {item["locator"] for item in payload.get("code", [])}
        test_locators = {item["locator"] for item in payload.get("tests", [])}
        missing_spec = sorted(set(expected.get("spec_entries", [])) - spec_locators)
        missing_code = sorted(set(expected.get("code", [])) - code_locators)
        missing_test = sorted(set(expected.get("tests", [])) - test_locators)
        queries = payload.get("db_queries", [])
        ok = not missing_spec and not missing_code and not missing_test and bool(queries)
        detail = (
            f"spec={len(spec_locators)} code={len(code_locators)} tests={len(test_locators)} "
            f"db_queries={len(queries)}"
        )
        if missing_spec:
            detail += f" 缺少规格条目: {missing_spec}"
        if missing_code:
            detail += f" 缺少代码: {missing_code}"
        if missing_test:
            detail += f" 缺少测试: {missing_test}"
        return ok, detail

    steps.append(
        run_step(
            "08-context",
            "构建开发上下文：必须包含样例规定的规格、代码与测试来源",
            cli("context", "--task-file", str(TASK_FILE), "--json"),
            MINIMAX_ROOT,
            env,
            "上下文包含样例规定的全部来源",
            _check_context,
        )
    )

    # ---------------------------------------------------------------- 9
    if not args.skip_agent:
        steps.append(
            run_step(
                "09-minimax-change",
                "把上下文交给 MiniMax，由 MiniMax 生成并应用代码修改",
                cli("run", "--task-file", str(TASK_FILE)),
                MINIMAX_ROOT,
                env,
                "补丁被应用且目标测试通过",
                check_output(
                    lambda out, err: '"applied": true' in out,
                    "MiniMax 的补丁已应用到业务工程",
                ),
            )
        )

    # ---------------------------------------------------------------- 10
    steps.append(
        run_step(
            "10-target-tests-after",
            "修改后运行目标测试：必须全部通过",
            _pytest_command(task["target_tests"]),
            MINIMAX_ROOT / "demo_project",
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # ---------------------------------------------------------------- 11
    steps.append(
        run_step(
            "11-regression-tests-after",
            "修改后运行回归测试：必须全部通过",
            _pytest_command(task["regression_tests"]),
            MINIMAX_ROOT / "demo_project",
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    # --------------------------------------------------------------- 12
    def _check_state(step: Step) -> tuple[bool, str]:
        payload = _context_sources(step.stdout)
        state = payload.get("task_state") or {}
        done = state.get("done") or []
        todo = state.get("todo") or []
        verification = state.get("verification") or []
        ok = bool(done) and bool(verification) and not state.get("_error")
        return ok, f"done={len(done)} todo={len(todo)} verification={len(verification)}"

    steps.append(
        run_step(
            "12-state-reuse",
            "重启模块（新进程）后读取并使用保存的任务状态",
            cli("context", "--task-file", str(TASK_FILE), "--json"),
            MINIMAX_ROOT,
            env,
            "上下文中带有来自 PowerContext 的已完成事项与验证结果",
            _check_state,
        )
    )

    # --------------------------------------------------------------- 13
    spec_update = MINIMAX_ROOT / "validation" / "apply_spec_update.py"
    steps.append(
        run_step(
            "13-spec-update",
            "更新样例中的规格",
            [sys.executable, str(spec_update), "--apply"],
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    steps.append(
        run_step(
            "14-db-import-after-spec-update",
            "重新导入更新后的规格",
            cli("import"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    steps.append(
        run_step(
            "15-context-after-spec-update",
            "核对更新后的上下文确实使用了新规格",
            cli("context", "--task-file", str(TASK_FILE), "--json"),
            MINIMAX_ROOT,
            env,
            "上下文包含 SPEC-ORD-003 的更新版本与新增规则 R8",
            check_output(
                lambda out, err: "SPEC-ORD-003.R8" in out and '"version": 2' in out,
                "上下文使用了重新导入后的规格版本",
            ),
        )
    )

    steps.append(
        run_step(
            "16-spec-revert",
            "还原规格",
            [sys.executable, str(spec_update), "--revert"],
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    steps.append(
        run_step(
            "17-db-import-after-revert",
            "重新导入还原后的规格",
            cli("import"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    steps.append(
        run_step(
            "18-context-after-revert",
            "核对上下文已经不再返回固定答案",
            cli("context", "--task-file", str(TASK_FILE), "--json"),
            MINIMAX_ROOT,
            env,
            "上下文中不再包含新增规则 R8",
            check_output(
                lambda out, err: "SPEC-ORD-003.R8" not in out,
                "上下文跟随规格变化，而不是返回固定答案",
            ),
        )
    )

    # --------------------------------------------------------------- 19
    steps.append(
        run_step(
            "19-reset-after",
            "把业务工程恢复到修改后的快照",
            cli("reset", "--to", "after"),
            MINIMAX_ROOT,
            env,
            "exit_code=0",
            check_exit(0),
        )
    )

    passed = sum(1 for step in steps if step.passed)
    report = {
        "started_at": steps[0].started if steps else "",
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "code_version": version,
        "python": sys.version,
        "platform": sys.platform,
        "task_file": str(TASK_FILE),
        "steps": [step.as_dict() for step in steps],
        "passed": passed,
        "total": len(steps),
        "all_passed": passed == len(steps),
    }
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    width = max(len(step.name) for step in steps)
    print(f"code version : {version}")
    print(f"log          : {log_path}")
    print("-" * (width + 30))
    for step in steps:
        mark = "PASS" if step.passed else "FAIL"
        print(f"[{mark}] {step.name.ljust(width)}  {step.detail}")
    print("-" * (width + 30))
    print(f"{passed}/{len(steps)} steps passed")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
