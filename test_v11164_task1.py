# -*- coding: utf-8 -*-
"""V11.164 任务1端到端验证: 暂估/正式入库界限 + 红冲语义 + 三表判定"""
import sys, os, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as appmod
appmod.start_instances = lambda *a, **k: None  # 屏蔽真实钉钉推送

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data/purchase.db')
def q(sql, args=()):
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    r = c.execute(sql, args).fetchall()
    c.close()
    return r
def ex(sql, args=()):
    c = sqlite3.connect(DB, timeout=30)
    c.execute(sql, args)
    c.commit()
    c.close()

passed = []
def check(name, cond, extra=''):
    passed.append((name, cond, extra))
    print(('✅' if cond else '❌'), name, (' | ' + str(extra) if extra and not cond else ''))

# ---------- 步骤1: 登录 ----------
client = appmod.app.test_client()
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
ok = r.status_code == 200
check('admin 登录', ok, r.text[:100] if not ok else '')
if not ok:
    sys.exit(1)

# ---------- 步骤2: 手动入库 默认(不传is_est) → 暂估 is_est=1 ----------
r = client.post('/api/receivings', json={
    'items': [{'item_name': '【测试】暂估默认入库', 'quantity': 5, 'unit': '个', 'price': 10}],
    'dept': '库房', 'est_amount': 50})
check('手动入库默认创建成功', r.status_code == 200, r.text[:200])
rn1 = r.get_json().get('receive_no') if r.status_code == 200 else None
row = q("SELECT * FROM receivings WHERE receive_no=?", (rn1,))
check('手动入库默认 is_est=1(暂估)', row and row[0]['is_est'] == 1, dict(row[0]) if row else None)
rid1 = row[0]['id'] if row else None

# ---------- 步骤3: 手动入库 正式(is_est=0) ----------
r = client.post('/api/receivings', json={
    'items': [{'item_name': '【测试】正式入库', 'quantity': 3, 'unit': '个', 'price': 20}],
    'dept': '库房', 'is_est': 0})
check('手动入库正式创建成功', r.status_code == 200, r.text[:200])
rn2 = r.get_json().get('receive_no') if r.status_code == 200 else None
row = q("SELECT * FROM receivings WHERE receive_no=?", (rn2,))
check('手动入库正式 is_est=0', row and row[0]['is_est'] == 0, dict(row[0]) if row else None)

# ---------- 步骤4: 自动路径(货到付款下单) → 自动入库单 is_est=0 ----------
r = client.post('/api/orders', json={
    'trade_mode': '货到付款', 'supplier': '【测试】供应商A',
    'items': [{'item_name': '【测试】自动入库物资', 'quantity': 2, 'unit': '个', 'price': 50}]})
check('货到付款下单成功', r.status_code == 200, r.text[:200])
od = r.get_json() if r.status_code == 200 else {}
orderno = od.get('order_no')
row = q("SELECT * FROM receivings WHERE order_id=(SELECT id FROM purchase_orders WHERE order_no=?)", (orderno,)) if orderno else []
check('下单自动生成入库单', len(row) == 1, f'order={orderno}')
check('自动入库单 is_est=0(正式)', row and row[0]['is_est'] == 0, dict(row[0]) if row else None)
check('自动入库单 无发票号', row and not (row[0]['invoice_no'] or ''), '')

# ---------- 步骤5: 红冲语义 ----------
# 对暂估单(est_amount=50)红冲: 发票68 → is_est保持1, invoice_amount=68, est_amount保留50, 差价18
r = client.post(f'/api/receivings/{rid1}/invoice-match', json={
    'invoice_no': 'TEST-INV-001', 'amount': 68, 'invoice_type': '增值税专用发票'})
check('暂估单红冲成功', r.status_code == 200, r.text[:200])
row = q("SELECT * FROM receivings WHERE id=?", (rid1,))
r0 = row[0] if row else None
check('红冲后 is_est 仍=1(暂估已红冲)', r0 and r0['is_est'] == 1, '')
check('红冲后 est_amount 保留50(不被覆盖)', r0 and abs((r0['est_amount'] or 0) - 50) < 0.001, r0 and r0['est_amount'])
check('红冲后 invoice_amount=68', r0 and abs((r0['invoice_amount'] or 0) - 68) < 0.001, r0 and r0['invoice_amount'])
check('红冲后 invoice_no 已记录', r0 and r0['invoice_no'] == 'TEST-INV-001', '')
# 差价 = 68 - 50 = 18 (前端由 invoice_amount - est_amount 计算)
diff = (r0['invoice_amount'] or 0) - (r0['est_amount'] or 0) if r0 else 0
check('差价=invoice_amount-est_amount=18', abs(diff - 18) < 0.001, diff)
# 重复红冲被拒
r = client.post(f'/api/receivings/{rid1}/invoice-match', json={'invoice_no': 'TEST-INV-002', 'amount': 70})
check('重复红冲被拒(400)', r.status_code == 400, r.text[:100])
# 正式单红冲被拒
r2row = q("SELECT id FROM receivings WHERE receive_no=?", (rn2,))
if r2row:
    r = client.post(f'/api/receivings/{r2row[0]["id"]}/invoice-match', json={'invoice_no': 'TEST-INV-003', 'amount': 60})
    check('正式单红冲被拒(400)', r.status_code == 400, r.text[:100])

