# -*- coding: utf-8 -*-
"""V11.327 应急紧急采购 端到端验证(本机 5899)
覆盖: 发起/阈值B拦截/拆分拦截/临时审批(金额分级)/接单/临时入库(立即入账可领料)/未转正禁付款
      /超期锁单(禁领料禁付款禁转正)/延期1次解锁/补资料6类/正式分级审批/财务复核/转正闭环/台账月报导出/频次预警
"""
import json, os, sqlite3, sys, time, urllib.request, urllib.error, http.cookiejar, urllib.parse as up

BASE = 'http://127.0.0.1:5899'
DB = os.path.abspath(r'C:\Users\35322\Desktop\purchase_system\data\purchase.db')
TAG = '【测试】EMG327'
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
ok, bad = [], []


def api(p, d=None, method=None, raw=False):
    last = None
    for _ in range(3):
        try:
            r = op.open(urllib.request.Request(BASE + p, data=json.dumps(d).encode() if d is not None else None,
                                               method=method or ('POST' if d is not None else 'GET'),
                                               headers={'Content-Type': 'application/json'}), timeout=60)
            b = r.read()
            return r.status, (b if raw else b.decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            b = e.read()
            return e.code, (b if raw else b.decode('utf-8', 'replace'))
        except Exception as e:
            last = e; time.sleep(4)
    raise last


def j(b):
    try:
        return json.loads(b)
    except Exception:
        return {}


def emsg(b):
    x = j(b)
    return str(x.get('error') or x.get('message') or x.get('success') or b)[:160]


def chk(n, c, x=''):
    (ok if c else bad).append(n)
    print(('  ✅ ' if c else '  ❌ ') + n + (('  | ' + str(x)[:150]) if x else ''))


def q(sql, a=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = [dict(x) for x in c.execute(sql, a).fetchall()]; c.close(); return r


def ex(sql, a=()):
    c = sqlite3.connect(DB); c.execute(sql, a); c.commit(); c.close()


def new_emg(project, name, amt, qty=5, extra=None):
    b = {'project': project, 'item_name': name, 'quantity': qty, 'unit': '个', 'est_amount': amt,
         'need_arrive': '今天下午16:00前', 'reason': '救援急用停工待料'}
    if extra:
        b.update(extra)
    st, r = api('/api/emergency', b)
    return st, j(r)


def approve(biz, bid, comment='测试审批'):
    for _ in range(5):
        st, b = api('/api/approvals/%s/%d/approve' % (biz, bid), {'action': 'approved', 'comment': comment, 'signature': '测试'})
        if st != 200:
            return st, b
        pend = q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending'", (biz, bid))[0]['n']
        if not pend:
            break
    return 200, b'{"success":true}'


print('=== V11.327 应急紧急采购 端到端验证 ===')
_old = q("SELECT value FROM sys_config WHERE key='dingtalk_enabled'")
_old = _old[0]['value'] if _old else '1'
ex("UPDATE sys_config SET value='0' WHERE key='dingtalk_enabled'")
st, b = api('/api/login', {'username': 'admin', 'password': 'admin123'})
chk('登录 admin', st == 200)

EIDS = []
try:
    meta = j(api('/api/emergency/meta')[1])
    chk('元数据接口: 阈值A/B/工作日', meta.get('limit_a') == 5000 and meta.get('limit_b') == 20000 and meta.get('promise_days') == 3, meta.get('limit_b'))

    # ---- 1. 阈值B拦截 ----
    st, r = new_emg(TAG + 'A工地', TAG + '物资A', 25000)
    chk('预估金额 > B 禁用应急通道', st == 400 and '上限' in str(r.get('error')), r.get('error'))

    # ---- 2. 拆分拦截(同项目同物资7天合计>B) ----
    st, r1 = new_emg(TAG + 'B工地', TAG + '拆分物资', 12000); EIDS.append(r1.get('id'))
    st2, r2 = new_emg(TAG + 'B工地', TAG + '拆分物资', 12000)
    chk('拆分订单规避金额管控被拦截', st2 == 400 and '拆分' in str(r2.get('error')), r2.get('error'))

    # ---- 3. 发起(普通档 ≤A, 1级临时审批) + 大额档(2级) ----
    st, r3 = new_emg(TAG + 'C工地', TAG + '急用料C', 3000, 5); EIDS.append(r3.get('id'))
    chk('发起应急申请成功(YJ编号)', st == 200 and str(r3.get('emg_no', '')).startswith('YJ-'), r3.get('emg_no'))
    E1 = r3.get('id')
    lv1 = q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type='emergency_temp' AND biz_id=?", (E1,))[0]['n']
    chk('≤¥5000: 临时审批仅1级(部门负责人)', lv1 == 1, lv1)
    chk('单据标签=应急采购 - 待转正', q("SELECT label FROM emergency_purchases WHERE id=?", (E1,))[0]['label'] == '应急采购 - 待转正')
    st, r4 = new_emg(TAG + 'C工地', TAG + '急用料D', 9000, 5); EIDS.append(r4.get('id'))
    lv2 = q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type='emergency_temp' AND biz_id=?", (r4['id'],))[0]['n']
    chk('¥5000~¥20000: 临时审批2级(部门+事业部)', lv2 == 2, lv2)

    # ---- 4. 临时审批通过 → 采购接单 ----
    approve('emergency_temp', E1)
    st1 = q("SELECT status,temp_approved_by FROM emergency_purchases WHERE id=?", (E1,))[0]
    chk('临时审批通过 → 状态=采购接单', st1['status'] == '采购接单' and st1['temp_approved_by'], st1)

    # ---- 5. 接单: 必填/无法多比价理由校验 + 价格基准偏差 ----
    st, b = api('/api/emergency/%d/supplier' % E1, {'supplier': TAG + '供应商', 'quote_amt': 3000})
    chk('未传比价资料且未勾选无法多比价 → 拦截', st == 400 and '比价' in emsg(b), emsg(b))
    st, b = api('/api/emergency/%d/supplier' % E1, {'supplier': TAG + '供应商', 'quote_amt': 3000, 'no_compare': True})
    chk('勾选无法多比价但未填理由 → 拦截', st == 400 and '理由' in emsg(b), emsg(b))
    # 造历史价(该物资近3次采购均价=100/单位) → 报价 150/单位 超基准20%
    c = sqlite3.connect(DB)
    _po_ids = []
    for _i in range(3):
        cur = c.execute("""INSERT INTO purchase_orders(order_no,item_name,spec,quantity,unit,price,status,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,datetime('now','localtime'),datetime('now','localtime'))""",
                        (TAG + '-PO%d' % _i, TAG + '急用料C', '规格C', 1, '个', 100.0, '已通过'))
        _po_ids.append(cur.lastrowid)
    c.commit(); c.close()
    st, b = api('/api/emergency/%d/supplier' % E1, {'supplier': TAG + '供应商', 'quote_amt': 750, 'no_compare': True, 'no_compare_reason': '抢险仅一家现货'})
    _dd = j(b)
    chk('接单成功并算出价格偏差(基准¥100/单位)', st == 200 and abs(float(_dd.get('price_dev_pct') or 0) - 50.0) < 0.01, _dd)
    chk('价格超基准 → 列表标价格异常', any(x['id'] == E1 and x.get('price_warn') for x in j(api('/api/emergency')[1])['rows']))

    # ---- 6. 临时入库(≥2附件) → 立即入账 + 倒计时 ----
    st, b = api('/api/emergency/%d/temp-stock' % E1, {'qty': 5, 'warehouse': '生产库房', 'zone': '待验区', 'attachments': ['tmp_a.jpg']})
    chk('临时入库附件不足2个 → 拦截', st == 400 and '送货单' in emsg(b), emsg(b))
    st, b = api('/api/emergency/%d/temp-stock' % E1, {'qty': 5, 'warehouse': '生产库房', 'zone': '待验区',
                                                  'location': 'A区-01货架-01层', 'attachments': ['tmp_a.jpg', 'tmp_b.jpg']})
    _tr = j(b)
    chk('临时入库成功并生成临时入库单', st == 200 and str(_tr.get('receive_no', '')).startswith('RK-'), _tr.get('message'))
    RID = _tr.get('receive_id')
    _inv = q("SELECT * FROM inventory WHERE item_name=? AND warehouse='生产库房'", (TAG + '急用料C',))
    chk('库存立即入账(生产库房5件, 库区=待验区)', len(_inv) == 1 and _inv[0]['quantity'] == 5 and _inv[0]['zone'] == '待验区', _inv)
    _rv = q("SELECT is_emg,emg_no,status FROM receivings WHERE id=?", (RID,))[0]
    chk('入库单标记为应急临时入库(待转正)', _rv['is_emg'] == 1 and _rv['emg_no'].startswith('YJ-'), _rv)
    _e = q("SELECT status,deadline FROM emergency_purchases WHERE id=?", (E1,))[0]
    chk('状态=待补资料且生成3工作日截止日', _e['status'] == '待补资料' and bool(_e['deadline']), _e)

    # ---- 7. 未转正禁止付款 ----
    st, b = api('/api/payments', {'supplier': TAG + '供应商', 'amount': 750, 'payment_reason': '应急采购付款测试',
                                  'expect_pay_date': '2026-09-30', 'payee_name': TAG + '供应商', 'payee_account': '6222001'})
    chk('未转正应急单 → 禁止付款', st == 400 and '应急' in emsg(b), emsg(b))

    # ---- 8. 价格超基准: 补资料必须填价格说明 ----
    _docs = {k: ['tmp_%s.pdf' % k] for k in ('formal_request', 'compare_record', 'contract_or_order', 'invoice', 'temp_approve_shot', 'situation_note')}
    st, b = api('/api/emergency/%d/formal-docs' % E1, {'docs': _docs})
    chk('价格超基准未填说明 → 拦截', st == 400 and '价格说明' in emsg(b), emsg(b))
    st, b = api('/api/emergency/%d/formal-docs' % E1, {'docs': {k: v for k, v in _docs.items() if k != 'invoice'}, 'price_note': '抢险加急溢价'})
    chk('资料不齐(缺发票) → 拦截', st == 400 and '缺少' in emsg(b), emsg(b))

    # ---- 9. 超期锁单: 把截止日改到过去 → 巡检锁单 ----
    ex("UPDATE emergency_purchases SET deadline=? WHERE id=?", ((time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400 * 6))), E1))
    api('/api/emergency/sweep', {})
    _lk = q("SELECT status,label,lock_reason FROM emergency_purchases WHERE id=?", (E1,))[0]
    chk('超期未补资料 → 自动锁单(标签已锁定)', _lk['status'] == '已锁定' and '已锁定' in _lk['label'], _lk)
    st, b = api('/api/requisitions', {'items': [{'item_name': TAG + '急用料C', 'spec': '规格C', 'quantity': 1, 'receiver': '温丽', 'purpose': '测试领料'}],
                                      'dept': '综合办'})
    chk('已锁单 → 禁止新增领料', st == 400 and '锁定' in emsg(b), emsg(b))
    st, b = api('/api/emergency/%d/formal-docs' % E1, {'docs': _docs, 'price_note': '抢险加急溢价'})
    chk('已锁单 → 禁止转正(补资料被拦)', st == 400 and '锁定' in emsg(b), emsg(b))
    chk('锁单进入预警中心', q("SELECT COUNT(*) n FROM alert_items WHERE alert_type='emg_locked' AND biz_id=?", (E1,))[0]['n'] >= 1)
    st, b = api('/api/payments', {'supplier': TAG + '供应商', 'amount': 750, 'payment_reason': '应急采购付款测试',
                                  'expect_pay_date': '2026-09-30', 'payee_name': TAG + '供应商', 'payee_account': '6222001'})
    chk('锁单状态仍禁止付款', st == 400, emsg(b))

    # ---- 10. 延期(仅1次) → 审批通过自动解锁+顺延 ----
    st, b = api('/api/emergency/%d/extend' % E1, {'reason': '供应商发票下周开'})
    chk('提交延期申请成功', st == 200 and '顺延' in emsg(b), emsg(b))
    approve('emergency_extend', E1)
    _ex1 = q("SELECT status,extend_count,deadline FROM emergency_purchases WHERE id=?", (E1,))[0]
    chk('延期通过 → 解锁回待补资料+次数=1', _ex1['status'] == '待补资料' and _ex1['extend_count'] == 1, _ex1)
    st, b = api('/api/emergency/%d/extend' % E1, {'reason': '再延一次试试'})
    chk('仅允许延期1次', st == 400 and '延期过1次' in emsg(b), emsg(b))

    # ---- 11. 补资料 → 正式分级审批 → 财务复核 → 转正 ----
    st, b = api('/api/emergency/%d/formal-docs' % E1, {'docs': _docs, 'price_note': '抢险加急溢价', 'linked_order_no': TAG + '-FPO', 'linked_contract_no': TAG + '-FC'})
    chk('6类资料齐全 → 提交正式分级审批', st == 200 and '正式分级审批' in emsg(b), emsg(b))
    chk('状态=正式审批中', q("SELECT status FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '正式审批中')
    approve('emergency_formal', E1)
    _fs = q("SELECT status,formal_approved_at FROM emergency_purchases WHERE id=?", (E1,))[0]
    chk('正式审批通过 → 财务复核', _fs['status'] == '财务复核', _fs)
    chk('自动创建财务复核审批实例', q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type='emergency_finance' AND biz_id=?", (E1,))[0]['n'] >= 1)
    approve('emergency_finance', E1)
    chk('财务复核通过 → 待转正', q("SELECT status FROM emergency_purchases WHERE id=?", (E1,))[0]['status'] == '待转正')
    st, b = api('/api/emergency/%d/convert' % E1, {})
    _cv = q("SELECT status,label,converted_at FROM emergency_purchases WHERE id=?", (E1,))[0]
    _cr = q("SELECT is_emg,is_emg_converted FROM receivings WHERE id=?", (RID,))[0]
    chk('转正闭环: 标签=已闭环', _cv['status'] == '已闭环' and '已闭环' in _cv['label'], _cv)
    chk('临时入库单转正式入库(is_emg=0/is_emg_converted=1)', _cr['is_emg'] == 0 and _cr['is_emg_converted'] == 1, _cr)
    st, b = api('/api/payments', {'supplier': TAG + '供应商', 'amount': 750, 'payment_reason': '应急采购付款测试',
                                  'expect_pay_date': '2026-09-30', 'payee_name': TAG + '供应商', 'payee_account': '6222001'})
    chk('转正后付款限制解除(不再被应急拦截)', not ('应急' in emsg(b)), (st, emsg(b)))
    st, b = api('/api/requisitions', {'items': [{'item_name': TAG + '急用料C', 'spec': '规格C', 'quantity': 1, 'receiver': '温丽', 'purpose': '测试领料'}], 'dept': '综合办'})
    chk('转正后可正常领料', not ('锁定' in emsg(b)), (st, emsg(b)))

    # ---- 12. 台账/月报/导出/频次预警 ----
    _lg = j(api('/api/emergency/ledger')[1])
    chk('独立台账含本单且字段齐全', any(x['id'] == E1 for x in _lg['rows']) and 'extend_txt' in _lg['rows'][0], _lg['total'])
    st, b = api('/api/emergency/export', raw=True)
    chk('台账可导出Excel', st == 200 and b[:2] == b'PK' and len(b) > 3000, (st, len(b)))
    _rp = j(api('/api/emergency/report?month=' + time.strftime('%Y-%m'))[1])
    chk('月度报表: 单据数/金额/超时清单', _rp.get('count', 0) >= 1 and 'overdue_rows' in _rp, {k: _rp.get(k) for k in ('count', 'amount', 'locked')})
    # 频次预警: 同项目当月≥3次
    _pj = TAG + 'Freq工地'
    for _i in range(3):
        _s, _r = new_emg(_pj, TAG + '频次物资%d' % _i, 1000 + _i)
        EIDS.append(_r.get('id'))
    api('/api/emergency/sweep', {})
    chk('同项目月度≥3次 → 频次预警入预警中心', q("SELECT COUNT(*) n FROM alert_items WHERE alert_type='emg_freq'")[0]['n'] >= 1)
    _rp2 = j(api('/api/emergency/report?month=' + time.strftime('%Y%m'[:0] + time.strftime('%Y-%m')))[1])
    chk('月报频次预警清单命中该项目', any(x['project'] == _pj for x in (_rp2.get('freq_warn') or [])), _rp2.get('freq_warn'))
    # 留痕
    chk('全流程留痕(日志≥8条)', q("SELECT COUNT(*) n FROM emergency_logs WHERE emg_id=?", (E1,))[0]['n'] >= 8,
        q("SELECT COUNT(*) n FROM emergency_logs WHERE emg_id=?", (E1,))[0]['n'])

finally:
    print('\n--- 清理测试数据 ---')
    c = sqlite3.connect(DB)
    _ids = [r[0] for r in c.execute("SELECT id FROM emergency_purchases WHERE project LIKE ? OR item_name LIKE ?", (TAG + '%', TAG + '%')).fetchall()]
    for _i in _ids:
        c.execute("DELETE FROM emergency_logs WHERE emg_id=?", (_i,))
        for _b in ('emergency_temp', 'emergency_formal', 'emergency_finance', 'emergency_extend'):
            c.execute("DELETE FROM approval_instances WHERE biz_type=? AND biz_id=?", (_b, _i))
            c.execute("DELETE FROM dingtalk_instances WHERE biz_type=? AND biz_id=?", (_b, _i))
            c.execute("DELETE FROM approval_action_logs WHERE biz_type=? AND biz_id=?", (_b, _i))
        c.execute("DELETE FROM notifications WHERE biz_type='emergency' AND biz_id=?", (_i,))
        c.execute("DELETE FROM alert_items WHERE biz_type='emergency' AND biz_id=?", (_i,))
    c.execute("DELETE FROM emergency_purchases WHERE project LIKE ? OR item_name LIKE ?", (TAG + '%', TAG + '%'))
    for r in c.execute("SELECT id FROM receivings WHERE emg_no LIKE 'YJ-%' AND item_name LIKE ?", (TAG + '%',)).fetchall():
        c.execute("DELETE FROM inventory_flows WHERE doc_type='receiving' AND doc_id=?", (r[0],))
        c.execute("DELETE FROM receivings WHERE id=?", (r[0],))
    c.execute("DELETE FROM inventory WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM inventory_flows WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM purchase_orders WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM purchase_orders WHERE order_no LIKE ?", (TAG + '%',))
    for _pr in c.execute("SELECT id FROM payment_requests WHERE supplier LIKE ?", (TAG + '%',)).fetchall():
        for _t in ('approval_instances', 'dingtalk_instances', 'approval_action_logs'):
            c.execute("DELETE FROM %s WHERE biz_type='payment' AND biz_id=?" % _t, (_pr[0],))
        c.execute("DELETE FROM notifications WHERE biz_type='payment' AND biz_id=?", (_pr[0],))
    c.execute("DELETE FROM payment_requests WHERE supplier LIKE ?", (TAG + '%',))
    # 清理本次测试产生的孤儿付款审批实例(付款单已删、实例残留; 仅限今天生成, 不动历史数据)
    c.execute("""DELETE FROM approval_instances WHERE biz_type='payment' AND status='pending'
                  AND biz_id NOT IN (SELECT id FROM payment_requests) AND substr(created_at,1,10)=date('now','localtime')""")
    c.execute("DELETE FROM requisitions WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM requisition_items WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM alert_items WHERE alert_type='emg_freq'")
    c.execute("DELETE FROM alert_items WHERE alert_type='emg_locked'")
    c.execute("DELETE FROM reminder_log WHERE rule LIKE 'emg%'")
    c.execute("UPDATE sys_config SET value=? WHERE key='dingtalk_enabled'", (_old or '1',))  # 兜底: 历史值缺失/异常时恢复为开启
    c.commit()
    _l1 = c.execute("SELECT COUNT(*) FROM emergency_purchases WHERE project LIKE ? OR item_name LIKE ?", (TAG + '%', TAG + '%')).fetchone()[0]
    _l2 = c.execute("SELECT COUNT(*) FROM inventory WHERE item_name LIKE ?", (TAG + '%',)).fetchone()[0]
    _l3 = c.execute("SELECT COUNT(*) FROM receivings WHERE item_name LIKE ?", (TAG + '%',)).fetchone()[0]
    _pend = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
    c.close()
    print('  残留 应急单=%d 库存=%d 入库单=%d | 待审批实例=%d' % (_l1, _l2, _l3, _pend))

print('\n=== 结果: 通过 %d, 失败 %d ===' % (len(ok), len(bad)))
for x in bad:
    print('  - 失败:', x)
sys.exit(1 if bad else 0)
