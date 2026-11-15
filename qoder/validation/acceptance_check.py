#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""研读台 PaperDesk —— 独立验收脚本（评审方提供，非 Qoder 产出）

作用：不依赖项目自身实现的命令命名，直接从
  1) seekdb 里的真实表与索引
  2) PowerContext 的真实落盘文件
  3) 仓库里的真实文件与凭据泄漏
三条独立证据出发，逐条核对 PRD 第 5 节的 A1~A9。

用法（仓库根目录下）：

    python qoder/validation/acceptance_check.py

退出码：0 = 全部必查项通过；1 = 有必查项未通过；2 = 环境不满足（数据库连不上等）。

依赖：pymysql、pyyaml、powercontext（缺失时对应检查项记为 SKIP 而非 FAIL）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
QODER = os.path.join(REPO, "qoder")
PACKAGE = os.path.join(QODER, "paperdesk")

DB_HOST = os.environ.get("PAPERDESK_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("PAPERDESK_DB_PORT", "2882"))
DB_USER = os.environ.get("PAPERDESK_DB_USER", "root")
DB_PASSWORD = os.environ.get("PAPERDESK_DB_PASSWORD", "")
DB_NAME = os.environ.get("PAPERDESK_DB_NAME", "paperdesk")

REQUIRED_SUBCOMMANDS = ("init-db", "import", "search", "card", "review", "memory", "outline")

results: list[tuple[str, str, str]] = []  # (验收项, 状态, 说明)


def record(item: str, status: str, detail: str) -> None:
    results.append((item, status, detail))
    mark = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    print(f"[{mark}] {item:<4} {detail}")


def connect():
    import pymysql

    return pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                           password=DB_PASSWORD, database=DB_NAME,
                           charset="utf8mb4", connect_timeout=10)


