# -*- coding: utf-8 -*-
"""补充清理: 库存表 inventory(上轮脚本遗漏)"""
import sqlite3, os
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
conn = sqlite3.connect(DB, timeout=30)
conn.execute('DELETE FROM inventory')
conn.execute("DELETE FROM sqlite_sequence WHERE name='inventory'")
conn.commit()
print('inventory 剩余:', conn.execute('SELECT COUNT(*) FROM inventory').fetchone()[0])
print('inventory_flows 剩余:', conn.execute('SELECT COUNT(*) FROM inventory_flows').fetchone()[0])
conn.close()
