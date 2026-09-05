# -*- coding: utf-8 -*-
"""数据库迁移执行器(双机共用)

用途: 代码走 git 同步, 数据库各机独立。谁改了表结构(ALTER TABLE 等),
     必须把 SQL 写成 migrations/ 下的 .sql 文件提交进 git;
     另一台 git pull 后运行本脚本, 自动应用所有未执行过的迁移。

用法:
    python migrate_db.py              (Windows: .venv\Scripts\python.exe migrate_db.py)
    python3 migrate_db.py             (Mac/Linux)

行为:
    1. 执行前自动备份 data/purchase.db -> data/purchase.db.bak_migrate_时间戳
    2. 建 migrations_log 表, 记录每个已执行的迁移文件名
    3. 扫描 migrations/*.sql, 只执行没执行过的, 跳过已执行的(不会重复跑)
    4. 系统运行中也能执行(自动等锁最多30秒), 建议业务低峰期操作

写迁移文件规范:
    migrations/YYYYMMDD_一句话说明.sql
    例: migrations/20260905_维修模块加字段.sql
    文件里直接写 SQL, 可含多条语句; 只写一次性结构变更, 不写业务数据。
"""
import sqlite3, os, sys, glob, shutil, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'data', 'purchase.db')
MIG_DIR = os.path.join(BASE, 'migrations')


def main():
    if not os.path.exists(DB):
        print('未找到数据库:', DB)
        print('确认在采购系统代码根目录运行本脚本')
        return 1

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = DB + '.bak_migrate_' + ts
    shutil.copy2(DB, bak)
    print('[1/3] 已备份数据库 ->', os.path.basename(bak))

    conn = sqlite3.connect(DB, timeout=30)
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('CREATE TABLE IF NOT EXISTS migrations_log(name TEXT PRIMARY KEY, applied_at TEXT)')
    done = {r[0] for r in conn.execute('SELECT name FROM migrations_log')}
    conn.commit()

    files = sorted(glob.glob(os.path.join(MIG_DIR, '*.sql')))
    if not files:
        print('[2/3] migrations/ 目录没有 .sql 文件, 无需迁移')

    applied = skipped = failed = 0
    for f in files:
        name = os.path.basename(f)
        if name in done:
            print('[跳过]', name, '(已执行过)')
            skipped += 1
            continue
        sql = open(f, encoding='utf-8').read().strip()
        if not sql:
            print('[跳过]', name, '(空文件)')
            skipped += 1
            continue
        try:
            print('[执行]', name)
            conn.executescript(sql)
            conn.execute('INSERT INTO migrations_log(name, applied_at) VALUES(?,?)', (name, ts))
            conn.commit()
            applied += 1
        except Exception as e:
            conn.rollback()
            print('[失败]', name, '->', e)
            print('      已回滚该文件, 备份在', os.path.basename(bak), '可手动恢复')
            failed += 1
            break
    conn.close()

    print('[3/3] 完成: 新执行 %d 个, 跳过 %d 个%s' % (applied, skipped, ', 失败 %d 个!' % failed if failed else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