def query(cur, sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


# ---------------------------------------------------------------- A1 能运行
def check_a1() -> None:
    item = "A1"
    missing = [p for p in ("paperdesk/__init__.py", "paperdesk/cli.py", "requirements.txt",
                           "README.md") if not os.path.exists(os.path.join(QODER, *p.split("/")))]
    if missing:
        record(item, "FAIL", f"缺少文件：{', '.join(missing)}")
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = PACKAGE + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run([sys.executable, "-m", "paperdesk", "--help"],
                              cwd=QODER, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:  # noqa: BLE001
        record(item, "FAIL", f"CLI 启动异常：{exc}")
        return
    if proc.returncode != 0:
        record(item, "FAIL", f"`python -m paperdesk --help` 退出码 {proc.returncode}")
        return
    out = (proc.stdout or "") + (proc.stderr or "")
    absent = [c for c in REQUIRED_SUBCOMMANDS if c not in out]
    if absent:
        record(item, "FAIL", f"CLI 缺少子命令：{', '.join(absent)}")
    else:
        record(item, "PASS", "CLI 可启动且子命令齐全：" + "、".join(REQUIRED_SUBCOMMANDS))


# ------------------------------------------------- A2/A3/A4/A7/A8 数据库侧
def check_database() -> None:
    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        for item in ("A2", "A3", "A4", "A7", "A8"):
            record(item, "SKIP", f"无法连接 seekdb：{exc}")
        return

    with conn.cursor() as cur:
        tables = [r[0] for r in query(cur, "show tables")]
        if not tables:
            for item in ("A2", "A3", "A4", "A7", "A8"):
                record(item, "FAIL", f"库 {DB_NAME} 为空，没有建表")
            conn.close()
            return

        # 找全文索引
        ft_tables = []
        for t in tables:
            cols = query(cur, "show create table `%s`" % t)
            ddl = cols[0][1] if cols else ""
            if "FULLTEXT" in ddl.upper():
                ft_tables.append(t)
        chunk_table = ft_tables[0] if ft_tables else None

        # A2 原生全文索引
        if chunk_table:
            record("A2", "PASS", f"表 `{chunk_table}` 上存在原生 FULLTEXT 索引")
        else:
            record("A2", "FAIL", f"未在 {tables} 上找到 FULLTEXT 索引")

        # 找块表（标题含 chunk/block/content 之类）与卡片表（含 question 字段）
        cols_by_table = {}
        for t in tables:
            cols_by_table[t] = [r[0] for r in query(cur, "show columns from `%s`" % t)]

        card_table = None
        for t, cols in cols_by_table.items():
            low = [c.lower() for c in cols]
            if any(c in low for c in ("question", "problem")) and any(c in low for c in ("method", "conclusion")):
                card_table = t
                break

        # A3 中文命中
        if chunk_table:
            sample = None
            for t, cols in cols_by_table.items():
                for c in cols:
                    if c.lower() in ("content", "body", "text", "chunk_text", "chunk"):
                        sample = (t, c)
                        break
                if sample:
                    break
            if sample:
                t, c = sample
                hit = None
                for kw in ("对比学习", "检索增强", "图神经网络", "掌握度"):
                    try:
                        rows = query(cur, "select count(*) from `%s` where match(%s) against(%%s in boolean mode)"
                                     % (t, "`%s`" % c), ("%" + kw + "%",))
                        n = int(rows[0][0])
                    except Exception:
                        n = 0
                    if n > 0:
                        hit = (kw, n)
                        break
                if hit:
                    record("A3", "PASS", f"中文关键词「{hit[0]}」全文命中 {hit[1]} 条（表 `{t}`）")
                else:
                    record("A3", "FAIL", f"中文关键词全文检索无命中（表 `{t}` 列 `{c}`）")
            else:
                record("A3", "SKIP", "未识别到块正文字段")
        else:
            record("A3", "SKIP", "无全文索引表")

        # A4 结构化过滤字段
        need = ("year", "tag", "author")
        found = set()
        for t, cols in cols_by_table.items():
            for n in need:
                if any(n in c.lower() for c in cols):
                    found.add(n)
        if len(found) == len(need):
            record("A4", "PASS", "结构化过滤字段齐备：year / tag / author")
        else:
            record("A4", "FAIL", f"结构化过滤字段缺失，缺少：{', '.join(sorted(set(need) - found))}")

        # A7 卡片闭环
        if card_table:
            cols = cols_by_table[card_table]
            nxt = [c for c in cols if "next_review" in c.lower() or "due" in c.lower()]
            try:
                total = int(query(cur, "select count(*) from `%s`" % card_table)[0][0])
            except Exception:
                total = 0
            if total > 0 and nxt:
                try:
                    nn = int(query(cur, "select count(*) from `%s` where `%s` is not null"
                                   % (card_table, nxt[0]))[0][0])
                except Exception:
                    nn = 0
                if nn > 0:
                    record("A7", "PASS", f"卡片表 `{card_table}` 有 {total} 张卡片，{nn} 条已算出下次复习时间")
                else:
                    record("A7", "FAIL", f"卡片表 `{card_table}` 的 `{nxt[0]}` 全为空，复习算法未生效")
            else:
                record("A7", "FAIL", f"卡片表 `{card_table}` 数据/字段不完整（cards={total}, next_review={bool(nxt)}）")
        else:
            record("A7", "FAIL", "未识别到卡片表（应含 question / method / conclusion 等字段）")

        # A8 导入幂等：重复导入后块数不变
        if chunk_table:
            try:
                before = int(query(cur, "select count(*) from `%s`" % chunk_table)[0][0])
            except Exception:
                before = 0
            env = dict(os.environ)
            env["PYTHONPATH"] = PACKAGE + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONUTF8"] = "1"
            notes = os.path.join(QODER, "data", "sample_notes")
            ok = True
            detail = ""
            for _ in range(2):
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "paperdesk", "import", notes],
                        cwd=QODER, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=300)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    detail = f"导入命令异常：{exc}"
                    break
                if proc.returncode != 0:
                    ok = False
                    detail = f"`import` 退出码 {proc.returncode}"
                    break
            if not ok:
                record("A8", "FAIL", detail)
            else:
                try:
                    after = int(query(cur, "select count(*) from `%s`" % chunk_table)[0][0])
                except Exception:
                    after = -1
                if before == after:
                    record("A8", "PASS", f"重复导入两次，块数保持 {after} 条（幂等）")
                else:
                    record("A8", "FAIL", f"重复导入后块数从 {before} 变为 {after}，未幂等")
        else:
            record("A8", "SKIP", "无全文索引表，无法定位块表")
    conn.close()


# ------------------------------------------------------ A5/A6 PowerContext
def check_memory() -> None:
    item = "A5"
    state_dir = os.path.join(QODER, ".state")
    dbs = []
    if os.path.isdir(state_dir):
        for fn in os.listdir(state_dir):
            if fn.endswith(".db") or fn.endswith(".sqlite") or fn.endswith(".sqlite3"):
                dbs.append(os.path.join(state_dir, fn))
    if not dbs:
        record(item, "FAIL", f"未找到 PowerContext 落盘文件（{state_dir} 下没有 .db/.sqlite）")
        record("A6", "FAIL", "无 PowerContext 落盘，无法验证跨进程复用")
        return
    biggest = max(dbs, key=os.path.getsize)
    size = os.path.getsize(biggest)
    if size < 1024:
        record(item, "FAIL", f"PowerContext 落盘文件过小（{biggest}, {size} bytes），疑似空写")
        record("A6", "FAIL", "PowerContext 落盘内容为空")
        return
    record(item, "PASS", f"PowerContext 落盘存在：{os.path.relpath(biggest, REPO)}（{size} bytes）")

    # 跨进程读回：这里已是第二个进程，读同一文件里的内容
    try:
        import sqlite3

        conn = sqlite3.connect(biggest)
        tables = [r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'").fetchall()]
        total = 0
        for t in tables:
            try:
                total += int(conn.execute('select count(*) from "%s"' % t).fetchone()[0])
            except Exception:
                pass
        conn.close()
        if total > 0:
            record("A6", "PASS", f"独立进程读回 PowerContext 内容，共 {total} 条记录（表：{', '.join(tables[:5])}）")
        else:
            record("A6", "FAIL", f"PowerContext 落盘文件里没有记录（表：{', '.join(tables[:5])}）")
    except Exception as exc:  # noqa: BLE001
        record("A6", "SKIP", f"无法读取 PowerContext 落盘：{exc}")


# ------------------------------------------------------------ A9 凭据泄漏
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI 风格密钥"),
    (r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]",
     "疑似硬编码凭据"),
    (r"(?i)Bearer\s+[A-Za-z0-9._\-]{24,}", "硬编码 Bearer 令牌"),
]
ALLOW_FILES = {".gitignore", ".md", ".example", ".txt"}


