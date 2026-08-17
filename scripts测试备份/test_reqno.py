# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"C:\Users\mjj\Desktop\purchase_system")
import app as A

conn = A.db()
no = A.gen_req_no(conn)
print("生成编号:", no)
print("格式正确(SC+年月日+2位序号, 无横线):", len(no) == 12 and no[:2] == "SC" and "-" not in no)
rows = conn.execute("SELECT req_no, dept, apply_date FROM purchase_requests WHERE req_no LIKE 'SC20260804%'").fetchall()
print("今日已有申请:", [dict(r) for r in rows] if rows else "无")
conn.close()
