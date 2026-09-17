# -*- coding: utf-8 -*-
"""V11.335 回归: 出库按库房严格扣减(修复实测事故: 应急临时入库的货被常规出库跨库房扣走并叠成 -666 负库存)
覆盖: ①未指定库房时自动落定唯一库房 ②同一物资待审批单占用库存(不能重复开单) ③审批通过按该库房扣减且流水库房与账实一致
      ④临时待分配库的物资不被常规出库跨库扣走(须先分配) ⑤显式指定临时待分配库仍可出库(应急先领用)
      ⑥出库终审前库存复校(不足不予通过, 不产生负库存)
运行: .venv/bin/python tests/test_v11335_req_wh_strict.py   (需本机 5899 服务在跑)
测试数据统一带【测试】V11335 标记, 结束自动清理。
"""
import json, os, sqlite3, sys, time, urllib.request, urllib.error, http.cookiejar

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'purchase.db')
M = '【测试】V11335'
A = M + 'A'          # 普通物资(生产库房)
B = M + 'B'          # 应急临时物资(临时待分配库)
C = M + 'C'          # 复校用(生产库房)
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
ok, bad = [], []


def api(p, d=None, method=None):
    for _ in range(3):
        try:
            r = op.open(urllib.request.Request(BASE + p, data=json.dumps(d).encode() if d is not None else None,
                                               method=method or ('POST' if d is not None else 'GET'),
                                               headers={'Content-Type': 'application/json'}), timeout=60)
            return r.status, json.loads(r.read().decode('utf-8', 'replace') or '{}')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode('utf-8', 'replace') or '{}')
            except Exception:
                return e.code, {}
        except Exception:
            time.sleep(2)
    return 0, {}


def chk(name, cond, extra=None):
    (ok if cond else bad).append(name + ((' | ' + str(extra)) if extra and not cond else ''))
    print(('  ✅ ' if cond else '  ❌ ') + name + ((' | ' + str(extra)) if extra and not cond else ''))


def db():
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row
    return c


def inv_qty(item, wh=''):
    c = db()
    r = c.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory WHERE item_name=? AND (?='' OR warehouse=?)",
                  (item, wh, wh)).fetchone()[0]
    c.close()
    return float(r or 0)


def setup():
    c = db()
    c.execute("DELETE FROM inventory WHERE item_name LIKE ?", (M + '%',))
    c.execute("DELETE FROM requisitions WHERE item_name LIKE ? AND status='待审批'", (M + '%',))
    c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,updated_at) VALUES(?,?,?,?,?,?,datetime('now','localtime'))", (A, 'T335', '个', 100, '生产库房', 10))
    c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,is_emg_temp,updated_at) VALUES(?,?,?,?,?,?,1,datetime('now','localtime'))", (B, 'T335', '个', 100, '临时待分配库', 10))
    c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,updated_at) VALUES(?,?,?,?,?,?,datetime('now','localtime'))", (C, 'T335', '个', 0, '生产库房', 10))
    c.commit(); c.close()


def new_req(item, qty, wh='', receiver='测试员'):
    return api('/api/requisitions', {'items': [{'item_name': item, 'spec': 'T335', 'quantity': qty, 'unit': '个',
                                            'warehouse': wh}], 'receiver': receiver, 'purpose': '测试出库'}, method='POST')


def approve(rid):
    return api('/api/approvals/requisition/%d/approve' % rid, {'action': 'approved', 'comment': '回归测试'}, method='POST')


def cleanup():
    c = db()
    try:
        _r = [x[0] for x in c.execute("SELECT id FROM requisitions WHERE item_name LIKE ?", (M + '%',)).fetchall()]
        _r += [x[0] for x in c.execute("SELECT DISTINCT ri.requisition_id FROM requisition_items ri WHERE ri.item_name LIKE ?", (M + '%',)).fetchall()]
        _r = list(set(_r))
        for rid in _r:
            c.execute("DELETE FROM requisition_items WHERE requisition_id=?", (rid,))
            c.execute("DELETE FROM approval_instances WHERE biz_type='requisition' AND biz_id=?", (rid,))
            c.execute("DELETE FROM dingtalk_instances WHERE biz_type='requisition' AND biz_id=?", (rid,))
            c.execute("DELETE FROM feishu_instances WHERE biz_type='requisition' AND biz_id=?", (rid,))
            c.execute("DELETE FROM notifications WHERE biz_type='requisition' AND biz_id=?", (rid,))
            c.execute("DELETE FROM inventory_flows WHERE doc_type='requisition' AND doc_id=?", (rid,))
            c.execute("DELETE FROM requisitions WHERE id=?", (rid,))
        c.execute("DELETE FROM inventory WHERE item_name LIKE ?", (M + '%',))
        c.execute("DELETE FROM inventory_flows WHERE item_name LIKE ?", (M + '%',))
        c.execute("DELETE FROM approval_action_logs WHERE biz_type='requisition' AND biz_id IN (%s)" % (','.join(['?'] * len(_r)) or '0'), tuple(_r or [0]))
        c.commit()
        print('  🧹 测试数据已清理 (出库单 %d 张)' % len(_r))
    except Exception as e:
        print('  ⚠️ 清理异常:', e)
    finally:
        c.close()


