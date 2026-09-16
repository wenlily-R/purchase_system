# -*- coding: utf-8 -*-
"""V11.325 分批次入库 + 三级库房/指定库房入库 端到端验证(本机 5899)
样例: 订单9个 → 第1批入4 部分入库 → 新增批次入库申请 入剩余5 → 全部入库完成
"""
import json, sqlite3, sys, time, urllib.request, urllib.error, http.cookiejar, os

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'purchase.db')
DB = os.path.abspath(DB)
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
OK, BAD = [], []


def api(path, data=None, method=None, raw=False):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    last = None
    for _ in range(4):
        try:
            req = urllib.request.Request(BASE + path, data=body, method=method or ('POST' if data is not None else 'GET'),
                                         headers={'Content-Type': 'application/json'})
            r = op.open(req, timeout=60)
            b = r.read()
            return r.status, (b if raw else b.decode('utf-8', 'replace'))
        except urllib.error.HTTPError as e:
            b = e.read()
            return e.code, (b if raw else b.decode('utf-8', 'replace'))
        except Exception as e:   # 看门狗检测到新脚本会自动重启服务 → 重试
            last = e
            time.sleep(4)
    raise last


def msg(b):
    """取接口返回的错误/提示文案(已解码)"""
    try:
        j = json.loads(b)
        return str(j.get('error') or j.get('message') or j.get('success'))
    except Exception:
        return str(b)[:200]


def chk(name, cond, extra=''):
    (OK if cond else BAD).append(name)
    print(('  ✅ ' if cond else '  ❌ ') + name + (('  | ' + str(extra)[:200]) if extra else ''))


