"""Assembling the development context of a task.

The context is the answer of the module: it points at the specification
entries that apply, at the code that has to change, at the tests that verify
the change and at the state that a previous run left behind.

The same context is rendered twice - once as JSON for machines and once as
Markdown for a human or a model prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .db import Hit

__all__ = ["DevelopmentContext", "render_markdown", "build_prompt"]


@dataclass
class DevelopmentContext:
    """Everything the assistant needs for one development task."""

    task_id: str
    task_text: str
    specs: list[Hit] = field(default_factory=list)
    code: list[Hit] = field(default_factory=list)
    tests: list[Hit] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    queries: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_text": self.task_text,
            "spec_entries": [hit.as_dict() for hit in self.specs],
            "code": [hit.as_dict() for hit in self.code],
            "tests": [hit.as_dict() for hit in self.tests],
            "task_state": self.state,
            "db_queries": self.queries,
            "symbols": self.symbols,
        }


def render_markdown(context: DevelopmentContext) -> str:
    """Render the context as Markdown."""
    lines: list[str] = []
    lines.append(f"# 开发上下文：{context.task_id}")
    lines.append("")
    lines.append("## 任务")
    lines.append("")
    lines.append(context.task_text.strip())
    lines.append("")

    lines.append("## 适用规格条目")
    lines.append("")
    if context.specs:
        for hit in context.specs:
            lines.append(f"- **{hit.locator}** (v{hit.meta.get('version', '?')}, {hit.source_path})")
            lines.append(f"  - {hit.body.strip()}")
    else:
        lines.append("- （无）")
    lines.append("")

    lines.append("## 需要修改的代码")
    lines.append("")
    if context.code:
        for hit in context.code:
            lines.append(f"- **{hit.locator}**")
    else:
        lines.append("- （无）")
    lines.append("")

    lines.append("## 验证用测试")
    lines.append("")
    if context.tests:
        for hit in context.tests:
            lines.append(f"- **{hit.locator}**")
    else:
        lines.append("- （无）")
    lines.append("")

    state = context.state or {}
    lines.append("## 任务状态（来自 PowerContext）")
    lines.append("")
    lines.append(f"- 状态：{state.get('status', 'unknown')}")
    lines.append(f"- 已完成：{_join(state.get('done'))}")
    lines.append(f"- 待办：{_join(state.get('todo'))}")
    verification = state.get("verification") or []
    if verification:
        lines.append("- 已有验证结果：")
        for item in verification:
            if isinstance(item, dict):
                lines.append(
                    f"  - {item.get('name', 'check')}: {item.get('status', '?')}"
                    + (f" ({item.get('details')})" if item.get("details") else "")
                )
            else:
                lines.append(f"  - {item}")
    else:
        lines.append("- 已有验证结果：（无）")
    lines.append("")
    return "\n".join(lines)


def _join(values: Any) -> str:
    if not values:
        return "（无）"
    if isinstance(values, str):
        return values
    return "；".join(str(value) for value in values)


SYSTEM_PROMPT = """\
你是资深 Python 工程师，正在按规格驱动开发（SDD）的方式完成一个局部代码修改。

规则：
1. 只能修改「需要修改的代码」中列出的文件与符号，不要新增文件，不要改动测试文件。
2. 实现必须严格满足「适用规格条目」中的每一条规则，同时不得破坏规格中声明的既有行为。
3. 输出必须是一个标准的 unified diff：以 `--- a/<相对路径>` / `+++ b/<相对路径>` 开头，hunk 头使用 `@@ -l,s +l,s @@` 格式。
4. diff 的上下文行必须与给出的文件内容逐字一致，行号必须对应该文件的真实行号；补丁会被直接应用到工作区。
5. diff 之外的任何内容都不要输出；不要使用 markdown 代码围栏以外的自然语言解释。
6. 只输出一个 diff，不要输出多个候选方案。
"""


def _group_by_file(hits: list[Hit]) -> list[tuple[str, list[str], dict[str, Hit]]]:
    """Group hits by their source file.

    Returns ``(source_path, [locators], {symbol: hit})`` per file, keeping
    the order in which the files were retrieved.
    """
    order: list[str] = []
    grouped: dict[str, dict[str, Hit]] = {}
    for hit in hits:
        if hit.source_path not in grouped:
            grouped[hit.source_path] = {}
            order.append(hit.source_path)
        grouped[hit.source_path][hit.symbol] = hit
    result = []
    for source_path in order:
        symbols = grouped[source_path]
        locators = [hit.locator for hit in symbols.values() if hit.symbol != "__file__"]
        result.append((source_path, locators, symbols))
    return result


def _file_body(source_path: str, symbols: dict[str, Hit], project_root: Path | None) -> str:
    """Return the full file content, preferring the file on disk."""
    if project_root is not None:
        candidate = Path(project_root) / source_path
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    file_hit = symbols.get("__file__")
    if file_hit is not None:
        return file_hit.body
    return "\n\n".join(hit.body for hit in symbols.values() if hit.symbol != "__file__")


def build_prompt(
    context: DevelopmentContext,
    extra_instructions: str = "",
    project_root: Path | None = None,
) -> str:
    """Build the user prompt that carries the context to the model."""
    sections: list[str] = []
    sections.append("## 任务")
    sections.append("")
    sections.append(context.task_text.strip())
    sections.append("")

    sections.append("## 适用规格条目")
    sections.append("")
    for hit in context.specs:
        sections.append(f"- {hit.locator}  [来源 {hit.source_path}, 版本 {hit.meta.get('version', '?')}]")
        sections.append(f"  {hit.body.strip()}")
    sections.append("")

    sections.append("## 需要修改的代码（下面是文件的完整内容，请据此生成 diff）")
    sections.append("")
    for source_path, locators, symbols in _group_by_file(context.code):
        sections.append(f"### {source_path}")
        sections.append(f"要修改的符号：{', '.join(locators) if locators else '（整文件）'}")
        sections.append("```python")
        sections.append(_file_body(source_path, symbols, project_root).rstrip())
        sections.append("```")
        sections.append("")

    sections.append("## 验证用测试（不得修改）")
    sections.append("")
    for source_path, locators, symbols in _group_by_file(context.tests):
        sections.append(f"### {source_path}")
        sections.append(f"涉及的用例：{', '.join(locators) if locators else '（整文件）'}")
        sections.append("```python")
        sections.append(_file_body(source_path, symbols, project_root).rstrip())
        sections.append("```")
        sections.append("")

    state = context.state or {}
    if state:
        sections.append("## 上一轮的任务状态")
        sections.append("")
        sections.append(f"- 状态：{state.get('status', 'unknown')}")
        sections.append(f"- 已完成：{_join(state.get('done'))}")
        sections.append(f"- 待办：{_join(state.get('todo'))}")
        sections.append("")

    if extra_instructions:
        sections.append("## 额外要求")
        sections.append("")
        sections.append(extra_instructions.strip())
        sections.append("")

    sections.append("请输出实现上述修改的 unified diff。")
    return "\n".join(sections)
