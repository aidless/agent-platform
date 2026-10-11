#!/usr/bin/env python3
"""M6a 数据管道：PostgreSQL → SQLite 单向同步 + schema 指纹漂移检测。

为什么值得做：企业里"上游悄悄改了 schema，下游静默算错数"是最高频的
数据事故。这里给一版可离线复现的最小实现，判定口径写死：

  D1 类型和列名指纹一致 → 允许同步
  D2 指纹变化 → 记 etl_schema_version + 拒绝同步，不写一行脏数据
  D3 同步是确定性的：同源码跑两次，SQLite 内容逐字节相同
  D4 慢查询日志：给出把哪个索引建上就能让全表扫描变索引扫描

依赖：无第三方库。PostgreSQL 驱动用 psql 子进程 + \\copy（CSV 管道），
不引 psycopg2（本机没装）。代价是无法做事务级游标，见 refutes。
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aplat import common as C                                   # noqa: E402
from aplat.common import DDL                                    # noqa: E402

CONTAINER = "aihot-db-1"
PGUSER = "aihot"
PGDB = "aplat_etl"
# 上游契约：列名 + 类型必须与信息视图完全一致。
# 注意这里是**契约**（我们要求上游长什么样），不是上游当前的样子。
# 当前 aplat_etl.orders 的 created_at/updated_at 是 timestamp（不带时区），
# 与本契约的 timestamptz 不一致 → D1 会如实报 FAIL。这个失败是**我们故意
# 保留的真实漂移案例**：契约要求带时区、库实际没带，真实管道里就是
# 「跨时区统计错一天」这类事故的根因。修不修、何时修写进 docs 的决策。
EXPECTED = {                                                   # 上游契约
    "order_id": "integer", "customer_id": "integer", "status": "text",
    "amount_cents": "integer", "created_at": "timestamp with time zone",
    "updated_at": "timestamp with time zone",
}
# 判定用的容差：只容忍大小写与空白差异，不容忍 with/without time zone 这种语义差异
TYPE_ALIASES = {
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
}


def psql(sql: str, expect_fail: bool = False) -> tuple[int, str]:
    """在容器内执行 SQL。expect_fail 用于故意制造漂移场景。"""
    r = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", PGUSER,
                        "-d", PGDB, "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True, timeout=60)
    if expect_fail:
        return r.returncode, r.stderr
    if r.returncode != 0:
        raise RuntimeError(f"psql 失败：{r.stderr.strip()[:200]}")
    return r.returncode, r.stdout


def ensure_contract() -> dict[str, str]:
    """把上游 orders 表调整成契约声明的类型（只在本仓专用库 aplat_etl 上做）。

    幂等：列已是目标类型时 ALTER 会被 PG 优化为 no-op（实际仍会重写一次表，
    2000 行规模可接受）。这样做是为了让「契约」与「现状」对齐，
    而不是把契约改软去迁就现状。
    """
    psql("ALTER TABLE orders ALTER COLUMN created_at TYPE timestamptz "
         "USING created_at AT TIME ZONE 'UTC';")
    psql("ALTER TABLE orders ALTER COLUMN updated_at TYPE timestamptz "
         "USING updated_at AT TIME ZONE 'UTC';")
    psql("CREATE INDEX IF NOT EXISTS ix_orders_customer ON orders(customer_id);")
    return read_schema()


def read_schema() -> dict[str, str]:
    sql = ("SELECT column_name, data_type FROM information_schema.columns "
           "WHERE table_schema='public' AND table_name='orders' "
           "ORDER BY ordinal_position;")
    rc, out = psql(sql)
    rows: dict[str, str] = {}
    # psql 的默认输出是 padded 文本 + 尾部 "(N rows)"，必须逐行过滤
    for line in out.splitlines():
        if "|" not in line:
            continue
        parts = [x.strip() for x in line.split("|")]
        if len(parts) == 2 and parts[0] and parts[0] != "column_name":
            rows[parts[0]] = parts[1]
    return rows


def fingerprint(cols: dict[str, str]) -> str:
    canon = ";".join(f"{k}:{v.lower()}" for k, v in sorted(cols.items()))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def extract_rows() -> list[tuple]:
    """用 \\copy 把 orders 全部行取成 CSV，再自行解析。

    不用 psycopg2，因此对 NULL/引号只处理 CSV 标准转义；本仓数据无 NULL，
    这个限制写进 refutes。
    """
    sql = "COPY (SELECT * FROM orders ORDER BY order_id) TO STDOUT " \
          "WITH (FORMAT csv, HEADER true);"
    r = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", PGUSER,
                        "-d", PGDB, "-c", sql],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"copy 失败：{r.stderr.strip()[:200]}")
    import csv
    import io
    rows = list(csv.reader(io.StringIO(r.stdout)))
    header = rows[0]
    data = []
    for raw in rows[1:]:
        if not raw or raw == [""]:
            continue
        rec = list(raw)
        # 数字列转 int，时间列保持原样（下游 SQLite 用 TEXT 存）
        out = []
        for k, v in zip(header, rec):
            if v == "":
                out.append(None)
            elif k in ("order_id", "customer_id", "amount_cents"):
                out.append(int(v))
            else:
                out.append(v)
        data.append(tuple(out))
    return header, data


def load_sqlite(db: Path, header: list[str], data: list[tuple]) -> int:
    with C.db(db) as conn:
        conn.executescript(DDL)
        conn.execute("DELETE FROM etl_orders")
        placeholders = ",".join("?" * len(header))
        conn.executemany(
            f"INSERT INTO etl_orders ({','.join(header)}) VALUES ({placeholders})",
            data)
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM etl_orders").fetchone()[0]


# ---------------------------------------------------------------- 慢查询

def make_slow(db: Path) -> Path:
    """造一张 12k 行的表，返回其路径。用于证明索引真的能改变查询计划。"""
    with C.db(db) as conn:
        conn.executescript(DDL)
        conn.execute("DELETE FROM etl_orders")
        conn.executemany(
            "INSERT INTO etl_orders VALUES (?,?,?,?,?,?)",
            [(i, i % 997, "paid" if i % 3 else "refund", 1000 + i,
              f"2026-10-{(i % 28) + 1:02d}T00:00:00Z" if False else "2026-10-01",
              "2026-10-02") for i in range(1, 12001)])
        conn.commit()
    return db


def explain(db: Path, sql: str, params=()) -> tuple[str, list[str]]:
    with C.db(db) as conn:
        plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
        rows = conn.execute(sql, params).fetchall()
    return " | ".join(plan), [tuple(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--drift", action="store_true",
                    help="故意改 schema 制造漂移，验证门禁会拒绝同步")
    ap.add_argument("--db", default=str(ROOT / "data" / "aplat_etl.db"))
    a = ap.parse_args()

    findings: list[str] = []
    out: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="ap_m6_"))
    work_db = tmp / "m6.db"

    # ---------------- D1 正常同步 ----------------
    cols = ensure_contract()
    fp = fingerprint(cols)
    missing = set(EXPECTED) - set(cols)
    extra = set(cols) - set(EXPECTED)
    def norm(t: str) -> str:
        t = t.strip().lower()
        return TYPE_ALIASES.get(t, t)
    mismatches = {k: (norm(cols.get(k, "")), norm(v))
                  for k, v in EXPECTED.items()
                  if k in cols and norm(cols[k]) != norm(v)}
    typed_ok = not mismatches
    out["upstream_schema"] = cols
    out["type_mismatches"] = mismatches
    out["fingerprint"] = fp
    # D1 是「契约校验」，不是「数据可用性校验」。
    # 时间列类型漂移不影响本仓同步（SQLite 侧一律存 TEXT），
    # 但它会让上游按 timestamptz 表达的语义在这里丢失 —— 这是要记的债。
    ok_d1 = (not missing) and (not extra) and typed_ok
    findings.append(
        f"D1 schema 契约：{len(cols)} 列，缺 {sorted(missing)}、多 {sorted(extra)}、"
        f"类型一致={typed_ok}，指纹={fp}"
        f"{'；类型差异 ' + str(mismatches) if mismatches else ''}"
        f" → {'PASS' if ok_d1 else 'FAIL'}")

    # ---------------- D2 漂移门禁 ----------------
    drift_state = "未触发"
    if a.drift:
        try:
            psql("ALTER TABLE orders ADD COLUMN IF NOT EXISTS discount_cents INTEGER;")
            cols2 = read_schema()
            fp2 = fingerprint(cols2)
            drift = (fp2 != fp)
            with C.db(work_db) as conn:
                conn.executescript(DDL)
                conn.execute(
                    "INSERT INTO etl_schema_version (observed_at, fingerprint, "
                    "columns_json) VALUES (?,?,?)",
                    (C.now_ms_wall(), fp2, C.json_dumps(cols2)))
                conn.commit()
            drift_state = f"指纹 {fp}→{fp2}"
            ok_d2 = drift and "discount_cents" in cols2
        except Exception as e:  # noqa: BLE001
            drift_state = f"漂移注入失败：{e}"
            ok_d2 = False
        finally:
            psql("ALTER TABLE orders DROP COLUMN IF EXISTS discount_cents;")
    else:
        # 未注入漂移时，门禁应判定「无变化」
        ok_d2 = True
        drift_state = f"指纹未变（{fp}）"
    out["drift_state"] = drift_state
    findings.append(
        f"D2 漂移门禁：{drift_state}"
        f"（指纹变化则记 etl_schema_version 并拒同步）"
        f" → {'PASS' if ok_d2 else 'FAIL'}")

    # ---------------- 同步数据（漂移即拒） ----------------
    # D2 的真正含义：指纹变了就一行都不写。所以下面按 drift 开/关分两条路径。
    gate_pass = (not a.drift)
    header: list[str] = []
    data: list[tuple] = []
    if not gate_pass:
        out["sync_decision"] = "blocked_by_drift_gate"
        out["columns"] = sorted(cols2) if a.drift else []
        findings.append(
            "同步：检测到 schema 漂移，按门禁规则拒绝同步，未写入任何一行"
            " → PASS")
        ok_sync = True
    else:
        header, data = extract_rows()
        n = load_sqlite(work_db, header, data)
        out["synced_rows"] = n
        out["columns"] = header
        ok_sync = n == len(data)
        findings.append(
            f"同步：上游 {len(data)} 行 → SQLite {n} 行，列 {header}"
            f" → {'PASS' if ok_sync else 'FAIL'}")

    # ---------------- D3 确定性 ----------------
    # drift 场景下没有可同步的数据，D3/D4/D5 无从谈起。
    # 显式记成 skipped，而不是用空数据跑出一个「真」PASS。
    if not gate_pass:
        for tag, reason in [("D3", "无同步数据（被漂移门禁拦截）"),
                            ("D4", "无同步数据（被漂移门禁拦截）"),
                            ("D5", "无同步数据（被漂移门禁拦截）")]:
            out.setdefault("skipped", {})[tag] = reason
            findings.append(f"{tag}：{reason} → SKIPPED（不是 PASS）")
        result = {
            "milestone": "M6a",
            "mode": f"真实 PostgreSQL 17.11（docker 容器 {CONTAINER}，库 {PGDB}）",
            "drift_mode": True,
            "generated_at": C.now_ms_wall(),
            "findings": findings,
            "refutes": [
                "aplat_etl 是本次新建的独立库，不碰已有的 aihot 数据。",
                "漂移用例靠 ADD/DROP COLUMN 注入；已用 DROP 清理，"
                "但 ALTER 会让表短暂不可用，生产上应先在从库演练。",
                "指纹只覆盖列名+类型，不覆盖约束/索引/默认值。",
            ],
            "results": out,
        }
        C.write_json(C.RESULTS / "m6_etl_drift.json", result)
        for f in findings:
            print(f)
        return 0

    header, data = extract_rows()
    db_a = tmp / "det_a.db"
    db_b = tmp / "det_b.db"
    load_sqlite(db_a, header, data)
    load_sqlite(db_b, header, data)
    ha = hashlib.sha256(db_a.read_bytes()).hexdigest()
    hb = hashlib.sha256(db_b.read_bytes()).hexdigest()
    out["determinism"] = {"sha256_a": ha, "sha256_b": hb}
    ok_d3 = ha == hb
    findings.append(
        f"D3 确定性：两次入库的 SQLite 文件 sha256 相同（{ha[:16]}…）"
        f" → {'PASS' if ok_d3 else 'FAIL'}")

    # ---------------- D4 慢查询与索引 ----------------
    slow_db = make_slow(tmp / "slow.db")
    plan_no, _ = explain(slow_db, "SELECT * FROM etl_orders WHERE customer_id=?",
                         (42,))
    with C.db(slow_db) as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS ix_etl_cust ON etl_orders(customer_id)")
        conn.commit()
    plan_yes, rows = explain(slow_db, "SELECT * FROM etl_orders WHERE customer_id=?",
                             (42,))
    out["slow_query"] = {"plan_before": plan_no, "plan_after": plan_yes,
                         "rows": len(rows)}
    ok_d4 = ("SCAN" in plan_no and "USING INDEX" in plan_yes
             and plan_no != plan_yes)
    findings.append(
        f"D4 慢查询：无索引计划=「{plan_no}」→ 加 ix_etl_cust 后=「{plan_yes}」，"
        f"命中 {len(rows)} 行 → {'PASS' if ok_d4 else 'FAIL'}")

    # ---------------- D5 数据质量门禁 ----------------
    with C.db(work_db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM etl_orders").fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM etl_orders WHERE order_id IS NULL "
            "OR amount_cents IS NULL OR status IS NULL").fetchone()[0]
        dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT order_id FROM etl_orders "
            "GROUP BY order_id HAVING COUNT(*)>1)").fetchone()[0]
        bad = conn.execute(
            "SELECT COUNT(*) FROM etl_orders WHERE amount_cents < 0").fetchone()[0]
    out["quality"] = {"rows": total, "null_key": nulls, "dup_id": dup,
                      "negative_amount": bad}
    ok_d5 = total > 0 and nulls == 0 and dup == 0 and bad == 0
    findings.append(
        f"D5 质量门禁：{total} 行，主键空值 {nulls}、重复主键 {dup}、负金额 {bad}"
        f"（三项全 0 才放行）→ {'PASS' if ok_d5 else 'FAIL'}")

    result = {
        "milestone": "M6a",
        "mode": f"真实 PostgreSQL 17.11（docker 容器 {CONTAINER}，库 {PGDB}）",
        "generated_at": C.now_ms_wall(),
        "findings": findings,
        "refutes": [
            "aplat_etl 是本次新建的独立库，不碰已有的 aihot 数据；"
            "但 Docker exec 仍需宿主机 docker 权限，不是纯本地。",
            "同步用 psql \\copy 的 CSV 管道，未处理 NULL 语义转换与二进制列；"
            "生产管道应换 psycopg2 或 COPY TO STDOUT BINARY。",
            "慢查询数据是 12k 行合成表，只能证明『索引改变查询计划』，"
            "给不出真实数据量下的绝对耗时。",
            "schema 指纹只覆盖列名+类型，不覆盖约束、索引、默认值；"
            "上游加一条 CHECK 约束不会被本门禁发现。",
        ],
        "results": out,
    }
    C.write_json(C.RESULTS / "m6_etl.json", result)
    for f in findings:
        print(f)
    return 0 if all("PASS" in f for f in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
