# -*- coding: utf-8 -*-
"""三方询价 V11.0 端到端验证(2026-08-14 Mac 重写版)
配方照抄 purchase-system-ops 技能: 屏蔽钉钉发起 + sqlite 造已通过申请 + test_client 全链路 + 清理
"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as appmod

# 1) 屏蔽真实钉钉发起(零额度消耗)
appmod.start_instances = lambda *a, **k: None

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
conn = sqlite3.connect(DB, timeout=30)

PASS = 0; FAIL = 0
def check(name, cond, extra=''):
    global PASS, FAIL
    if cond: PASS += 1; print('  ✅', name, extra)
    else: FAIL += 1; print('  ❌', name, extra)

# 2) 造一条「已通过」测试申请(带标记, 后续清理)
cur = conn.execute("""INSERT INTO purchase_requests(req_no,dept,requester,requester_id,purpose,target_date,status,total_estimated,remark)
    VALUES(?,?,?,?,?,?,?,?,?)""",
    ('SC-TEST-INQ-%s' % int(__import__('time').time()), '综合办', '温丽', 1,
     '【询价测试】三方询价端到端验证专用', '2026-08-20', '已通过', 1000, 'V11.0重写验证, 验证后清理'))
req_id = cur.lastrowid
for nm, sp, qt, ep in [('测试物资A', 'A型', 2, 300), ('测试物资B', 'B型', 4, 100)]:
    conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,estimated_price,total_price) VALUES(?,?,?,?,?,?,?)",
                 (req_id, nm, sp, '个', qt, ep, ep*qt))
conn.commit()
print('[1] 测试申请已创建: id=%d' % req_id)

client = appmod.app.test_client()

# 3) 登录
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
check('登录 admin', r.status_code == 200, 'HTTP %d' % r.status_code)

# 4) 可询价列表应包含该申请
r = client.get('/api/inquiries/eligible')
el = r.get_json()
check('eligible 含测试申请', any(x['id'] == req_id for x in el), '共%d条' % len(el))

# 5) 发起询价(3家)
r = client.post('/api/inquiries', json={'req_id': req_id, 'suppliers': [
    {'name': '供应商甲', 'contact': '张三', 'phone': '13900000001'},
    {'name': '供应商乙', 'contact': '李四', 'phone': '13900000002'},
    {'name': '供应商丙', 'contact': '王五', 'phone': '13900000003'},
]})
j = r.get_json()
check('发起询价成功', r.status_code == 200 and j.get('success'), str(j))
inq_id = j.get('id'); inq_no = j.get('inq_no')

# 6) 读取 token
toks = conn.execute("SELECT id,supplier_name,token FROM inquiry_suppliers WHERE inquiry_id=?", (inq_id,)).fetchall()
check('生成3家供应商token', len(toks) == 3, '实际%d家' % len(toks))
sid_map = {t[1]: t[0] for t in toks}
tok_map = {t[1]: t[2] for t in toks}

# 7) 免登录报价: 甲 ¥800, 乙 ¥750 (丙不报价)
r = client.post('/api/inquiry/vendor/%s/quote' % tok_map['供应商甲'], json={'quote_price': 800, 'quote_remark': '10天交期'})
check('甲报价800', r.status_code == 200 and r.get_json().get('success'))
r = client.post('/api/inquiry/vendor/%s/quote' % tok_map['供应商乙'], json={'quote_price': 750})
check('乙报价750', r.status_code == 200 and r.get_json().get('success'))

# 8) 详情比价: 乙应是最低价且已报价2家
r = client.get('/api/inquiries/%d' % inq_id)
d = r.get_json()
sups = d['suppliers']
quoted = [s for s in sups if s['quote_price'] > 0]
check('详情已报价2家', len(quoted) == 2, '实际%d家' % len(quoted))
check('最低价=乙750', min(s['quote_price'] for s in quoted) == 750)

# 9) 选中乙 → 生成订单
r = client.post('/api/inquiries/%d/select' % inq_id, json={'supplier_id': sid_map['供应商乙']})
j = r.get_json()
check('选中乙生成订单', r.status_code == 200 and j.get('success'), str(j))
order_no = j.get('order_no'); oid = j.get('id')

# 10) 断言: 询价单状态/订单明细/选中标记/审批本地记录
st = conn.execute("SELECT status,selected_supplier_id FROM inquiries WHERE id=?", (inq_id,)).fetchone()
check('询价单状态=已生成订单', st[0] == '已生成订单', st[0])
check('selected_supplier_id=乙', st[1] == sid_map['供应商乙'])
sel = conn.execute("SELECT is_selected FROM inquiry_suppliers WHERE id=?", (sid_map['供应商乙'],)).fetchone()
check('乙 is_selected=1', sel[0] == 1)
items = conn.execute("SELECT COUNT(*) FROM order_items WHERE order_id=?", (oid,)).fetchone()[0]
check('order_items 2行', items == 2, '实际%d行' % items)
amt = conn.execute("SELECT total_amount FROM purchase_orders WHERE id=?", (oid,)).fetchone()[0]
check('订单金额=750', abs(amt - 750) < 0.01, '实际%.2f' % amt)
appr = conn.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=?", (oid,)).fetchone()[0]
check('审批实例已本地生成', appr > 0, '%d条' % appr)

# 11) 列表接口含询价单
r = client.get('/api/inquiries')
check('列表含询价单', any(x['id'] == inq_id for x in r.get_json()))

# 12) 重复询价拦截: 已生成订单的申请不可再询价
r = client.post('/api/inquiries', json={'req_id': req_id, 'suppliers': [{'name': '供应商丁'}]})
check('重复询价被拦截', r.status_code == 400, str(r.get_json()))

# ── 清理链 ──
conn.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=?", (oid,))
conn.execute("DELETE FROM purchase_orders WHERE id=?", (oid,))
conn.execute("DELETE FROM inquiry_suppliers WHERE inquiry_id=?", (inq_id,))
conn.execute("DELETE FROM inquiries WHERE id=?", (inq_id,))
conn.execute("DELETE FROM request_items WHERE req_id=?", (req_id,))
conn.execute("DELETE FROM purchase_requests WHERE id=?", (req_id,))
conn.execute("DELETE FROM logs WHERE detail LIKE '%询价%' AND created_at>=datetime('now','localtime','-10 minutes')")
conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM inquiries),0) WHERE name='inquiries'")
conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM inquiry_suppliers),0) WHERE name='inquiry_suppliers'")
conn.commit(); conn.close()
print('[2] 测试数据已清理')

print('\n════════ 结果: %d/%d PASS ════════' % (PASS, FAIL))
sys.exit(0 if FAIL == 0 else 1)
