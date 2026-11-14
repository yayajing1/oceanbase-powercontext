"""Command line interface of the SDD context module.

::

    python -m sdd_context init
    python -m sdd_context import
    python -m sdd_context context --task-file validation/sample_task.json
    python -m sdd_context state show --task-id TASK-ORD-003
    python -m sdd_context run --task-file validation/sample_task.json
    python -m sdd_context reset --to before
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .config import MINIMAX_ROOT, load_config
from .context import render_markdown
from .db import DocumentStore
from .ingest import ingest_project
from .retrieve import Retriever
from .state import PowerContextStateStore

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdd-context",
        description="SDD context management module (MiniMax x OceanBase)",
    )
    parser.add_argument("--project-root", default=None, help="override SDD_PROJECT_ROOT")
    parser.add_argument("--json", action="store_true", help="print machine readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="check the database, PowerContext and the MiniMax key")
    subparsers.add_parser("init", help="create the schema, the table and the full text index")
    subparsers.add_parser("import", help="import the specifications, the code and the tests")
    subparsers.add_parser("stats", help="show what is stored in the database")

    context_parser = subparsers.add_parser("context", help="build the development context of a task")
    context_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    context_parser.add_argument("--task-id", default="")
    context_parser.add_argument("--task-file", default="")
    context_parser.add_argument("--task-text", default="")
    context_parser.add_argument("--limit", type=int, default=0)

    state_parser = subparsers.add_parser("state", help="read and write the task state")
    state_sub = state_parser.add_subparsers(dest="state_command", required=True)
    state_show = state_sub.add_parser("show", help="show the stored state of a task")
    state_show.add_argument("--task-id", required=True)
    state_list = state_sub.add_parser("list", help="list the stored tasks")
    state_save = state_sub.add_parser("save", help="write a state document")
    state_save.add_argument("--task-id", required=True)
    state_save.add_argument("--file", required=True, help="JSON file with the state document")
    state_sub.add_parser("reset", help="remove the local PowerContext storage (start from an empty state)")

    run_parser = subparsers.add_parser("run", help="run the full development loop")
    run_parser.add_argument("--task-file", required=True)
    run_parser.add_argument("--no-apply", action="store_true", help="do not apply the produced diff")
    run_parser.add_argument("--no-tests", action="store_true", help="do not run the tests")
    run_parser.add_argument("--records-dir", default="")

    reset_parser = subparsers.add_parser("reset", help="restore the demo project from a snapshot")
    reset_parser.add_argument("--to", choices=["before", "after"], required=True)

    return parser


def _load_task(config, args) -> dict:
    if getattr(args, "task_file", ""):
        path = Path(args.task_file)
        if not path.is_absolute():
            path = MINIMAX_ROOT / path
        return json.loads(path.read_text(encoding="utf-8"))
    task_text = getattr(args, "task_text", "") or ""
    return {
        "task_id": getattr(args, "task_id", "") or "TASK",
        "task_text": task_text,
        "objective": task_text,
        "target_tests": [],
        "regression_tests": [],
    }


def _print(payload, as_json: bool, markdown: str = "") -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    elif markdown:
        print(markdown)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _cmd_doctor(config) -> int:
    report: dict = {"config": config.redacted()}
    store = DocumentStore(config)
    try:
        report["database"] = {"ok": True, "version": store._server_version()}
    except Exception as exc:
        report["database"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        state_store = PowerContextStateStore(config)
        report["powercontext"] = {"ok": True, **state_store.describe()}
    except Exception as exc:
        report["powercontext"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    report["minimax"] = {
        "ok": bool(config.minimax_api_key),
        "base_url": config.minimax_base_url,
        "model": config.minimax_model,
        "hint": "set MINIMAX_API_KEY to call the model" if not config.minimax_api_key else "",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    ok = report["database"].get("ok") and report["powercontext"].get("ok")
    return 0 if ok else 1


def _cmd_init(config) -> int:
    store = DocumentStore(config)
    result = store.ensure_schema()
    _print(result, True)
    return 0


def _cmd_import(config) -> int:
    result = ingest_project(config)
    _print(result, True)
    return 0


def _cmd_stats(config) -> int:
    store = DocumentStore(config)
    stats = store.stats()
    stats["import_log"] = store.import_log(limit=10)
    _print(stats, True)
    return 0


def _cmd_context(config, args) -> int:
    task = _load_task(config, args)
    task_id = task.get("task_id") or "TASK"
    store = DocumentStore(config)
    retriever = Retriever(store, config)
    limit = args.limit or config.context_limit_per_kind
    selection = retriever.select(task.get("task_text", ""), limit_per_kind=limit)
    state = {}
    try:
        state = PowerContextStateStore(config).load(task_id) or {}
    except Exception as exc:
        state = {"_error": f"{type(exc).__name__}: {exc}"}
    payload = {
        "task_id": task_id,
        "task_text": task.get("task_text", ""),
        "spec_entries": [hit.as_dict() for hit in selection.specs],
        "code": [hit.as_dict() for hit in selection.code],
        "tests": [hit.as_dict() for hit in selection.tests],
        "task_state": state,
        "db_queries": selection.queries,
        "symbols": selection.symbols,
    }
    if args.json:
        _print(payload, True)
    else:
        from .context import DevelopmentContext

        context = DevelopmentContext(
            task_id=task_id,
            task_text=task.get("task_text", ""),
            specs=selection.specs,
            code=selection.code,
            tests=selection.tests,
            state=state,
            queries=selection.queries,
            symbols=selection.symbols,
        )
        print(render_markdown(context))
    return 0


def _cmd_state(config, args) -> int:
    state_store = PowerContextStateStore(config)
    if args.state_command == "show":
        state = state_store.load(args.task_id)
        _print(state if state is not None else {"task_id": args.task_id, "found": False}, True)
        return 0 if state is not None else 1
    if args.state_command == "list":
        _print({"tasks": state_store.list_tasks()}, True)
        return 0
    if args.state_command == "save":
        path = Path(args.file)
        if not path.is_absolute():
            path = MINIMAX_ROOT / path
        document = json.loads(path.read_text(encoding="utf-8"))
        result = state_store.save(args.task_id, document)
        _print(
            {
                "task_id": args.task_id,
                "scope_id": result.scope_id,
                "source_id": result.source_id,
                "position": result.position,
                **result.detail,
            },
            True,
        )
        return 0
    if args.state_command == "reset":
        _print(state_store.reset(), True)
        return 0
    return 2


def _cmd_run(config, args) -> int:
    from .agent import AgentOptions, SddAgent

    task = _load_task(config, args)
    records_dir = Path(args.records_dir) if args.records_dir else None
    if records_dir and not records_dir.is_absolute():
        records_dir = MINIMAX_ROOT / records_dir
    options = AgentOptions(
        task_id=task.get("task_id", "TASK"),
        task_text=task.get("task_text", ""),
        target_tests=task.get("target_tests", []),
        regression_tests=task.get("regression_tests", []),
        apply_patch=not args.no_apply,
        run_tests=not args.no_tests,
        extra_instructions=task.get("extra_instructions", ""),
        records_dir=records_dir,
        objective=task.get("objective", ""),
    )
    report = SddAgent(config, options).run()
    _print(report, True)
    failed = [test for test in report.get("tests", []) if not test.get("passed")]
    return 1 if failed else 0


def _cmd_reset(config, args) -> int:
    snapshot = MINIMAX_ROOT / "code" / args.to
    if not snapshot.exists():
        print(f"the snapshot does not exist: {snapshot}", file=sys.stderr)
        return 2
    project_root = Path(config.project_root).resolve()
    copied = []
    for source in sorted(snapshot.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(snapshot)
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append(relative.as_posix())
    print(json.dumps({"restored_from": str(snapshot), "files": copied}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    overrides = {"project_root": Path(args.project_root)} if args.project_root else {}
    config = load_config(**overrides)

    if args.command == "doctor":
        return _cmd_doctor(config)
    if args.command == "init":
        return _cmd_init(config)
    if args.command == "import":
        return _cmd_import(config)
    if args.command == "stats":
        return _cmd_stats(config)
    if args.command == "context":
        return _cmd_context(config, args)
    if args.command == "state":
        return _cmd_state(config, args)
    if args.command == "run":
        return _cmd_run(config, args)
    if args.command == "reset":
        return _cmd_reset(config, args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