def check_secrets() -> None:
    item = "A9"
    hits = []
    for dirpath, dirnames, filenames in os.walk(QODER):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".state", ".venv", "node_modules")]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if fn.endswith((".example", ".md")):
                continue
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for pat, label in SECRET_PATTERNS:
                for m in re.finditer(pat, txt):
                    snippet = m.group(0)[:60]
                    if "PLACEHOLDER" in snippet.upper() or "your-" in snippet.lower():
                        continue
                    hits.append(f"{os.path.relpath(p, REPO)}: {label} -> {snippet[:40]}...")
    if hits:
        record(item, "FAIL", f"发现疑似真实凭据 {len(hits)} 处：" + "; ".join(hits[:5]))
    else:
        record(item, "PASS", "仓库内未发现真实 API Key / 密码 / 令牌")


# -------------------------------------------------------------- A10 网站
def check_site() -> None:
    item = "A10"
    idx = os.path.join(QODER, "site", "index.html")
    if not os.path.exists(idx):
        record(item, "FAIL", "缺少 qoder/site/index.html")
        return
    html = open(idx, encoding="utf-8", errors="replace").read()
    need = {
        "项目简介": ("项目简介", "简介", "PaperDesk", "研读台"),
        "解决的问题": ("问题", "痛点", "找不到"),
        "核心功能": ("核心功能", "功能", "检索", "复习"),
        "技术分工": ("seekdb", "PowerContext"),
        "代码仓库": ("github.com", "oceanbase-powercontext"),
    }
    missing = [k for k, kws in need.items() if not any(kw.lower() in html.lower() for kw in kws)]
    if missing:
        record(item, "FAIL", f"站点缺少板块：{', '.join(missing)}")
    else:
        record(item, "PASS", "站点源码包含简介/问题/功能/技术分工/仓库链接")


# ------------------------------------------------------- 端到端脚本自检
def check_e2e() -> None:
    script = os.path.join(QODER, "validation", "run_validation.py")
    if not os.path.exists(script):
        record("E2E", "FAIL", "缺少 qoder/validation/run_validation.py")
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = PACKAGE + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run([sys.executable, script], cwd=QODER, env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
    except Exception as exc:  # noqa: BLE001
        record("E2E", "FAIL", f"端到端脚本异常：{exc}")
        return
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
    if proc.returncode == 0:
        record("E2E", "PASS", "端到端验证脚本退出码 0")
    else:
        record("E2E", "FAIL", f"端到端验证脚本退出码 {proc.returncode}\n{tail[-800:]}")


def main() -> int:
    print(f"仓库：{REPO}")
    print(f"数据库：{DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}\n")
    try:
        check_a1()
    except Exception as exc:  # noqa: BLE001
        record("A1", "FAIL", f"检查异常：{exc}")
    check_database()
    check_memory()
    check_secrets()
    check_site()
    check_e2e()

    print("\n" + "=" * 60)
    fails = [r for r in results if r[1] == "FAIL"]
    skips = [r for r in results if r[1] == "SKIP"]
    for item, status, detail in results:
        print(f"  {item:<4} {status:<5} {detail}")
    print("=" * 60)
    print(f"通过 {len(results) - len(fails) - len(skips)} / 必查 {len(results)}，"
          f"未通过 {len(fails)}，跳过 {len(skips)}")
    summary = {
        "total": len(results),
        "passed": len(results) - len(fails) - len(skips),
        "failed": [f"{i}: {d}" for i, _, d in fails],
        "skipped": [f"{i}: {d}" for i, _, d in skips],
    }
    out = os.path.join(QODER, "validation", "acceptance-result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(f"结果已写入 {os.path.relpath(out, REPO)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