def main():
    print('=== V11.335 出库按库房严格扣减 回归 ===')
    st, r = api('/api/login', {'username': 'admin', 'password': 'admin123'}, method='POST')
    chk('登录(admin)', st == 200 and r.get('success'))
    setup()

    # ① 未指定库房 → 自动落定唯一库房(生产库房), 建单成功
    st, r = new_req(A, 100)
    chk('①建单成功(未指定库房, 自动落定)', st == 200 and r.get('success'), r)
    rid1 = r.get('id')
    c = db(); row = c.execute("SELECT warehouse FROM requisitions WHERE id=?", (rid1,)).fetchone() if rid1 else None
    its = c.execute("SELECT warehouse FROM requisition_items WHERE requisition_id=?", (rid1,)).fetchone() if rid1 else None
    c.close()
    chk('①单据库房=生产库房', bool(row) and row['warehouse'] == '生产库房', row['warehouse'] if row else None)
    chk('①明细库房=生产库房', bool(its) and its['warehouse'] == '生产库房', its['warehouse'] if its else None)

    # ② 同物资再建一张 → 被"待审批占用"拦截(修复前可无限建单)
    st, r = new_req(A, 100)
    chk('②重复开单被占用拦截', st == 400 and '占用' in (r.get('error') or ''), r)

    # ③ 审批通过 → 按该库房扣减, 流水库房=生产库房
    st, r = approve(rid1)
    chk('③审批通过', st == 200 and r.get('success'), r)
    chk('③生产库房结存 100→0', abs(inv_qty(A, '生产库房')) < 1e-6, inv_qty(A, '生产库房'))
    c = db()
    fl = c.execute("SELECT warehouse,qty FROM inventory_flows WHERE doc_type='requisition' AND doc_id=? AND flow_type='出库'", (rid1,)).fetchall()
    c.close()
    chk('③出库流水库房=生产库房(账实一致)', len(fl) == 1 and fl[0]['warehouse'] == '生产库房', [dict(x) for x in fl])

    # ③b 库存已扣空 → 再建单被拦
    st, r = new_req(A, 10)
    chk('③b库存不足被拦', st == 400 and '库存不足' in (r.get('error') or ''), r)

    # ④ 临时待分配库的物资: 未指定库房 → 拦并提示先分配(修复前会被跨库房扣走!)
    st, r = new_req(B, 10)
    _e = r.get('error') or ''
    chk('④临时库物资不被跨库扣走(拦+提示分配)', st == 400 and ('临时' in _e or '库存不足' in _e), _e)
    chk('④临时待分配库结存未被扣(仍100)', abs(inv_qty(B, '临时待分配库') - 100) < 1e-6, inv_qty(B, '临时待分配库'))

    # ⑤ 显式指定临时待分配库 → 允许(应急先领用)
    st, r = new_req(B, 10, wh='临时待分配库')
    chk('⑤显式选临时待分配库可出库', st == 200 and r.get('success'), r)
    if r.get('id'):
        st, r2 = approve(r['id'])
        chk('⑤审批通过并按临时库扣减', st == 200 and r2.get('success'), r2)
        chk('⑤临时库 100→90', abs(inv_qty(B, '临时待分配库') - 90) < 1e-6, inv_qty(B, '临时待分配库'))

    # ⑥ 终审复校: 直接造一张待审批单(库房结存0) → 审批应被库存复校拦住, 且不产生负库存
    c = db()
    c.execute("INSERT INTO requisitions(req_no,dept,requester,item_name,spec,quantity,unit,purpose,status,receiver,created_at,warehouse) VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),?)",
              ('CK-TESTV11335', '', '回归测试', C, 'T335', 50, '个', '测试', '待审批', '测试员', '生产库房'))
    rid3 = c.execute("SELECT id FROM requisitions WHERE req_no='CK-TESTV11335'").fetchone()[0]
    c.execute("INSERT INTO requisition_items(requisition_id,item_name,spec,unit,quantity,purpose,created_at,receiver,batch_no,warehouse) VALUES(?,?,?,?,?,?,datetime('now','localtime'),?,?,?)",
              (rid3, C, 'T335', '个', 50, '测试', '测试员', '', '生产库房'))
    c.execute("INSERT INTO approval_instances(biz_type,biz_id,level_no,role,approver,status,created_at) VALUES('requisition',?,1,'库管员','',?,datetime('now','localtime'))", (rid3, 'pending'))
    c.commit(); c.close()
    st, r = approve(rid3)
    chk('⑥终审库存复校拦截(不足不予通过)', st == 400 and '库存不足' in (r.get('error') or ''), r)
    chk('⑥未产生负库存', inv_qty(C, '生产库房') >= 0, inv_qty(C, '生产库房'))

    cleanup()
    print('\n=== 结果: %d 项通过, %d 项失败 ===' % (len(ok), len(bad)))
    if bad:
        for b in bad:
            print('  失败:', b)
        sys.exit(1)
    print('✅ 全部通过')


if __name__ == '__main__':
    main()
