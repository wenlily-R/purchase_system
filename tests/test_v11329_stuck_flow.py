# -*- coding: utf-8 -*-
"""V11.329 应急采购「审批卡单/详情页/配置页」专项回归(本机 5899)
覆盖: ①提交后自动进应急临时审批节点且审批人已绑定(不再空白) ②审批中心列表能看到单号/内容/金额
      ③审批通过→采购接单→临时入库→库存入账可领料 ④3工作日倒计时 ⑤补资料→正式审批→财务复核
      ⑥转正审批节点开关: 关闭=财务复核后直接待转正; 开启=财务复核后进转正审批, 通过才待转正
      ⑦转正闭环→临时入库转正式+解锁付款 ⑧审批流配置页 GET/POST 可正常读写
"""
import json, os, re, sqlite3, sys, time, urllib.request, urllib.error, http.cookiejar

BASE = 'http://127.0.0.1:5899'
DB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'purchase.db'))
TAG = '【测试】V11329'
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
ok, bad = [], []


def api(p, d=None, method=None):
    last = None
    for _ in range(3):
        try:
            r = op.open(urllib.request.Request(BASE + p, data=json.dumps(d).encode() if d is not None else None,
                                               method=method or ('POST' if d is not None else 'GET'),
                                               headers={'Content-Type': 'application/json'}), timeout=60)
            return r.status, r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode('utf-8', 'replace')
        except Exception as e:
            last = e; time.sleep(4)
    return 0, str(last)


def j(b):
    try:
        return json.loads(b)
    except Exception:
        return {}


def emsg(b):
    x = j(b)
    return str(x.get('error') or x.get('message') or b)[:170]


def chk(n, c, x=''):
    (ok if c else bad).append(n)
    print(('  ✅ ' if c else '  ❌ ') + n + (('  | ' + str(x)[:150]) if x else ''))


