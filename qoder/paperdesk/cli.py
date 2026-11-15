"""命令行入口：本轮提供 init-db 与 import。"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .config import Config
from .db import Database, SchemaReport
from .errors import ConfigError, DatabaseError, ImportError_, PaperDeskError
from .importer import Importer

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paperdesk",
        description="研读台 PaperDesk：把 Markdown 文献笔记导入 seekdb。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "数据库默认 127.0.0.1:2882，库名 paperdesk；\n"
            "用 PAPERDESK_DB_* 环境变量或 qoder/.env 覆盖。"
        ),
    )
    parser.add_argument("--version", action="version", version=f"paperdesk {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init = subparsers.add_parser(
        "init-db",
        help="初始化表与全文索引，探测中文解析器",
        description="创建论文、标签、作者、块、卡片等表，并在块字段上建立原生 FULLTEXT 索引。",
    )
    init.add_argument("--json", action="store_true", help="输出稳定字段的 JSON")
    init.set_defaults(handler=_cmd_init_db)

    imp = subparsers.add_parser(
        "import",
        help="导入 Markdown 笔记目录或文件",
        description="解析 front matter 与标题小节，按层级切块并幂等写入。目录默认清理已删除的文件。",
    )
    imp.add_argument("paths", nargs="+", metavar="PATH", help="笔记文件或目录（.md/.markdown/.txt）")
    imp.add_argument("--max-chars", type=int, default=None, help="单块字符上限，默认取 PAPERDESK_MAX_CHARS=800")
    imp.add_argument(
        "--prune",
        dest="prune",
        action="store_true",
        default=None,
        help="删除该目录下本次未出现的旧文献（目录导入默认开启）",
    )
    imp.add_argument(
        "--no-prune",
        dest="prune",
        action="store_false",
        help="保留目录中本次未出现的旧文献",
    )
    imp.add_argument("--json", action="store_true", help="输出稳定字段的 JSON")
    imp.set_defaults(handler=_cmd_import)

    return parser


def _config(args: argparse.Namespace) -> Config:
    config = Config.from_env()
    if getattr(args, "max_chars", None) is not None:
        config = config.with_overrides(max_chars=args.max_chars)
    return config


def _print_warnings(report: SchemaReport) -> None:
    for warning in report.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)


def _cmd_init_db(args: argparse.Namespace) -> int:
    config = _config(args)
    db = Database(config)
    report = db.init_schema()
    _print_warnings(report)
    indexes = db.index_listing()

    if args.json:
        payload = report.as_dict()
        payload["fulltext_index_columns"] = indexes
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"数据库：{report.database}（seekdb {report.server_version}）")
    if report.created_database:
        print("已创建数据库。")
    created = "、".join(report.created_tables) if report.created_tables else "无（已存在）"
    print(f"表：{len(report.tables)} 张；本次新建：{created}")
    print(f"  {', '.join(report.tables)}")
    if indexes:
        print("全文索引：")
        for item in indexes:
            columns = ", ".join(item["columns"])
            print(f"  {item['table']}.{item['index']} ({columns}) — 解析器 {report.fulltext_parser}")
    else:
        print("全文索引：无", file=sys.stderr)
    ngram = f"，ngram_token_size={report.ngram_token_size}" if report.ngram_token_size else ""
    print(f"中文全文解析器：{report.fulltext_parser}{ngram}")
    print("（中文可检索性由该解析器保证；若显示 default，请换用带 ik/beng/ngram 的 seekdb 构建。）")
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    config = _config(args)
    db = Database(config)
    schema = db.init_schema()
    _print_warnings(schema)

    importer = Importer(db, max_chars=config.max_chars)
    report = importer.import_paths(args.paths, prune=args.prune)

    if args.json:
        payload = {
            "database": config.db_name,
            "fulltext_parser": schema.fulltext_parser,
            "max_chars": config.max_chars,
            **report.as_dict(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"导入完成（库 {config.db_name}，解析器 {schema.fulltext_parser}）："
            f"文件 {report.files_seen} 个，新增 {report.files_added}，"
            f"更新 {report.files_updated}，跳过 {report.files_skipped}，失败 {report.files_failed}"
        )
        print(
            f"块：新增 {report.chunks_added}，更新 {report.chunks_updated}，"
            f"未变 {report.chunks_unchanged}，删除 {report.chunks_removed}；"
            f"prune 文档 {report.documents_pruned}"
        )
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)

    return 1 if report.files_failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    try:
        return int(args.handler(args))
    except (ConfigError, DatabaseError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except ImportError_ as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    except PaperDeskError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
