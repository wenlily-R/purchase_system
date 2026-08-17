# -*- coding: utf-8 -*-
"""V11.2 电子签名验证: 手写签名 → 同意 → 签名随审批记录保存并返回"""
import os, sys, sqlite3, json, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as S

S.start_instances = lambda *a, **k: None
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
conn = sqlite3.connect(DB, timeout=30)

MARK = '【签名验证】'
conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_request' AND biz_id IN (SELECT id FROM purchase_requests WHERE purpose=?)", (MARK,))
conn.execute("DELETE FROM request_items WHERE req_id IN (SELECT id FROM purchase_requests WHERE purpose=?)", (MARK,))
conn.execute("DELETE FROM purchase_requests WHERE purpose=?", (MARK,))
conn.commit()

# 构造一条已通过的申请(直接造审批节点)
conn.execute("INSERT INTO purchase_requests(req_no,status,purpose,dept,requester,requester_id) VALUES('SIGNTEST','待审批',?,'综合办','测试员',1)", (MARK,))
rid = conn.execute("SELECT id FROM purchase_requests WHERE purpose=?", (MARK,)).fetchone()[0]
conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,total_price) VALUES(?,?,?,?,?,?)", (rid, '签名测试物资', 'X', '个', 1, 100))
# 审批节点: 一级=系统管理员(admin id=1), 保证当前用户可操作
conn.execute("INSERT INTO approval_instances(biz_type,biz_id,level_no,role,approver,approver_id,status) VALUES('purchase_request',?,1,'系统管理员','温丽',1,'pending')", (rid,))
conn.commit()
print('构造待审批申请#%s' % rid)

client = S.app.test_client()
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
assert r.status_code == 200, '登录失败'

# 伪造一个签名图片 dataURL(1x1 PNG)
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')
sig = 'data:image/png;base64,' + base64.b64encode(png).decode()

# ① 带签名同意
r = client.post('/api/approvals/purchase_request/%d/approve' % rid,
                json={'action': 'approved', 'comment': '同意', 'signature': sig})
assert r.status_code == 200, r.get_data(as_text=True)
print('① 带签名同意 OK:', r.get_json())

# ② 签名是否入库
row = conn.execute("SELECT signature,status FROM approval_instances WHERE biz_type='purchase_request' AND biz_id=?", (rid,)).fetchone()
assert row and row[0].startswith('data:image/png'), '签名未保存!'
assert row[1] == 'approved', '状态未更新'
print('② 签名已入库 OK, 长度:', len(row[0]), '状态:', row[1])

# ③ 审批列表接口返回签名
r = client.get('/api/approvals/purchase_request/%d/list' % rid)
lst = r.get_json()
assert lst and lst[0].get('signature','').startswith('data:image/png'), '列表接口未返回签名!'
print('③ 审批列表返回签名 OK')

# ④ 详情接口(申请单)含审批签名
r = client.get('/api/prequests/%d' % rid)
d = r.get_json()
assert d['approvals'][0]['signature'].startswith('data:image/png'), '详情接口未返回签名!'
print('④ 申请详情返回签名 OK')

# ⑤ 无签名也可同意(兼容旧流程)
conn.execute("UPDATE approval_instances SET status='pending', signature='' WHERE biz_type='purchase_request' AND biz_id=?", (rid,))
conn.execute("UPDATE purchase_requests SET status='待审批' WHERE id=?", (rid,))
conn.commit()
r = client.post('/api/approvals/purchase_request/%d/approve' % rid,
                json={'action': 'approved', 'comment': '同意'})
assert r.status_code == 200, r.get_data(as_text=True)
row = conn.execute("SELECT signature,status FROM approval_instances WHERE biz_type='purchase_request' AND biz_id=?", (rid,)).fetchone()
assert row[0] == '' and row[1] == 'approved', '无签名同意异常'
print('⑤ 无签名兼容 OK')

print('=== 结果: ALL PASS ===')

# 清理
conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_request' AND biz_id=?", (rid,))
conn.execute("DELETE FROM request_items WHERE req_id=?", (rid,))
conn.execute("DELETE FROM purchase_requests WHERE id=?", (rid,))
conn.execute("DELETE FROM logs WHERE detail LIKE ?", ('%签名验证%',))
conn.commit(); conn.close()
print('清理完成')
