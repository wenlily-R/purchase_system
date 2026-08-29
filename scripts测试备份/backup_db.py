#!/usr/bin/env python3
"""采购系统数据库自动备份: 每周执行, 保留最近20份
用 sqlite3 backup API 保证 WAL 模式下备份一致性
"""
import sqlite3, os, glob, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data", "purchase.db")
DST = os.path.join(BASE, "backup")
os.makedirs(DST, exist_ok=True)

name = "purchase_" + datetime.datetime.now().strftime("%Y%m%d_%H%M") + ".db"
tmp = os.path.join(DST, name + ".tmp")

try:
    src = sqlite3.connect(SRC)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    dst.close(); src.close()
    os.replace(tmp, os.path.join(DST, name))
    # 保留最近20份
    files = sorted(glob.glob(os.path.join(DST, "purchase_*.db")), reverse=True)
    for f in files[20:]:
        os.remove(f)
    print("备份完成:", name, "| 现有备份:", len(files))
except Exception as e:
    print("备份失败:", e)
    if os.path.exists(tmp):
        os.remove(tmp)
