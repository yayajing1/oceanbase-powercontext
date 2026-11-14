"""The development loop: context -> MiniMax -> patch -> tests -> state.

The agent is the piece that makes the module useful for an assistant:

1. it builds the development context from the database,
2. it hands the context to MiniMax,
3. it stores the produced diff and (optionally) applies it,
4. it runs the target and the regression tests,
5. it writes the resulting task state into PowerContext so that a later
   run can continue from it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .config import Config
from .context import SYSTEM_PROMPT, DevelopmentContext, build_prompt, render_markdown
from .db import DocumentStore
from .minimax_client import MiniMaxClient
from .patcher import PatchError, apply_unified_diff, extract_diff
from .retrieve import Retriever
from .state import PowerContextStateStore

__all__ = ["AgentOptions", "SddAgent", "run_pytest"]


@dataclass
class AgentOptions:
    """The parameters of one agent run."""

    task_id: str
    task_text: str
    target_tests: Sequence[str] = ()
    regression_tests: Sequence[str] = ()
    apply_patch: bool = True
    run_tests: bool = True
    extra_instructions: str = ""
    records_dir: Path | None = None
    objective: str = ""


@dataclass
class TestResult:
    """The outcome of one pytest invocation."""

    name: str
    paths: list[str]
    exit_code: int
    passed: bool
    output: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "paths": self.paths,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "output_tail": self.output[-4000:],
        }


def run_pytest(project_root: Path, paths: Sequence[str], name: str) -> TestResult:
    """Run pytest on ``paths`` inside ``project_root``."""
    command = [sys.executable, "-m", "pytest", "--no-header", "-v", *paths]
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return TestResult(
        name=name,
        paths=list(paths),
        exit_code=int(completed.returncode),
        passed=completed.returncode == 0,
        output=output + f"\n[elapsed {time.time() - started:.1f}s]",
    )


class SddAgent:
    """Run one development task through the whole loop."""

    def __init__(self, config: Config, options: AgentOptions):
        self.config = config
        self.options = options
        self.store = DocumentStore(config)
        self.retriever = Retriever(self.store, config)
        self.state_store = PowerContextStateStore(config)

    # -- context ---------------------------------------------------------
    def build_context(self, task_text: str | None = None) -> DevelopmentContext:
        """Select the material that applies to the task."""
        text = task_text or self.options.task_text
        selection = self.retriever.select(text)
        state = {}
        try:
            state = self.state_store.load(self.options.task_id) or {}
        except Exception as exc:  # pragma: no cover - environment dependent
            state = {"_error": f"{type(exc).__name__}: {exc}"}
        return DevelopmentContext(
            task_id=self.options.task_id,
            task_text=text,
            specs=selection.specs,
            code=selection.code,
            tests=selection.tests,
            state=state,
            queries=selection.queries,
            symbols=selection.symbols,
        )

    # -- the loop --------------------------------------------------------
    def run(self) -> dict:
        """Run the whole loop and return a report."""
        started = time.time()
        context = self.build_context()
        records_dir = self.options.records_dir or (Path(self.config.project_root).parent / "records" / "minimax")
        records_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        prefix = f"{self.options.task_id}_{stamp}"

        client = MiniMaxClient(
            api_key=self.config.minimax_api_key,
            base_url=self.config.minimax_base_url,
            model=self.config.minimax_model,
            timeout=self.config.minimax_timeout,
            max_tokens=self.config.minimax_max_tokens,
            thinking=self.config.minimax_thinking,
        )
        prompt = build_prompt(
            context,
            self.options.extra_instructions,
            Path(self.config.project_root),
        )
        request = client.transcript(SYSTEM_PROMPT, prompt)

        response = client.complete(SYSTEM_PROMPT, prompt)
        attempts: list[dict] = [
            {"prompt": prompt, "response": response, "error": ""},
        ]
        diff = ""
        patch_error = ""
        for attempt in range(2):
            try:
                diff = extract_diff(response.text)
                patch_error = ""
                break
            except PatchError as exc:
                patch_error = str(exc)
                if attempt == 1:
                    break
                retry_prompt = (
                    prompt
                    + "\n\n注意：上一次回答中没有可用的 unified diff。"
                    + "请直接输出 unified diff（以 `--- a/` 和 `+++ b/` 开头），不要输出任何解释文字。"
                )
                response = client.complete(
                    SYSTEM_PROMPT, retry_prompt, max_tokens=client.max_tokens * 2
                )
                attempts.append({"prompt": retry_prompt, "response": response, "error": patch_error})

        answer_path = records_dir / f"{prefix}_answer.md"
        answer_parts = [
            "# MiniMax answer",
            "",
            f"- endpoint: {request['endpoint']}",
            f"- model: {response.model}",
            f"- request_id: {response.request_id}",
            f"- elapsed_ms: {response.elapsed_ms}",
            f"- usage: {json.dumps(response.usage, ensure_ascii=False)}",
            f"- attempts: {len(attempts)}",
            "",
        ]
        for index, attempt in enumerate(attempts, 1):
            answer_parts.append(f"## 尝试 {index} · Prompt")
            answer_parts.append("")
            answer_parts.append("```text")
            answer_parts.append(attempt["prompt"])
            answer_parts.append("```")
            answer_parts.append("")
            answer_parts.append(f"## 尝试 {index} · Answer")
            answer_parts.append("")
            answer_parts.append(attempt["response"].text or "（空）")
            answer_parts.append("")
        answer_path.write_text("\n".join(answer_parts), encoding="utf-8")

        report: dict = {
            "task_id": self.options.task_id,
            "context": context.as_dict(),
            "minimax": {
                "endpoint": request["endpoint"],
                "model": response.model,
                "request_id": response.request_id,
                "elapsed_ms": response.elapsed_ms,
                "usage": response.usage,
                "attempts": len(attempts),
                "prompt_path": str(answer_path),
            },
            "patch": None,
            "applied": False,
            "tests": [],
            "state": None,
            "elapsed_ms": int((time.time() - started) * 1000),
        }

        # --- the diff -----------------------------------------------------
        if not diff and patch_error:
            report["patch_error"] = patch_error

        if diff:
            patch_path = records_dir / f"{prefix}.patch"
            patch_path.write_text(diff + "\n", encoding="utf-8")
            report["patch"] = {"path": str(patch_path), "bytes": len(diff)}
            if self.options.apply_patch:
                try:
                    touched = apply_unified_diff(Path(self.config.project_root), diff)
                    report["applied"] = True
                    report["patch"]["files"] = touched
                except PatchError as exc:
                    report["patch_error"] = str(exc)

        # --- the tests ----------------------------------------------------
        verification: list[dict] = []
        project_root = Path(self.config.project_root)
        if self.options.run_tests:
            if self.options.target_tests:
                target = run_pytest(project_root, list(self.options.target_tests), "target")
                report["tests"].append(target.as_dict())
                verification.append(
                    {
                        "name": "target tests: " + ", ".join(self.options.target_tests),
                        "status": "passed" if target.passed else "failed",
                        "details": _pytest_summary(target.output),
                    }
                )
            if self.options.regression_tests:
                regression = run_pytest(project_root, list(self.options.regression_tests), "regression")
                report["tests"].append(regression.as_dict())
                verification.append(
                    {
                        "name": "regression tests: " + ", ".join(self.options.regression_tests),
                        "status": "passed" if regression.passed else "failed",
                        "details": _pytest_summary(regression.output),
                    }
                )

        # --- the state ----------------------------------------------------
        previous = context.state or {}
        done = list(previous.get("done", []) or [])
        todo = list(previous.get("todo", []) or [])
        if report["applied"]:
            done.append("MiniMax 生成的补丁已应用：" + ", ".join(report["patch"].get("files", [])))
        else:
            todo.append("应用 MiniMax 生成的补丁")
        for item in verification:
            if item["status"] == "passed":
                done.append(f"验证通过：{item['name']}")
            else:
                todo.append(f"验证未通过：{item['name']}")

        status = "completed" if verification and all(item["status"] == "passed" for item in verification) else "in_progress"
        state = {
            "task_id": self.options.task_id,
            "objective": self.options.objective or self.options.task_text[:500],
            "status": status,
            "summary": _summary(status, report),
            "done": done,
            "todo": todo,
            "verification": verification,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            saved = self.state_store.save(self.options.task_id, state)
            report["state"] = {
                "scope_id": saved.scope_id,
                "source_id": saved.source_id,
                "position": saved.position,
                **saved.detail,
            }
        except Exception as exc:  # pragma: no cover - environment dependent
            report["state"] = {"error": f"{type(exc).__name__}: {exc}"}

        report["task_state"] = state
        report["context_markdown"] = render_markdown(context)
        return report


_SUMMARY_RE = re.compile(r"(\d+ (?:passed|failed|error|skipped)[^\n]*)", re.IGNORECASE)


def _pytest_summary(output: str) -> str:
    """Extract the pytest result line, e.g. ``8 passed in 0.02s``."""
    matches = _SUMMARY_RE.findall(output)
    if matches:
        return matches[-1][:400]
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped and ("passed" in stripped or "failed" in stripped or "error" in stripped):
            return stripped[:400]
    return output.strip().splitlines()[-1][:400] if output.strip() else ""


def _summary(status: str, report: dict) -> str:
    applied = report.get("applied")
    tests = report.get("tests", [])
    passed = sum(1 for test in tests if test.get("passed"))
    return (
        f"patch_applied={applied}; tests_passed={passed}/{len(tests)}; status={status}"
    )
