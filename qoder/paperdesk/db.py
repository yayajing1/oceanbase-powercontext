"""seekdb / OceanBase（MySQL 协议）连接、建表与全文索引。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Sequence

import pymysql

from .config import Config
from .errors import DatabaseError, DatabaseUnavailable

__all__ = ["Database", "SchemaReport", "SCHEMA_VERSION", "FULLTEXT_INDEXES"]

SCHEMA_VERSION = 1

FULLTEXT_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("chunks", "ft_chunks_content", "content"),
    ("chunks", "ft_chunks_search", "search_text"),
    ("study_cards", "ft_cards_search", "card_search_text"),
)

PARSER_CANDIDATES: tuple[str | None, ...] = ("ik", "beng", "ngram", None)

_TABLES: dict[str, str] = {
    "schema_meta": """
        CREATE TABLE IF NOT EXISTS schema_meta (
          meta_key   VARCHAR(64)  NOT NULL,
          meta_value VARCHAR(512) NOT NULL,
          updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (meta_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "documents": """
        CREATE TABLE IF NOT EXISTS documents (
          document_id   CHAR(32)      NOT NULL,
          path_hash     CHAR(64)      NOT NULL,
          source_path   VARCHAR(1024) NOT NULL,
          rel_path      VARCHAR(512)  NOT NULL,
          import_root   VARCHAR(1024) NOT NULL,
          title         VARCHAR(512)  NOT NULL,
          authors_text  VARCHAR(512)  NOT NULL DEFAULT '',
          tags_text     VARCHAR(512)  NOT NULL DEFAULT '',
          pub_year      SMALLINT      NULL,
          content_hash  CHAR(64)      NOT NULL,
          file_size     INT           NOT NULL DEFAULT 0,
          mtime         DATETIME      NULL,
          imported_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (document_id),
          UNIQUE KEY uk_path_hash (path_hash),
          KEY idx_pub_year (pub_year),
          KEY idx_import_root (import_root(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "document_tags": """
        CREATE TABLE IF NOT EXISTS document_tags (
          document_id CHAR(32)     NOT NULL,
          tag         VARCHAR(128) NOT NULL,
          PRIMARY KEY (document_id, tag),
          KEY idx_tag_doc (tag, document_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "document_authors": """
        CREATE TABLE IF NOT EXISTS document_authors (
          document_id CHAR(32)     NOT NULL,
          author      VARCHAR(128) NOT NULL,
          PRIMARY KEY (document_id, author),
          KEY idx_author_doc (author, document_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "chunks": """
        CREATE TABLE IF NOT EXISTS chunks (
          chunk_id      CHAR(32)  NOT NULL,
          document_id   CHAR(32)  NOT NULL,
          ordinal       INT       NOT NULL,
          occurrence    INT       NOT NULL,
          heading_path  VARCHAR(512) NOT NULL DEFAULT '',
          content       TEXT      NOT NULL,
          search_text   TEXT      NOT NULL,
          content_hash  CHAR(64)  NOT NULL,
          start_line    INT       NOT NULL,
          end_line      INT       NOT NULL,
          start_offset  INT       NOT NULL,
          end_offset    INT       NOT NULL,
          char_count    INT       NOT NULL,
          created_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (chunk_id),
          UNIQUE KEY uk_doc_ordinal (document_id, ordinal),
          KEY idx_doc (document_id),
          KEY idx_doc_occurrence (document_id, content_hash, occurrence)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "study_cards": """
        CREATE TABLE IF NOT EXISTS study_cards (
          card_id          CHAR(32)  NOT NULL,
          source_chunk_id  CHAR(32)  NULL,
          problem          TEXT      NOT NULL,
          method           TEXT      NOT NULL,
          conclusion       TEXT      NOT NULL,
          relation         TEXT      NOT NULL,
          card_search_text TEXT      NOT NULL,
          source_title        VARCHAR(512)  NOT NULL DEFAULT '',
          source_path         VARCHAR(1024) NOT NULL DEFAULT '',
          source_start_line   INT NULL,
          source_end_line     INT NULL,
          source_year         SMALLINT NULL,
          source_tags_text    VARCHAR(512)  NOT NULL DEFAULT '',
          source_authors_text VARCHAR(512)  NOT NULL DEFAULT '',
          source_chunk_hash   CHAR(64) NULL,
          source_snapshot     TEXT NULL,
          in_queue        TINYINT  NOT NULL DEFAULT 0,
          mastery         DECIMAL(3,1) NULL,
          last_score      TINYINT  NULL,
          review_count    INT      NOT NULL DEFAULT 0,
          interval_days   INT      NOT NULL DEFAULT 0,
          ease_factor     DECIMAL(4,2) NOT NULL DEFAULT 2.50,
          next_review_at  DATETIME NULL,
          last_reviewed_at DATETIME NULL,
          created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                                   ON UPDATE CURRENT_TIMESTAMP,
          PRIMARY KEY (card_id),
          KEY idx_source_chunk (source_chunk_id),
          KEY idx_due (in_queue, next_review_at),
          KEY idx_source_year (source_year)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "card_tags": """
        CREATE TABLE IF NOT EXISTS card_tags (
          card_id CHAR(32)     NOT NULL,
          tag     VARCHAR(128) NOT NULL,
          PRIMARY KEY (card_id, tag),
          KEY idx_card_tag (tag, card_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "card_authors": """
        CREATE TABLE IF NOT EXISTS card_authors (
          card_id CHAR(32)     NOT NULL,
          author  VARCHAR(128) NOT NULL,
          PRIMARY KEY (card_id, author),
          KEY idx_card_author (author, card_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
    "review_events": """
        CREATE TABLE IF NOT EXISTS review_events (
          event_id       BIGINT    NOT NULL AUTO_INCREMENT,
          card_id        CHAR(32)  NOT NULL,
          score          TINYINT   NOT NULL,
          reviewed_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
          next_review_at DATETIME  NOT NULL,
          interval_days  INT       NOT NULL,
          note           VARCHAR(512) NOT NULL DEFAULT '',
          PRIMARY KEY (event_id),
          KEY idx_card_reviewed (card_id, reviewed_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
    """,
}


@dataclass
class SchemaReport:
    database: str
    server_version: str
    created_database: bool = False
    created_tables: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    fulltext_indexes: dict[str, str] = field(default_factory=dict)
    fulltext_parser: str = "unknown"
    ngram_token_size: int | None = None
    index_created: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "database": self.database,
            "server_version": self.server_version,
            "created_database": self.created_database,
            "created_tables": list(self.created_tables),
            "tables": list(self.tables),
            "fulltext_indexes": dict(self.fulltext_indexes),
            "fulltext_parser": self.fulltext_parser,
            "ngram_token_size": self.ngram_token_size,
            "index_created": self.index_created,
            "warnings": list(self.warnings),
        }


class Database:
    """一个显式的 seekdb 客户端；不引入 ORM。"""

    def __init__(self, config: Config):
        self.config = config

    # -- 连接 -----------------------------------------------------------
    def _connect(self, database: str | None):
        cfg = self.config
        try:
            return pymysql.connect(
                host=cfg.db_host,
                port=cfg.db_port,
                user=cfg.db_user,
                password=cfg.db_password,
                database=database,
                charset="utf8mb4",
                connect_timeout=cfg.db_connect_timeout,
                autocommit=True,
            )
        except pymysql.err.MySQLError as exc:
            raise DatabaseUnavailable(self._diagnose(exc, database)) from exc

    def connect(self):
        """连接到配置里的数据库。"""
        return self._connect(self.config.db_name)

    def connect_server(self):
        """不选库连接，用于建库和探测服务器信息。"""
        return self._connect(None)

    def _diagnose(self, exc: Exception, database: str | None) -> str:
        cfg = self.config
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        target = f"{cfg.db_user}@{cfg.db_host}:{cfg.db_port}/{database or '(未选库)'}"
        lines = [
            f"无法连接 seekdb / OceanBase：{target}",
            f"原始错误：{exc}",
            "排查建议：",
        ]
        if code in (2002, 2003, 2013):
            lines.extend(
                [
                    "  1) 实例可能未启动，或端口不是 2882。",
                    "     Docker：docker ps | findstr -i seekdb      （Linux/macOS 用 grep）",
                    "     端口：  netstat -ano | findstr 2882",
                    "  2) 用环境变量覆盖地址：PAPERDESK_DB_HOST / PAPERDESK_DB_PORT",
                    "  3) 账号不同时设置：PAPERDESK_DB_USER / PAPERDESK_DB_PASSWORD",
                ]
            )
        elif code in (1044, 1045):
            lines.extend(
                [
                    "  1) 检查用户名与密码：PAPERDESK_DB_USER / PAPERDESK_DB_PASSWORD",
                    "  2) 确认该账号对目标库有建表与 ALTER 权限（全文索引需要 ALTER）。",
                ]
            )
        elif code == 1049:
            lines.append(
                "  目标库不存在。运行 `python -m paperdesk init-db` 会自动创建；"
                "或手动 `CREATE DATABASE paperdesk DEFAULT CHARSET utf8mb4`。"
            )
        elif code == 1231:
            lines.append("  客户端/服务端字符集参数不合法；本项目固定使用 utf8mb4。")
        else:
            lines.append("  先确认 seekdb 已启动、端口正确，再用上面的环境变量覆盖默认值。")

        lines.append(f"  当前配置（密码已隐藏）：{cfg.redacted()}")
        lines.append(f"  可选环境文件：{cfg.env_file}（存在：{'是' if cfg.env_file.is_file() else '否'}）")
        return "\n".join(lines)

    # -- 基础工具 -------------------------------------------------------
    def server_version(self, conn=None) -> str:
        own = conn is None
        conn = conn or self.connect_server()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION()")
                row = cur.fetchone()
            return str(row[0]) if row else "unknown"
        finally:
            if own:
                conn.close()

    def _ensure_database(self) -> bool:
        conn = self.connect_server()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT schema_name FROM information_schema.schemata WHERE schema_name = %s",
                    (self.config.db_name,),
                )
                if cur.fetchone() is not None:
                    return False
                try:
                    cur.execute(
                        f"CREATE DATABASE `{self.config.db_name}` "
                        "DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_general_ci"
                    )
                except pymysql.err.MySQLError as exc:
                    code = exc.args[0] if exc.args else None
                    hint = (
                        "当前账号没有建库权限，请让管理员创建该库，"
                        "或把 PAPERDESK_DB_NAME 指向已有库。"
                        if code in (1044, 1227)
                        else "建库失败，请检查账号权限与库名。"
                    )
                    raise DatabaseUnavailable(
                        f"无法创建数据库 `{self.config.db_name}`：{exc}\n{hint}"
                    ) from exc
            return True
        finally:
            conn.close()

    def _table_names(self, cur) -> list[str]:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name",
            (self.config.db_name,),
        )
        return [str(row[0]) for row in cur.fetchall()]

    def _fulltext_on_server(self, cur) -> set[tuple[str, str]]:
        """按索引名判断是否已存在，避免不同版本 index_type 拼写差异导致重复建索引。"""
        names = sorted({index for _, index, _ in FULLTEXT_INDEXES})
        placeholders = ", ".join(["%s"] * len(names))
        cur.execute(
            "SELECT DISTINCT table_name, index_name FROM information_schema.statistics "
            f"WHERE table_schema = %s AND index_name IN ({placeholders})",
            (self.config.db_name, *names),
        )
        wanted = {(table, index) for table, index, _ in FULLTEXT_INDEXES}
        return {(str(row[0]), str(row[1])) for row in cur.fetchall()} & wanted

    def _ngram_token_size(self, conn) -> int | None:
        try:
            with conn.cursor() as cur:
                cur.execute("SHOW VARIABLES LIKE 'ngram_token_size'")
                row = cur.fetchone()
        except pymysql.err.MySQLError:
            return None
        if not row:
            return None
        try:
            return int(row[1])
        except (TypeError, ValueError):
            return None

    def get_meta(self, key: str) -> str | None:
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT meta_value FROM `{self.config.db_name}`.schema_meta "
                    "WHERE meta_key = %s",
                    (key,),
                )
                row = cur.fetchone()
            return str(row[0]) if row else None
        finally:
            conn.close()

    def set_meta(self, key: str, value: str, cur=None) -> None:
        sql = (
            f"INSERT INTO `{self.config.db_name}`.schema_meta (meta_key, meta_value) "
            "VALUES (%s, %s) ON DUPLICATE KEY UPDATE meta_value = VALUES(meta_value)"
        )
        if cur is not None:
            cur.execute(sql, (key, value))
            return
        conn = self.connect()
        try:
            with conn.cursor() as own:
                own.execute(sql, (key, value))
        finally:
            conn.close()

    # -- 建表 -----------------------------------------------------------
    def init_schema(self) -> SchemaReport:
        created_database = self._ensure_database()
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                existing = set(self._table_names(cur))
                created_tables: list[str] = []
                for name, ddl in _TABLES.items():
                    try:
                        cur.execute(ddl)
                    except pymysql.err.MySQLError as exc:
                        raise DatabaseError(
                            f"建表失败（{name}）：{exc}\n"
                            "请确认账号有 CREATE / ALTER 权限，且数据库版本支持 utf8mb4 InnoDB。"
                        ) from exc
                    if name not in existing:
                        created_tables.append(name)

                parser, indexes, index_created, warnings = self._ensure_fulltext(cur, conn)
                ngram = self._ngram_token_size(conn)
                version = self.server_version(conn)
                tables = self._table_names(cur)

                self.set_meta("schema_version", str(SCHEMA_VERSION), cur)
                self.set_meta("fulltext_parser", parser, cur)
                if ngram is not None:
                    self.set_meta("ngram_token_size", str(ngram), cur)
                self.set_meta(
                    "fulltext_indexes",
                    ", ".join(f"{table}.{index}" for table, index in sorted(indexes)),
                    cur,
                )
        finally:
            conn.close()

        return SchemaReport(
            database=self.config.db_name,
            server_version=version,
            created_database=created_database,
            created_tables=created_tables,
            tables=tables,
            fulltext_indexes={f"{table}.{index}": parser for table, index in sorted(indexes)},
            fulltext_parser=parser,
            ngram_token_size=ngram,
            index_created=index_created,
            warnings=warnings,
        )

    def _ensure_fulltext(self, cur, conn) -> tuple[str, set[tuple[str, str]], bool, list[str]]:
        existing = self._fulltext_on_server(cur)
        wanted = {(table, index) for table, index, _ in FULLTEXT_INDEXES}
        missing = [item for item in FULLTEXT_INDEXES if (item[0], item[1]) not in existing]
        warnings: list[str] = []

        if not missing:
            parser = self.get_meta_value(cur, "fulltext_parser") or "unknown"
            return parser, existing & wanted, False, warnings

        errors: list[str] = []
        for candidate in PARSER_CANDIDATES:
            try:
                for table, index, column in missing:
                    statement = (
                        f"ALTER TABLE `{table}` ADD FULLTEXT INDEX `{index}` (`{column}`)"
                    )
                    if candidate:
                        statement += f" WITH PARSER {candidate}"
                    cur.execute(statement)
            except pymysql.err.MySQLError as exc:
                errors.append(f"{candidate or 'default'}: {exc}")
                existing = self._fulltext_on_server(cur)
                missing = [item for item in FULLTEXT_INDEXES if (item[0], item[1]) not in existing]
                if not missing:
                    break
                continue

            parser = candidate or "default"
            if candidate is None:
                warnings.append(
                    "服务端没有可用的中文全文解析器（ik/beng/ngram），已退化为默认解析器；"
                    "连续中文可能无法被 MATCH 命中。请换用带中文解析器的 seekdb 构建。"
                )
            existing = self._fulltext_on_server(cur)
            return parser, existing & wanted, True, warnings

        raise DatabaseError(
            "全文索引创建失败，所有解析器都不可用：\n  "
            + "\n  ".join(errors)
            + "\n请确认 seekdb 版本支持 FULLTEXT 与 WITH PARSER（seekdb v1.4.0 起支持 ngram）。"
        )

    @staticmethod
    def get_meta_value(cur, key: str) -> str | None:
        cur.execute("SELECT meta_value FROM schema_meta WHERE meta_key = %s", (key,))
        row = cur.fetchone()
        return str(row[0]) if row else None

    # -- 事务 -----------------------------------------------------------
    @contextmanager
    def transaction(self, conn=None) -> Iterator:
        own = conn is None
        conn = conn or self.connect()
        try:
            conn.begin()
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if own:
                conn.close()

    # -- 诊断 -----------------------------------------------------------
    def index_listing(self) -> list[dict]:
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, index_name, index_type, "
                    "group_concat(column_name ORDER BY seq_in_index) "
                    "FROM information_schema.statistics "
                    "WHERE table_schema = %s AND table_name IN ('chunks', 'study_cards') "
                    "GROUP BY table_name, index_name, index_type "
                    "ORDER BY table_name, index_name",
                    (self.config.db_name,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        wanted = {index for _, index, _ in FULLTEXT_INDEXES}
        return [
            {
                "table": str(row[0]),
                "index": str(row[1]),
                "type": str(row[2]),
                "columns": str(row[3]).split(",") if row[3] else [],
            }
            for row in rows
            if str(row[1]) in wanted
        ]

    def count_chunks(self, keyword: str | None = None) -> int:
        """用于导入后自检；给出关键词时验证 MATCH 是否真的能命中中文。"""
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                if keyword is None:
                    cur.execute("SELECT COUNT(*) FROM chunks")
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM chunks WHERE MATCH(content) "
                        "AGAINST (%s IN BOOLEAN MODE)",
                        (keyword,),
                    )
                row = cur.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def fetch_all(self, sql: str, args: Sequence[object] = ()) -> list[tuple]:
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(args))
                return list(cur.fetchall())
        finally:
            conn.close()
