# -*- coding: utf-8 -*-
"""V11.3 验证: ①询价选中→草稿订单(商家详情填入,不自动审批) ②草稿提交审批 ③交易模式自定义"""
import os, sys, sqlite3, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as S

S.start_instances = lambda *a, **k: None
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
conn = sqlite3.connect(DB, timeout=30)
MARK = '【V113验证】'

# 清理历史测试
for t in ('inquiry_suppliers', 'inquiries'):
    pass
reqs = conn.execute("SELECT id FROM purchase_requests WHERE purpose=?", (MARK,)).fetchall()
for (rid,) in reqs:
    for (iid,) in conn.execute("SELECT id FROM inquiries WHERE req_id=?", (rid,)).fetchall():
        conn.execute("DELETE FROM inquiry_suppliers WHERE inquiry_id=?", (iid,))
    conn.execute("DELETE FROM inquiries WHERE req_id=?", (rid,))
    oids = [r[0] for r in conn.execute("SELECT id FROM purchase_orders WHERE req_id=?", (rid,)).fetchall()]
    for (oid,) in [(o,) for o in oids]:
        conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
        conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=?", (oid,))
    conn.execute("DELETE FROM purchase_orders WHERE req_id=?", (rid,))
    conn.execute("DELETE FROM request_items WHERE req_id=?", (rid,))
conn.execute("DELETE FROM purchase_requests WHERE purpose=?", (MARK,))
conn.commit()

conn.execute("INSERT INTO purchase_requests(req_no,status,purpose,dept,requester,requester_id) VALUES('V113TEST','已通过',?,'综合办','测试',1)", (MARK,))
req_id = conn.execute("SELECT id FROM purchase_requests WHERE purpose=?", (MARK,)).fetchone()[0]
conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,total_price) VALUES(?,?,?,?,?,?)", (req_id,'物料A','X','个',2,2000))
conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,total_price) VALUES(?,?,?,?,?,?)", (req_id,'物料B','Y','箱',3,3000))
conn.commit()
print('构造申请#%s' % req_id)

client = S.app.test_client()
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
assert r.status_code == 200, '登录失败'

# ① 创建询价(3家) + 报价
r = client.post('/api/inquiries', json={'req_id': req_id, 'suppliers': [
    {'name': '甲商', 'contact': '张三', 'phone': '13800000001'},
    {'name': '乙商', 'contact': '李四', 'phone': '13800000002'},
    {'name': '丙商', 'contact': '王五', 'phone': '13800000003'}]})
iid = r.get_json()['id']
toks = [t[0] for t in conn.execute("SELECT token FROM inquiry_suppliers WHERE inquiry_id=?", (iid,)).fetchall()]
client.post('/api/inquiry/vendor/%s/quote' % toks[0], json={'quote_price': 4500, 'quote_remark': '交期7天, 货到付款'})
print('① 询价创建+报价 OK')

# ② 选中 → 订单应为草稿, 不进审批, 商家信息填入remark
sid = conn.execute("SELECT id FROM inquiry_suppliers WHERE inquiry_id=? AND supplier_name='甲商'", (iid,)).fetchone()[0]
r = client.post('/api/inquiries/%d/select' % iid, json={'supplier_id': sid})
j = r.get_json()
assert r.status_code == 200, r.get_data(as_text=True)
assert j.get('status') == '草稿', '订单应为草稿: %s' % j
oid = j['id']
order = conn.execute("SELECT status, remark, trade_mode, supplier FROM purchase_orders WHERE id=?", (oid,)).fetchone()
assert order[0] == '草稿', '状态应为草稿'
assert '张三' in order[1] and '13800000001' in order[1], '商家联系人/电话未填入: %s' % order[1]
assert '交期7天' in order[1] and '货到付款' in order[1], '报价备注未填入'
assert order[2] == '货到付款', '交易模式应从备注提取: %s' % order[2]
aprv = conn.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=?", (oid,)).fetchone()[0]
assert aprv == 0, '草稿不应有审批实例, 实际%d' % aprv
print('② 草稿订单 OK: 状态=%s, 商家信息已填入, 交易模式=%s, 无审批实例' % (order[0], order[2]))
print('   备注: %s' % order[1][:80])

# ③ 草稿提交审批 → 待审批 + 审批实例创建
r = client.post('/api/orders/%d/submit' % oid, json={})
j = r.get_json()
assert r.status_code == 200, r.get_data(as_text=True)
assert j.get('status') == '待审批', j
order = conn.execute("SELECT status FROM purchase_orders WHERE id=?", (oid,)).fetchone()
aprv = conn.execute("SELECT status FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=? ORDER BY level_no", (oid,)).fetchall()
assert order[0] == '待审批' and aprv and aprv[0][0] == 'pending', '提交后应为待审批+pending实例'
print('③ 提交审批 OK: 状态=%s, 审批实例=%s' % (order[0], aprv))

# ④ 交易模式自定义: 创建订单用自定义模式
r = client.post('/api/orders', json={'items': [{'item_name': '测试品', 'quantity': 1, 'price': 100}], 'supplier': '甲商', 'trade_mode': '月结30天'})
assert r.status_code == 200, r.get_data(as_text=True)
oid2 = r.get_json()['id']
tm = conn.execute("SELECT trade_mode FROM purchase_orders WHERE id=?", (oid2,)).fetchone()[0]
assert tm == '月结30天', '自定义交易模式未保存: %s' % tm
print('④ 自定义交易模式 OK: %s' % tm)

# 清理
for oid in (oid, oid2):
    conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=?", (oid,))
conn.execute("DELETE FROM purchase_orders WHERE id IN (?,?)", (oid, oid2))
conn.execute("DELETE FROM inquiry_suppliers WHERE inquiry_id=?", (iid,))
conn.execute("DELETE FROM inquiries WHERE id=?", (iid,))
conn.execute("DELETE FROM request_items WHERE req_id=?", (req_id,))
conn.execute("DELETE FROM purchase_requests WHERE id=?", (req_id,))
conn.execute("DELETE FROM logs WHERE detail LIKE ?", ('%V113%',))
conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM inquiries),0) WHERE name='inquiries'")
conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM purchase_orders),0) WHERE name='purchase_orders'")
conn.commit(); conn.close()
print('=== 结果: ALL PASS ===')
print('清理完成')