def q(sql, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = [dict(x) for x in c.execute(sql, args).fetchall()]; c.close(); return r


def ex(sql, args=()):
    c = sqlite3.connect(DB)
    c.execute(sql, args); c.commit(); c.close()


print('=== V11.325 端到端验证 ===')
st, body = api('/api/login', {'username': 'admin', 'password': 'admin123'})
chk('登录 admin', st == 200, body[:120])

# 关钉钉推送(避免测试审批推到真实手机)
_old_dd = q("SELECT value FROM sys_config WHERE key='dingtalk_enabled'")
_old_dd = (_old_dd[0]['value'] if _old_dd else '1')
ex("UPDATE sys_config SET value='0' WHERE key='dingtalk_enabled'")

TAG = '【测试】V11325'
OID = OIT = RID1 = RID2 = None
try:
    # ---- 造测试订单: 总数 9 ----
    c = sqlite3.connect(DB)
    cur = c.execute("""INSERT INTO purchase_orders(order_no,item_name,spec,quantity,unit,price,tax_rate,status,supplier,trade_mode,rcv_state,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),datetime('now','localtime'))""",
                    (TAG + '-9', TAG + '物资', '规格A', 9, '件', 10.0, 13, '已通过', '测试供应商', '货到付款', ''))
    OID = cur.lastrowid
    cur = c.execute("""INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,tax_rate,created_at)
                       VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))""", (OID, TAG + '物资', '规格A', '件', 9, 10.0, 13))
    OIT = cur.lastrowid
    c.commit(); c.close()
    print('测试订单 id=%s 数量=9' % OID)

    # ---- 1. 目标仓库必填校验 ----
    st, b = api('/api/orders/%d/receiving-batch' % OID, {'items': [{'quantity': 4}], 'is_est': 1})
    chk('未选目标仓库被拦截(必填)', st == 400 and '目标仓库' in msg(b), msg(b))

    # ---- 2. 第1批入库 4 个(指定库房/库区/库位) ----
    st, b = api('/api/orders/%d/receiving-batch' % OID,
                {'items': [{'quantity': 4}], 'is_est': 1, 'warehouse': '生产库房',
                 'zone': '待验区', 'location': 'A区-01货架-01层'})
    chk('第1批入库提交成功(数量4)', st == 200 and json.loads(b).get('success'), b)
    j1 = json.loads(b) if st == 200 else {}
    RID1 = j1.get('id')
    chk('自动生成批次号=第1批', j1.get('batch_no') == '第1批', j1.get('batch_no'))
    r1 = q("SELECT receive_no,warehouse,zone,location,batch_no,status,data_source,quantity FROM receivings WHERE id=?", (RID1,))
    chk('入库单记录库房/库区/库位', r1 and r1[0]['warehouse'] == '生产库房' and r1[0]['zone'] == '待验区' and r1[0]['location'] == 'A区-01货架-01层',
        r1)

    # ---- 3. 未审批前: 单据列表带 订单总量/已入/剩余 ----
    st, b = api('/api/receivings?rcv_state=')
    rows = json.loads(b)
    my = [x for x in rows if x['id'] == RID1]
    chk('列表返回该批单', len(my) == 1)
    chk('列表带 订单总量9/已入0/剩余9', my and my[0].get('order_total_qty') == 9.0 and my[0].get('order_pending_qty') == 9.0,
        my[0].get('order_total_qty') if my else None)
    chk('未入库时不给【部分入库】标签(还没实际入库)', my and not my[0].get('rcv_state_label'), my[0].get('rcv_state_label') if my else None)

    # ---- 4. 审批通过第1批 → 部分入库 ----
    for _ in range(4):
        st, b = api('/api/approvals/receiving/%d/approve' % RID1, {'action': 'approved', 'comment': '测试审批', 'signature': '测试'})
        print('    审批 ->', st, b[:120])
        if st != 200:
            break
        pend = q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type='receiving' AND biz_id=? AND status='pending'", (RID1,))
        if not pend[0]['n']:
            break
    r1 = q("SELECT status,completed_at FROM receivings WHERE id=?", (RID1,))
    chk('第1批状态=已入库', r1 and r1[0]['status'] == '已入库', r1)
    inv = q("SELECT item_name,warehouse,zone,location,quantity,price,data_source FROM inventory WHERE item_name=?", (TAG + '物资',))
    chk('库存落到「生产库房/待验区/A区-01货架-01层」数量4',
        len(inv) == 1 and inv[0]['warehouse'] == '生产库房' and inv[0]['zone'] == '待验区'
        and inv[0]['location'] == 'A区-01货架-01层' and inv[0]['quantity'] == 4, inv)
    po = q("SELECT status,rcv_state FROM purchase_orders WHERE id=?", (OID,))
    chk('订单状态=部分到货，待继续验收', po and po[0]['status'] == '部分到货，待继续验收', po)
    chk('订单到货状态=部分到货', po and po[0]['rcv_state'] == '部分到货', po)

    st, b = api('/api/receivings')
    my = [x for x in json.loads(b) if x['id'] == RID1]
    chk('单据状态标签=部分入库', my and my[0].get('rcv_state_label') == '部分入库', my[0].get('rcv_state_label') if my else None)
    chk('列表 已入4/剩余5', my and my[0].get('order_accepted_qty') == 4.0 and my[0].get('order_pending_qty') == 5.0,
        (my[0].get('order_accepted_qty'), my[0].get('order_pending_qty')) if my else None)

    # ---- 5. 批次台账接口 ----
    st, b = api('/api/receivings/%d/batches' % RID1)
    bd = json.loads(b)
    chk('批次台账: 总9/已入4/剩余5/1批', bd.get('order_total') == 9 and bd.get('accepted') == 4 and bd.get('pending') == 5 and len(bd.get('rows', [])) == 1, bd.get('state_label'))

    # ---- 6. 超量拦截 ----
    st, b = api('/api/orders/%d/receiving-batch' % OID, {'items': [{'quantity': 6}], 'is_est': 1, 'warehouse': '生产库房'})
    chk('超量提交被拦截(剩余5, 提交6)', st == 400 and '超过' in msg(b), msg(b))

    # ---- 7. 第2批入库剩余5(换库房: 生活库房/合格区) ----
    st, b = api('/api/orders/%d/receiving-batch' % OID,
                {'items': [{'quantity': 5}], 'is_est': 1, 'warehouse': '生活库房', 'zone': '合格区', 'location': ''})
    chk('第2批入库提交成功(数量5)', st == 200 and json.loads(b).get('success'), b)
    RID2 = json.loads(b).get('id') if st == 200 else None
    chk('批次号=第2批', json.loads(b).get('batch_no') == '第2批' if st == 200 else False)
    for _ in range(4):
        st, b = api('/api/approvals/receiving/%d/approve' % RID2, {'action': 'approved', 'comment': '测试审批', 'signature': '测试'})
        if st != 200:
            print('    第2批审批 ->', st, b[:120]); break
        pend = q("SELECT COUNT(*) n FROM approval_instances WHERE biz_type='receiving' AND biz_id=? AND status='pending'", (RID2,))
        if not pend[0]['n']:
            break
    inv2 = q("SELECT warehouse,zone,quantity FROM inventory WHERE item_name=? AND warehouse='生活库房'", (TAG + '物资',))
    chk('第2批库存落到「生活库房/合格区」数量5', len(inv2) == 1 and inv2[0]['zone'] == '合格区' and inv2[0]['quantity'] == 5, inv2)
    po = q("SELECT status,rcv_state FROM purchase_orders WHERE id=?", (OID,))
    chk('全部入库后订单状态=全部已验收', po and po[0]['status'] == '全部已验收', po)
    chk('到货状态=全部到货', po and po[0]['rcv_state'] == '全部到货', po)
    st, b = api('/api/receivings')
    my = [x for x in json.loads(b) if x['id'] == RID1]
    chk('单据状态标签=全部入库完成', my and my[0].get('rcv_state_label') == '全部入库完成', my[0].get('rcv_state_label') if my else None)

    # ---- 8. 剩余为0后不能再新增批次 ----
    st, b = api('/api/orders/%d/receiving-batch' % OID, {'items': [{'quantity': 1}], 'is_est': 1, 'warehouse': '生产库房'})
    chk('全部入库后新增批次被拦截', st == 400, b)

    # ---- 9. 列表按 部分入库/全部入库完成 筛选 ----
    st, b = api('/api/receivings?rcv_state=' + urllib.parse.quote('全部入库完成'))
    rows = json.loads(b)
    chk('筛选「全部入库完成」命中该订单', any(x['id'] == RID1 for x in rows), len(rows))
    st, b = api('/api/receivings?rcv_state=' + urllib.parse.quote('部分入库'))
    rowsp = json.loads(b)
    chk('筛选「部分入库」不含已完成的单', not any(x['id'] in (RID1, RID2) for x in rowsp), len(rowsp))

    # ---- 10. 批次记录导出 ----
    st, b = api('/api/export?type=rcv_batches&rid=%d' % RID1, raw=True)
    chk('批次记录可导出Excel(xlsx签名)', st == 200 and b[:2] == b'PK' and len(b) > 3000, (st, len(b)))

    # ---- 11. 三级库房 树/选项 ----
    st, b = api('/api/warehouse/tree')
    tr = json.loads(b)
    chk('库房树一级=7个仓库', len(tr.get('tree', [])) == 7, [x['name'] for x in tr.get('tree', [])])
    chk('每个仓库下含库区(待验区/合格区/不合格区)',
        all(any(z['name'] == '待验区' for z in w['children']) and any(z['name'] == '合格区' for z in w['children']) and any(z['name'] == '不合格区' for z in w['children']) for w in tr['tree']))
    chk('库位三级存在(合格区下)', any(l['name'].startswith('A区-01货架') for w in tr['tree'] for z in w['children'] for l in z['children']))
    _ren = [w for w in tr['tree'] if w['name'] == '生产库房'][0]
    chk('仓库节点显示库存统计(生产库房 4件)', _ren['stats']['qty'] == 4, _ren['stats'])
    st, b = api('/api/warehouse/tree?q=' + urllib.parse.quote('生活'))
    chk('库房搜索(生活)命中', len(json.loads(b)['tree']) == 1 and json.loads(b)['tree'][0]['name'] == '生活库房')
    st, b = api('/api/warehouse/options')
    op_ = json.loads(b)
    chk('入库联动选项: 7仓库且带库区/库位', len(op_['warehouses']) == 7 and len(op_['warehouses'][0]['zones']) == 3
        and any(z['locations'] for z in op_['warehouses'][0]['zones']))

    # ---- 12. 库房节点 增/排序/停用/删除 ----
    st, b = api('/api/warehouse/node', {'parent_id': 0, 'name': '【测试】临时仓', 'code': 'TST', 'color_tag': 'pending'})
    j = json.loads(b)
    chk('新增一级仓库成功', st == 200 and j.get('success'), b)
    NID = j.get('id')
    st, b = api('/api/warehouse/node', {'parent_id': NID, 'name': 'A货架区'})
    ZID = json.loads(b).get('id')
    chk('新增二级库区成功', st == 200 and json.loads(b).get('level') == 2, b)
    st, b = api('/api/warehouse/node/%d/move' % NID, {'dir': 'top'})
    chk('排序(置顶)成功', st == 200 and json.loads(b).get('success'), b)
    st, b = api('/api/warehouse/node/%d/toggle' % NID, {})
    chk('停用仓库成功', st == 200 and json.loads(b).get('status') == '停用', b)
    st, b = api('/api/warehouse/options')
    _names = [x['name'] for x in json.loads(b)['warehouses']]
    chk('停用后不进业务下拉', '【测试】临时仓' not in _names, _names)
    st, b = api('/api/warehouse/node/%d/toggle' % NID, {})
    st, b = api('/api/warehouse/node/%d' % ZID, method='DELETE')
    chk('删除空库区成功', st == 200, b)
    st, b = api('/api/warehouse/node/%d' % NID, method='DELETE')
    chk('删除空仓库成功', st == 200, b)

    # ---- 13. 历史导入数据隔离 ----
    c = sqlite3.connect(DB)
    cur = c.execute("""INSERT INTO receivings(receive_no,item_name,quantity,unit,status,dept,data_source,created_at)
                       VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))""",
                    (TAG + '-QC', TAG + '历史物资', 5, '个', '已入库', '期初建账', '历史导入'))
    HID = cur.lastrowid; c.commit(); c.close()
    st, b = api('/api/receivings')
    chk('历史导入单默认不显示', not any(x['id'] == HID for x in json.loads(b)))
    st, b = api('/api/receivings?hist=1')
    _h = [x for x in json.loads(b) if x['id'] == HID]
    chk('勾选「显示历史归档数据」后可查', len(_h) == 1 and _h[0].get('data_source') == '历史导入', _h[:1])
    # ---- 14. 历史导入数据只读(后端强制) ----
    st, b = api('/api/docs/receiving/%d/update' % HID, {'remark': '试图改历史数据'})
    chk('历史入库单不可修改', st == 400 and '只读' in msg(b), msg(b))
    st, b = api('/api/docs/receiving/%d/delete' % HID, {'confirm': 1})
    chk('历史入库单不可删除', st == 400 and '只读' in msg(b), msg(b))
    st, b = api('/api/receivings/%d/complete' % HID, {'qualified_qty': 5})
    chk('历史入库单不可提交审批', st == 400 and '只读' in msg(b), msg(b))
    c = sqlite3.connect(DB)
    cur = c.execute("""INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,data_source,updated_at)
                       VALUES(?,?,?,?,?,?,?,datetime('now','localtime'))""",
                    (TAG + '历史库存', '', '个', 7, '主库房', 3.5, '历史导入'))
    HINV = cur.lastrowid; c.commit(); c.close()
    st, b = api('/api/docs/inventory/%d/update' % HINV, {'quantity': 99})
    chk('历史库存条目不可修改', st == 400 and '只读' in msg(b), msg(b))
    # ---- 15. 库存查询 数据来源/库区 筛选 ----
    st, b = api('/api/inventory?src=hist')
    chk('库存「仅历史导入」筛选命中', any(x['id'] == HINV for x in json.loads(b)))
    st, b = api('/api/inventory?src=sys')
    chk('库存「仅系统新增」不命中历史条目', not any(x['id'] == HINV for x in json.loads(b)))
    st, b = api('/api/inventory?zone=' + urllib.parse.quote('待验区'))
    chk('库存按库区筛选可用', st == 200)

finally:
    # ---- 清理 ----
    print('\n--- 清理测试数据 ---')
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    _r = [dict(x) for x in c.execute("SELECT id FROM receivings WHERE item_name LIKE ? OR receive_no LIKE ?", (TAG + '%', TAG + '%')).fetchall()]
    for x in _r:
        c.execute("DELETE FROM inventory_flows WHERE doc_type='receiving' AND doc_id=?", (x['id'],))
        c.execute("DELETE FROM approval_instances WHERE biz_type='receiving' AND biz_id=?", (x['id'],))
        c.execute("DELETE FROM dingtalk_instances WHERE biz_type='receiving' AND biz_id=?", (x['id'],))
        c.execute("DELETE FROM approval_action_logs WHERE biz_type='receiving' AND biz_id=?", (x['id'],))
        c.execute("DELETE FROM receivings WHERE id=?", (x['id'],))
    c.execute("UPDATE inventory_flows SET doc_id=0 WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM inventory_flows WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM inventory WHERE item_name LIKE ?", (TAG + '%',))
    c.execute("DELETE FROM material_images WHERE item_name LIKE ?", (TAG + '%',))
    if OID:
        c.execute("DELETE FROM order_items WHERE order_id=?", (OID,))
        c.execute("DELETE FROM purchase_orders WHERE id=?", (OID,))
    c.execute("DELETE FROM warehouse_nodes WHERE name LIKE '【测试】%'")
    c.execute("UPDATE sys_config SET value=? WHERE key='dingtalk_enabled'", (_old_dd,))
    c.commit()
    _left = c.execute("SELECT COUNT(*) FROM receivings WHERE item_name LIKE ?", (TAG + '%',)).fetchone()[0]
    _lefti = c.execute("SELECT COUNT(*) FROM inventory WHERE item_name LIKE ?", (TAG + '%',)).fetchone()[0]
    _leftn = c.execute("SELECT COUNT(*) FROM warehouse_nodes WHERE name LIKE '【测试】%'").fetchone()[0]
    _pend = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
    c.close()
    print('  残留 单据=%d 库存=%d 库房节点=%d | 待审批实例=%d | 钉钉开关已还原=%s' % (_left, _lefti, _leftn, _pend, _old_dd))

print('\n=== 结果: 通过 %d 项, 失败 %d 项 ===' % (len(OK), len(BAD)))
if BAD:
    print('失败项:')
    for x in BAD:
        print('  -', x)
    sys.exit(1)
print('✅ 全部通过')