# ---------- 步骤6: 三表判定(est-view) ----------
# 造一张暂估未红冲单(用于est组), 并把测试单状态置为已入库(三表只统计已入库)
r = client.post('/api/receivings', json={
    'items': [{'item_name': '【测试】暂估未红冲', 'quantity': 1, 'unit': '个', 'price': 100}],
    'dept': '库房', 'est_amount': 100})
rn3 = r.get_json().get('receive_no') if r.status_code == 200 else None
rid3 = q("SELECT id FROM receivings WHERE receive_no=?", (rn3,))[0]['id'] if rn3 else None
for rn in (rn1, rn2, rn3):
    ex("UPDATE receivings SET status='已入库' WHERE receive_no=?", (rn,))
# 自动生成的入库单(正式)同样置已入库后应进白入组
ex("UPDATE receivings SET status='已入库' WHERE order_id=(SELECT id FROM purchase_orders WHERE order_no=?)", (orderno,))
v = client.get('/api/reports/est-view').get_json()
est_nos = [x['receive_no'] for x in v['est']]
hc_nos = [x['receive_no'] for x in v['hc']]
br_nos = [x['receive_no'] for x in v['br']]
check('三表: 暂估组=is_est且无发票(含测试暂估未红冲)', rn3 in est_nos, f'est={est_nos}')
check('三表: 红冲组=is_est且有发票(含测试红冲单)', rn1 in hc_nos, f'hc={hc_nos}')
check('三表: 白入组=非暂估(含测试正式单)', rn2 in br_nos, f'br={br_nos}')
# 红冲组差价字段
hc_d = [x for x in v['hc'] if x['receive_no'] == rn1]
check('红冲组 diff=18 字段正确', hc_d and abs(hc_d[0].get('diff', 0) - 18) < 0.001, hc_d[0] if hc_d else None)
# 自动生成(正式)单在白入组
auto_nos = [x['receive_no'] for x in q("SELECT receive_no FROM receivings WHERE order_id=(SELECT id FROM purchase_orders WHERE order_no=?)", (orderno,))]
check('自动生成(正式)入库单在白入组', auto_nos and auto_nos[0] in br_nos, f'br={br_nos}')

# ---------- 步骤7: 前端标签逻辑(代码静态核对, index.html 2358-2361行) ----------
import re
html = open('templates/index.html', encoding='utf-8').read()
m1 = re.search(r"x\.invoice_no\?'<span[^>]*>已红冲</span>'", html)
m2 = re.search(r": x\.is_est\?'<span[^>]*>暂估</span>'", html)
check('前端标签优先级存在(invoice_no>is_est>正式)', bool(m1) and bool(m2), '')

# ---------- 清理链 ----------
if rid3:
    ex("DELETE FROM receivings WHERE receive_no IN (?,?,?)", (rn1, rn2, rn3))
ex("DELETE FROM receivings WHERE order_id=(SELECT id FROM purchase_orders WHERE order_no=?)", (orderno,))
ex("DELETE FROM order_items WHERE order_id=(SELECT id FROM purchase_orders WHERE order_no=?)", (orderno,))
ex("DELETE FROM purchase_orders WHERE order_no=?", (orderno,))
ex("DELETE FROM approval_instances WHERE (biz_type='receiving' AND biz_id IN (?,?,?)) OR (biz_type='purchase_order' AND biz_id=(SELECT id FROM purchase_orders WHERE order_no=?))", (rid1, rid3, rid3, orderno))
ex("DELETE FROM dingtalk_instances WHERE (biz_type='receiving' AND biz_id IN (?,?,?)) OR (biz_type='purchase_order' AND biz_id=(SELECT id FROM purchase_orders WHERE order_no=?))", (rid1, rid3, rid3, orderno))
ex("DELETE FROM feishu_instances WHERE (biz_type='receiving' AND biz_id IN (?,?,?)) OR (biz_type='purchase_order' AND biz_id=(SELECT id FROM purchase_orders WHERE order_no=?))", (rid1, rid3, rid3, orderno))
ex("DELETE FROM logs WHERE detail LIKE '%【测试】%' OR (action IN ('新建入库单','发票核对红冲','新建订单') AND detail LIKE '%测试%')")
for t in ('receivings', 'order_items', 'purchase_orders', 'approval_instances', 'dingtalk_instances', 'feishu_instances'):
    ex(f"UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM {t}),0) WHERE name='{t}'")
# 清理复核
left_rows = q("SELECT COUNT(*) c FROM receivings WHERE item_name LIKE '%【测试】%'")
left = left_rows[0]['c'] if left_rows else 0
left2 = q("SELECT COUNT(*) c FROM purchase_orders WHERE supplier LIKE '%【测试】%'")[0]['c']
check('清理后测试入库单零残留', left == 0, left)
check('清理后测试订单零残留', left2 == 0, left2)

print('\n==== 汇总 ====')
fails = [p for p in passed if not p[1]]
print(f'通过 {len(passed)-len(fails)}/{len(passed)}')
sys.exit(1 if fails else 0)
