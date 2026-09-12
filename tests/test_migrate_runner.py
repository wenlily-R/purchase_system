#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: 数据库迁移器 migrate_db.py (V11.293d 修复的行为)

背景: 2026-09-12 迁移器"遇错即 break" → 历史非幂等迁移(20260909_V11254 的 biz_no 已被 init_db 幂等补过)
报 duplicate column name 后中断, 其后所有新迁移(含 V11297 报价运费列)永远排不上队 → 商家报价接口 500。
本测试钉住修复后的三条行为: ①重复列=已生效→跳过并记日志 ②单条失败不再堵住后续迁移 ③真失败仍以非零退出暴露。

全程 tempfile 临时库 + 临时 migrations 目录, 不触碰 data/purchase.db 与仓库 migrations/。
跑法: .venv/Scripts/python.exe tests/test_migrate_runner.py   (Mac: python3 tests/test_migrate_runner.py)
"""
import os, sys, sqlite3, shutil, tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(BASE, 'data', 'purchase.db')
sys.path.insert(0, BASE)
import migrate_db as M

P, F = [], []


def ck(n, c, e=''):
    (P if c else F).append(n)
    print(('  OK   ' if c else '  FAIL ') + n + (('  | ' + str(e)[:200]) if e else ''))


def setup(tmp, tag, migs):
    """临时库(含 orders 表 + 已有 biz_no 的 hascol 表) + 临时 migrations 目录"""
    d = os.path.join(tmp, tag)
    os.makedirs(d, exist_ok=True)
    db = os.path.join(d, 'purchase.db')
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, name TEXT)")
    c.execute("CREATE TABLE hascol(id INTEGER PRIMARY KEY, biz_no TEXT)")  # 模拟 init_db 已幂等补过 biz_no
    c.commit(); c.close()
    md = os.path.join(d, 'migrations')
    os.makedirs(md, exist_ok=True)
    for name, sql in migs:
        open(os.path.join(md, name), 'w', encoding='utf-8').write(sql)
    return db, md


def run(db, md):
    M.DB, M.MIG_DIR = db, md
    return M.main()


def cols(db, t):
    c = sqlite3.connect(db)
    r = [x[1] for x in c.execute("PRAGMA table_info(%s)" % t)]
    c.close()
    return r


def logged(db):
    c = sqlite3.connect(db)
    r = {x[0] for x in c.execute("SELECT name FROM migrations_log")}
    c.close()
    return r


def main():
    live_before = None
    if os.path.exists(LIVE):
        c = sqlite3.connect(LIVE)
        live_before = c.execute("SELECT COUNT(*) FROM migrations_log").fetchone()[0]
        c.close()
    tmp = tempfile.mkdtemp(prefix='test_migrate_runner-')

    print('[1] 重复列迁移(历史非幂等) → 跳过并记为已应用, 不中断')
    db, md = setup(tmp, 'case1', [
        ('20260101_A_重复列.sql', "ALTER TABLE hascol ADD COLUMN biz_no TEXT;"),
        ('20260102_B_新列.sql', "ALTER TABLE orders ADD COLUMN freight REAL DEFAULT 0;"),
    ])
    rc = run(db, md)
    ck('退出码=0(重复列不算失败)', rc == 0, rc)
    ck('重复列文件记入 migrations_log(不再重复尝试)', '20260101_A_重复列.sql' in logged(db), logged(db))
    ck('其后的新迁移仍被应用(原实现 break 后永远排不上队)', 'freight' in cols(db, 'orders'), cols(db, 'orders'))
    ck('执行前自动备份已生成', any(x.startswith('purchase.db.bak_migrate_') for x in os.listdir(os.path.dirname(db))))
    rc2 = run(db, md)
    ck('二次运行幂等(退出码0/日志不变)', rc2 == 0 and len(logged(db)) == 2, (rc2, logged(db)))

    print('[2] 真·坏迁移: 记失败、退出码非0, 但不堵住后续迁移')
    db2, md2 = setup(tmp, 'case2', [
        ('20260101_A_坏.sql', "ALTER TABLE no_such_table ADD COLUMN x INTEGER;"),
        ('20260102_B_好.sql', "ALTER TABLE orders ADD COLUMN freight REAL DEFAULT 0;"),
    ])
    rc = run(db2, md2)
    ck('退出码非0(真失败会被报出来)', rc == 1, rc)
    ck('坏迁移未被误记为已应用', '20260101_A_坏.sql' not in logged(db2), logged(db2))
    ck('排在其后的好迁移仍被应用', 'freight' in cols(db2, 'orders'), cols(db2, 'orders'))
    ck('好迁移记为已应用', '20260102_B_好.sql' in logged(db2), logged(db2))

    print('[3] 安全: 不触碰真实库')
    if live_before is not None:
        c = sqlite3.connect(LIVE)
        ck('真实库 migrations_log 未被本测试改动', c.execute("SELECT COUNT(*) FROM migrations_log").fetchone()[0] == live_before)
        c.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ck('临时目录已清理', not os.path.exists(tmp))

    print()
    print('=== 结果: %d 通过 / %d 失败 ===' % (len(P), len(F)))
    if F:
        print('失败项: ' + ' | '.join(F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