def q(sql, a=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = [dict(x) for x in c.execute(sql, a).fetchall()]; c.close(); return r


print('=== V11.329 应急采购 卡单/详情/配置页 专项回归 ===')
_old = q("SELECT value FROM sys_config WHERE key='dingtalk_enabled'")
_old = _old[0]['value'] if _old else '1'
c0 = sqlite3.connect(DB); c0.execute("UPDATE sys_config SET value='0' WHERE key='dingtalk_enabled'"); c0.commit(); c0.close()
chk('登录', api('/api/login', {'username': 'admin', 'password': 'admin123'})[0] == 200)
EIDS = []
_cv_old = None
try:
    # ---- ① 配置页读写 ----
    st, b = api('/api/emergency/flow-config')
    d = j(b)
    chk('配置页 GET: 5条审批链 + 阈值 + 转正节点', st == 200 and len(d.get('chains', {})) == 5 and 'limits' in d and 'convert' in d, sorted(d.get('chains', {}).keys()))
    chk('临时审批一级已绑定审批人(温丽/admin)', (d['chains']['emergency_temp']['levels'][0]['approver'] or '') == 'admin',
        d['chains']['emergency_temp']['levels'][0])
    chk('财务复核/延期审批保持按角色(未被误绑)', (d['chains']['emergency_finance']['levels'][0]['approver'] or '') == ''
        and (d['chains']['emergency_extend']['levels'][0]['approver'] or '') == '')
    _cv_old = d['convert']

    # ---- ② 提交 → 自动进临时审批节点, 审批人非空 ----
    st, b = api('/api/emergency', {'project': TAG + '工地', 'item_name': TAG + '料', 'quantity': 2, 'unit': '个',
                                   'est_amount': 3000, 'need_arrive': '今天下午', 'reason': '停工待料急用'})
    r = j(b); EIDS.append(r.get('id')); E1 = r.get('id')
    chk('提交应急申请成功', st == 200 and str(r.get('emg_no', '')).startswith('YJ-'), r.get('emg_no'))
    inst = q("SELECT * FROM approval_instances WHERE biz_type='emergency_temp' AND biz_id=?", (E1,))
    chk('自动进入应急临时审批节点(1级)', len(inst) == 1 and inst[0]['status'] == 'pending', inst)
    chk('审批人已绑定(不再空白→不卡单)', bool((inst[0]['approver'] or '').strip()) and (inst[0]['approver_id'] or 0) > 0, inst[0]['approver'])
    chk('审批人=温丽', (inst[0]['approver'] or '') == '温丽', inst[0]['approver'])

    # ---- ③ 审批中心可见(单号/内容/金额不再空白) ----
    pend = j(api('/api/approvals/all-pending')[1])
    row = [x for x in pend if x['biz_type'] == 'emergency_temp' and x['biz_id'] == E1]
    chk('审批中心能看到该应急审批', len(row) == 1, len(row))
    chk('列表单号/内容/金额非空', bool(str(row[0].get('biz_no') or '').strip()) and bool(str(row[0].get('biz_name') or '').strip())
        and float(row[0].get('biz_amount') or 0) > 0,
        {k: row[0].get(k) for k in ('biz_no', 'biz_name', 'biz_amount')} if row else '')
    chk('详情接口可打开(详情页数据源)', j(api('/api/emergency/%d' % E1)[1]).get('emg_no') == r.get('emg_no'))

    # ---- ④ 审批通过 → 采购接单 → 临时入库 → 库存入账 ----
    api('/api/approvals/emergency_temp/%d/approve' % E1, {'action': 'approved', 'comment': '急用同意', 'signature': 'x'})
    chk('临时审批通过 → 流转采购执行(采购接单)', q("SELECT status FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '采购接单',
        q("SELECT status,temp_approved_by FROM emergency_purchases WHERE id=?", (E1,))[0])
    st, b = api('/api/emergency/%d/supplier' % E1, {'supplier': TAG + '供应商', 'quote_amt': 3000, 'no_compare': True, 'no_compare_reason': '抢险仅一家'})
    chk('采购接单成功', st == 200, emsg(b))
    st, b = api('/api/emergency/%d/temp-stock' % E1, {'qty': 2, 'warehouse': '主库房', 'zone': '待验区', 'attachments': ['t1.jpg', 't2.jpg']})
    tr = j(b); RID = tr.get('receive_id')
    chk('临时入库成功(生成入库单+立即入账)', st == 200 and (RID or 0) > 0, tr.get('receive_no'))
    chk('库存已入账(可领用)', len(q("SELECT 1 FROM inventory WHERE item_name=? AND quantity>=2", (TAG + '料',))) == 1)
    chk('资料补齐3工作日倒计时已生成', bool(q("SELECT deadline FROM emergency_purchases WHERE id=?", (E1,))[0]['deadline']),
        q("SELECT deadline,status FROM emergency_purchases WHERE id=?", (E1,))[0])

    # ---- ⑤ 补资料 → 正式审批 → 财务复核(转正审批关闭时直连待转正) ----
    _docs = {k: ['d_%s.pdf' % k] for k in ('formal_request', 'compare_record', 'contract_or_order', 'invoice', 'temp_approve_shot', 'situation_note')}
    st, b = api('/api/emergency/%d/formal-docs' % E1, {'docs': _docs, 'linked_order_no': TAG + '-PO', 'linked_contract_no': TAG + '-HT'})
    chk('资料补齐 → 提交正式分级审批', st == 200, emsg(b))
    api('/api/approvals/emergency_formal/%d/approve' % E1, {'action': 'approved', 'comment': '同意', 'signature': 'x'})
    chk('正式审批通过 → 自动发起财务复核', q("SELECT status FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '财务复核'
        and len(q("SELECT 1 FROM approval_instances WHERE biz_type='emergency_finance' AND biz_id=?", (E1,))) >= 1)
    api('/api/approvals/emergency_finance/%d/approve' % E1, {'action': 'approved', 'comment': '资料完整', 'signature': 'x'})
    chk('财务复核通过(转正审批关闭) → 待转正', q("SELECT status FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '待转正')

    # ---- ⑥ 转正闭环 → 临时入库转正式 + 解锁付款 ----
    st, b = api('/api/emergency/%d/convert' % E1, {})
    chk('转正闭环成功', st == 200, emsg(b))
    chk('标签已闭环 + 临时入库单转正式入库', q("SELECT status,label FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '已闭环'
        and q("SELECT is_emg,is_emg_converted FROM receivings WHERE id=?", (RID,))[0] == {'is_emg': 0, 'is_emg_converted': 1})

    # ---- ⑦ 转正审批节点开关: 开启后 财务复核 → 转正审批 → 待转正 ----
    d2 = j(api('/api/emergency/flow-config')[1])
    body = {'limits': d2['limits'], 'chains': d2['chains'], 'convert': {'need': '1', 'role': '分管领导', 'approver': ''}}
    body['chains'] = {bt: {'levels': [{'level_no': i + 1, 'min_amount': x['min_amount'], 'max_amount': x['max_amount'],
                                       'role': x['role'], 'approver': x['approver'], 'label': x['label']}
                                      for i, x in enumerate(v['levels'])]} for bt, v in d2['chains'].items()}
    st, b = api('/api/emergency/flow-config', body)
    chk('开启转正审批节点(配置保存)', st == 200, emsg(b))
    chk('转正审批链已生成', len(q("SELECT 1 FROM approval_flow_config WHERE biz_type='emergency_convert'")) >= 1)
    st, b = api('/api/emergency', {'project': TAG + '工地2', 'item_name': TAG + '料2', 'quantity': 1, 'est_amount': 1000,
                                   'need_arrive': '明天', 'reason': '测试转正审批节点'})
    E2 = j(b).get('id'); EIDS.append(E2)
    api('/api/approvals/emergency_temp/%d/approve' % E2, {'action': 'approved', 'comment': 'ok', 'signature': 'x'})
    api('/api/emergency/%d/supplier' % E2, {'supplier': TAG + '供应商2', 'quote_amt': 1000, 'no_compare': True, 'no_compare_reason': 'x'})
    api('/api/emergency/%d/temp-stock' % E2, {'qty': 1, 'warehouse': '主库房', 'attachments': ['a.jpg', 'b.jpg']})
    api('/api/emergency/%d/formal-docs' % E2, {'docs': _docs, 'linked_order_no': TAG + '-PO2'})
    api('/api/approvals/emergency_formal/%d/approve' % E2, {'action': 'approved', 'comment': 'ok', 'signature': 'x'})
    api('/api/approvals/emergency_finance/%d/approve' % E2, {'action': 'approved', 'comment': 'ok', 'signature': 'x'})
    chk('开启后: 财务复核通过 → 进入转正审批节点', q("SELECT status FROM emergency_purchases WHERE id=?", (E2,))[0]['status'] == '转正审批中'
        and len(q("SELECT 1 FROM approval_instances WHERE biz_type='emergency_convert' AND biz_id=?", (E2,))) >= 1,
        q("SELECT status FROM emergency_purchases WHERE id=?", (E2,))[0])
    api('/api/approvals/emergency_convert/%d/approve' % E2, {'action': 'approved', 'comment': '同意转正', 'signature': 'x'})
    chk('转正审批通过 → 待转正 → 可转正闭环', q("SELECT status FROM emergency_purchases WHERE id=?", (E2,))[0]['status'] == '待转正'
        and api('/api/emergency/%d/convert' % E2, {})[0] == 200)
    # 关闭转正审批节点(恢复默认)
    d3 = j(api('/api/emergency/flow-config')[1])
    body['chains'] = {bt: {'levels': [{'level_no': i + 1, 'min_amount': x['min_amount'], 'max_amount': x['max_amount'],
                                      'role': x['role'], 'approver': x['approver'], 'label': x['label']}
                                     for i, x in enumerate(v['levels'])]} for bt, v in d3['chains'].items()}
    body['convert'] = {'need': '0', 'role': '分管领导', 'approver': ''}
    api('/api/emergency/flow-config', body)
    chk('转正审批节点已恢复关闭', (j(api('/api/emergency/flow-config')[1])['convert'].get('need') != '1'))
    # 审批人绑定仍在
    d4 = j(api('/api/emergency/flow-config')[1])
    chk('恢复后 临时审批一级审批人仍绑定温丽', (d4['chains']['emergency_temp']['levels'][0]['approver'] or '') == 'admin')
finally:
    print('\n--- 清理测试数据 ---')
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    for _i in EIDS:
        if not _i:
            continue
        c.execute("DELETE FROM emergency_logs WHERE emg_id=?", (_i,))
        for _b in ('emergency_temp', 'emergency_formal', 'emergency_finance', 'emergency_extend', 'emergency_convert'):
            for _t in ('approval_instances', 'dingtalk_instances', 'approval_action_logs'):
                c.execute("DELETE FROM %s WHERE biz_type=? AND biz_id=?" % _t, (_b, _i))
        c.execute("DELETE FROM notifications WHERE biz_type='emergency' AND biz_id=?", (_i,))
        c.execute("DELETE FROM alert_items WHERE biz_type='emergency' AND biz_id=?", (_i,))
    c.execute("DELETE FROM emergency_purchases WHERE project LIKE ? OR item_name LIKE ?", (TAG + '%', TAG + '%'))
    for r in c.execute("SELECT id FROM receivings WHERE item_name LIKE ?", (TAG + '%',)).fetchall():
        c.execute("DELETE FROM inventory_flows WHERE doc_type='receiving' AND doc_id=?", (r[0],))
        c.execute("DELETE FROM receivings WHERE id=?", (r[0],))
    c.execute("DELETE FROM inventory WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM inventory_flows WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM payment_requests WHERE supplier LIKE ?", (TAG + '%',))
    c.execute("""DELETE FROM approval_instances WHERE biz_type='payment' AND status='pending'
                  AND biz_id NOT IN (SELECT id FROM payment_requests) AND substr(created_at,1,10)=date('now','localtime')""")
    c.execute("DELETE FROM reminder_log WHERE rule LIKE 'emg%'")
    if _cv_old is not None:
        pass
    c.execute("UPDATE sys_config SET value=? WHERE key='dingtalk_enabled'", (_old or '1',))
    c.commit()
    _l1 = c.execute("SELECT COUNT(*) FROM emergency_purchases WHERE project LIKE ? OR item_name LIKE ?", (TAG + '%', TAG + '%')).fetchone()[0]
    _l2 = c.execute("SELECT COUNT(*) FROM inventory WHERE item_name LIKE ?", (TAG + '%',)).fetchone()[0]
    _pend = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
    c.close()
    print('  残留 应急单=%d 库存=%d | 待审批实例=%d' % (_l1, _l2, _pend))

print('\n=== 结果: 通过 %d, 失败 %d ===' % (len(ok), len(bad)))
for x in bad:
    print('  - 失败:', x)
sys.exit(1 if bad else 0)
