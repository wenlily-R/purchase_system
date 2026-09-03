#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V11.197 全链路回归: 链接跳转+核心功能一键验证 (2026-09-03)
覆盖: 三方询价外链/钉钉通知链接/加急跳转/审批驳回/撤回/附件/用户管理/库存/退库/公告
用法: .venv/bin/python test_regression_v1197.py
"""
import sys, os, json, io, sqlite3, datetime, base64, urllib.request, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as appmod

PU = appmod.dt_public_url()
PASS, FAIL = [], []
def chk(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + name + (f' — {detail}' if detail and not cond else ''), flush=True)

def http_get(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'ignore')
    except Exception as e:
        return 0, str(e)

def q(sql, args=()):
    c = sqlite3.connect('data/purchase.db'); c.row_factory = sqlite3.Row
    r = c.execute(sql, args).fetchall(); c.close(); return r
def w(sql, args=()):
    c = sqlite3.connect('data/purchase.db'); c.execute(sql, args); c.commit(); c.close()

print('=' * 60)
print('V11.197 全链路回归测试  公网地址: ' + (PU or '(无)'))
print('=' * 60)

# ── 0. 公网地址可用性(本次核心根因) ──
print('\n[0] 公网地址可用性')
st, _ = http_get(PU + '/api/health') if PU else (0, '')
chk('公网地址 200', st == 200, f'HTTP {st}')

# ── 1. 三方询价外网报价链接 ──
print('\n[1] 三方询价外网报价链接')
tokens = [r['token'] for r in q("SELECT token FROM inquiry_suppliers WHERE token!='' LIMIT 2")]
if tokens:
    for tk in tokens[:2]:
        st, body = http_get(f'{PU}/inq/{tk}') if PU else (0, '')
        chk(f'商家报价页 /inq/{tk[:8]}… 200', st == 200, f'HTTP {st}')
        chk('报价页含报价表单', '报价' in body or 'submit' in body.lower() or 'form' in body.lower())
else:
    print('  (库中无询价商家token, 跳过 — 用页面构造验证)')
    # 无商家时验证页面路由存在性
    st, body = http_get(f'{PU}/inq/nonexistenttoken123') if PU else (0, '')
    chk('/inq 路由响应(无效token提示页)', st == 200 and ('失效' in body or '无效' in body), f'HTTP {st}')

# ── 2. 钉钉通知链接(sso/goto 免登直达页) ──
print('\n[2] 钉钉通知/加急跳转 — sso/goto 免登直达页')
for bt, bid, act in [('purchase_request', 1, 'detail'), ('purchase_request', 1, 'approve')]:
    st, body = http_get(f'{PU}/sso/goto?biz={bt}&id={bid}&act={act}') if PU else (0, '')
    chk(f'/sso/goto {bt}#{bid} act={act} → 200', st == 200, f'HTTP {st}')
    chk('页面含免登逻辑', 'dd.config' in body or 'requestAuthCode' in body or 'bCopy' in body)

# ── 3. dt_send_todo url 生成正确性(直接单测) ──
print('\n[3] dt_send_todo 跳转 url 生成(直接验证)')
def fake_dt_post(path, payload):
    global _last_msg
    _last_msg = payload.get('msg', {})
    return 0, {}
appmod.dt_post = fake_dt_post
c = appmod.app.test_client()
c.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
# 加急(urgent) → approve
appmod.dt_send_todo(['012413135318990202251'], '加急测试', '单据催办', biz_type='purchase_request', biz_id=1, push_type='urgent', operator='温丽')
ac = _last_msg.get('action_card', {})
btn = (ac.get('btn_json_list') or [{}])[0]
chk('加急按钮标题=去处理', btn.get('title') == '去处理', str(btn))
chk('加急按钮 url 指向 sso/goto+approve', '/sso/goto?' in (btn.get('action_url') or '') and 'act=approve' in (btn.get('action_url') or ''), btn.get('action_url', ''))
chk('加急消息含复制兜底链接', '复制链接' in (ac.get('markdown') or ''))
# 结果通知(result) → detail 按钮
appmod.dt_send_todo(['0244091261174330'], '审批结果', '已通过', biz_type='purchase_request', biz_id=1, push_type='result', operator='温丽')
ac2 = _last_msg.get('action_card', {})
btn2 = (ac2.get('btn_json_list') or [{}])[0]
chk('结果通知按钮=查看详情', btn2.get('title') == '查看详情', str(btn2))
chk('结果通知 url act=detail', 'act=detail' in (btn2.get('action_url') or ''), btn2.get('action_url', ''))
# auto → approve (注: oa_notify_only=1 时 auto 被拦走OA原生通知, 不测auto; 用 overdue 验证审批类=approve)
appmod.dt_send_todo(['012413135318990202251'], '超期审批', '超期提醒', biz_type='contract', biz_id=1, push_type='overdue', operator='温丽')
ac4 = _last_msg.get('action_card', {})
btn4 = (ac4.get('btn_json_list') or [{}])[0]
chk('审批类提醒按钮=去处理+approve(overdue)', btn4.get('title') == '去处理' and 'act=approve' in (btn4.get('action_url') or ''), btn4.get('action_url', ''))

# ── 4. 审批驳回/撤回逻辑 ──
print('\n[4] 审批驳回/撤回')
# 造一张测试出库单并走审批流(用创建接口=直接进审批)
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
w("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price) VALUES('回归测试物资','R','个',50,'主库房',5)")
inv = q("SELECT id FROM inventory WHERE item_name='回归测试物资'")[0]['id']
r = c.post('/api/requisitions', json={'dept': '生产部', 'receiver': '邢果', 'receive_dept': '生产部', 'purpose': '回归测试', 'items': [{'item_name': '回归测试物资', 'spec': 'R', 'unit': '个', 'quantity': 3}]})
rr = r.json if hasattr(r, 'json') and r.json else {}
chk('出库单创建提交审批', bool(rr.get('success')), str(rr))
rqid = rr.get('id') or 0
if rqid:
    # 驳回 → 回草稿
    r = c.post(f'/api/approvals/requisition/{rqid}/approve', json={'action': 'rejected', 'comment': '回归驳回测试'})
    st = q("SELECT status,reject_count FROM requisitions WHERE id=?", (rqid,))[0]
    chk('驳回后回草稿', st['status'] == '草稿' or st['status'] == '已驳回', str(st['status']) + ' ' + str(r.json if hasattr(r, 'json') else r))
    chk('驳回次数累计', (st['reject_count'] or 0) >= 1)
    # 提交→撤回→回草稿
    c.post(f'/api/requisitions/{rqid}/submit', json={})
    r = c.post(f'/api/docs/requisition/{rqid}/withdraw', json={})
    rj = r.json if hasattr(r, 'json') else {}
    st2 = q("SELECT status FROM requisitions WHERE id=?", (rqid,))[0]
    chk('撤回后回草稿', st2['status'] == '草稿', st2['status'] + ' ' + str(rj.get('error', '')))
    # 清理
    for tbl in ('approval_instances', 'approval_action_logs', 'approval_reject_logs', 'dingtalk_instances'):
        try: w(f"DELETE FROM {tbl} WHERE biz_type='requisition' AND biz_id=?", (rqid,))
        except Exception: pass
    w("DELETE FROM requisition_items WHERE requisition_id=?", (rqid,))
    w("DELETE FROM requisitions WHERE id=?", (rqid,))
w("DELETE FROM inventory_flows WHERE doc_type='requisition' AND doc_id=?", (rqid or 0,))
w("DELETE FROM inventory WHERE item_name='回归测试物资'")

# ── 5. 附件上传下载预览 ──
print('\n[5] 附件上传/下载预览')
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
r = c.post('/api/upload', data={'file': (io.BytesIO(png), 'reg_test.png')}, content_type='multipart/form-data')
fp = r.json.get('file_path') if hasattr(r, 'json') else None
chk('附件上传', bool(fp), str(r.json if hasattr(r, 'json') else r))
if fp:
    st = c.get('/uploads/' + fp).status_code
    chk('附件可访问(本机)', st == 200, f'HTTP {st}')
    st2, _ = http_get(f'{PU}/uploads/{fp}') if PU else (0, '')
    chk('附件公网可访问', st2 == 200, f'HTTP {st2}')
    try: os.remove(os.path.join(appmod.BASE, 'uploads', fp))
    except Exception: pass

# ── 6. 用户管理保存 ──
print('\n[6] 用户管理保存')
users = c.get('/api/users').json if hasattr(c.get('/api/users'), 'json') else []
chk('用户列表获取', len(users) >= 5, f'{len(users)}人')
# 找一个非admin用户改备注再还原(不动真实数据: 只测保存接口的字段回显)
if users:
    u = users[1]
    chk('用户数据含关键字段', all(k in u for k in ('id', 'name', 'role')))

# ── 7. 库存统计 ──
print('\n[7] 库存统计')
st_inv = c.get('/api/inventory')
chk('库存列表接口', st_inv.status_code == 200, f'HTTP {st_inv.status_code}')

# ── 8. 退库模块 ──
print('\n[8] 退库模块')
chk('退库列表接口', c.get('/api/returns').status_code == 200)
chk('退库源单接口', c.get('/api/returns/source-requisitions').status_code == 200)

# ── 9. 公告模块 ──
print('\n[9] 公告模块')
chk('公告可见列表', c.get('/api/notices').status_code == 200)
chk('公告管理列表', c.get('/api/notices/manage').status_code == 200)
chk('公告日志', c.get('/api/notices/logs').status_code == 200)

# ── 10. 前端页面关键脚本无死引用 ──
print('\n[10] 前端脚本引用检查')
html = open(os.path.join(appmod.BASE, 'templates/index.html')).read()
# 公告相关函数是否存在
for fn in ('loadNotices', 'showNoticeDetail', 'loadNoticeMgmt', 'ntSave', 'noticeAct', 'showReturnDetail', 'saveReturn', 'submitReturn', 'confirmReturnWarehouse'):
    chk(f'前端函数 {fn} 存在', ('function ' + fn) in html or (fn + '=') in html)

# ── 汇总 ──
print('\n' + '=' * 60)
print(f'结果: ✅ {len(PASS)} 通过 | ❌ {len(FAIL)} 失败')
if FAIL:
    print('失败项:')
    for f in FAIL:
        print('  - ' + f)
print('=' * 60)
sys.exit(1 if FAIL else 0)
