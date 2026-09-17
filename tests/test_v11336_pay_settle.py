# -*- coding: utf-8 -*-
"""V11.336 回归: 付款方式结构化(应急询价"付款方式"下拉 → 结算方式 → 月末进月结汇总)
覆盖: ①应急询价保存付款方式(月结30天)→落结算类型=月结 ②定标生成应急订单带 settle_type=月结 + pay_term
      ③该订单出现在【合同管理→月结汇总】(含⚡应急单计数) ④常规订单编辑:交易模式选月结→结算方式自动=月结
      ⑤付款方式=货到付款→结算类型=现结
运行: .venv/bin/python tests/test_v11336_pay_settle.py   (需本机 5899 在跑)
测试数据统一带【测试】V11336 标记, 结束自动清理(含审批实例/通知)。
"""
import json, os, sqlite3, sys, urllib.request, urllib.error, http.cookiejar

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'purchase.db')
M = '【测试】V11336'
SUP = M + '供应商'
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
ok, bad = [], []


def api(p, d=None, method=None):
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
    except Exception as e:
        return 0, {'error': repr(e)}


def chk(name, cond, extra=None):
    (ok if cond else bad).append(name + ((' | ' + str(extra)) if extra and not cond else ''))
    print(('  ✅ ' if cond else '  ❌ ') + name + ((' | ' + str(extra)) if extra and not cond else ''))


def db():
    c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row
    return c


def cleanup():
    c = db()
    try:
        eids = [x[0] for x in c.execute("SELECT id FROM emergency_purchases WHERE item_name LIKE ?", (M + '%',)).fetchall()]
        for eid in eids:
            for t in ('approval_instances', 'dingtalk_instances', 'feishu_instances'):
                c.execute("DELETE FROM %s WHERE biz_type LIKE 'emergency%%' AND biz_id=?" % t, (eid,))
            c.execute("DELETE FROM notifications WHERE biz_id=? AND biz_type LIKE 'emergency%'", (eid,))
            c.execute("DELETE FROM emergency_logs WHERE emg_id=?", (eid,))
        oids = [x[0] for x in c.execute("SELECT id FROM purchase_orders WHERE supplier=? OR item_name LIKE ?", (SUP, M + '%')).fetchall()]
        for oid in oids:
            c.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
            for t in ('approval_instances', 'dingtalk_instances', 'feishu_instances', 'notifications'):
                c.execute("DELETE FROM %s WHERE biz_id=? AND biz_type='purchase_order'" % t, (oid,))
            c.execute("DELETE FROM purchase_orders WHERE id=?", (oid,))
        c.execute("DELETE FROM emergency_purchases WHERE item_name LIKE ?", (M + '%',))
        c.execute("DELETE FROM suppliers WHERE name=?", (SUP,))
        c.commit()
        print('  🧹 测试数据已清理 (应急单 %d 张 / 订单 %d 张)' % (len(eids), len(oids)))
    except Exception as e:
        print('  ⚠️ 清理异常:', e)
    finally:
        c.close()


def main():
    print('=== V11.336 付款方式结构化 回归 ===')
    st, r = api('/api/login', {'username': 'admin', 'password': 'admin123'}, method='POST')
    chk('登录(admin)', st == 200 and r.get('success'))
    # 造一条测试应急单(待定标), 用于走 询价→定标 链路
    c = db()
    c.execute("DELETE FROM emergency_purchases WHERE item_name LIKE ?", (M + '%',))
    c.execute("""INSERT INTO emergency_purchases(emg_no,project,item_name,spec,unit,quantity,est_amount,reason,dept,status,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),datetime('now','localtime'))""",
              ('TEST-V11336', '回归', M + '物资', 'T336', '个', 10, 1000, '回归测试', '综合办', '待询价'))
    eid = c.execute("SELECT id FROM emergency_purchases WHERE emg_no='TEST-V11336'").fetchone()[0]
    c.commit(); c.close()

    # ① 询价保存: 付款方式=月结30天 → 结算类型=月结
    st, r = api('/api/emergency/%d/inquiry' % eid, {'supplier': SUP, 'amount': 1000, 'tax_rate': 13,
                                                    'pay_method': '月结30天', 'settle_type': '月结'}, method='POST')
    chk('①询价保存(付款方式=月结30天)', st == 200 and r.get('success'), r)
    c = db()
    row = c.execute("SELECT inq_pay_method,inq_settle_type,status FROM emergency_purchases WHERE id=?", (eid,)).fetchone()
    c.close()
    chk('①付款方式落库', bool(row) and row['inq_pay_method'] == '月结30天', row['inq_pay_method'] if row else None)
    chk('①结算类型=月结', bool(row) and row['inq_settle_type'] == '月结', row['inq_settle_type'] if row else None)

    # ② 定标 → 生成应急订单, 带 settle_type/pay_term
    st, r = api('/api/emergency/%d/award' % eid, {}, method='POST')
    chk('②提交定标', st == 200 and r.get('success'), r)
    oid = r.get('order_id')
    c = db()
    o = c.execute("SELECT order_no,trade_mode,settle_type,pay_term,is_emg,total_amount,status FROM purchase_orders WHERE id=?", (oid,)).fetchone() if oid else None
    c.close()
    chk('②订单结算方式=月结', bool(o) and o['settle_type'] == '月结', dict(o) if o else None)
    chk('②订单付款方式文本=月结30天', bool(o) and o['pay_term'] == '月结30天', dict(o) if o else None)
    chk('②订单保留应急标识(trade_mode=应急采购/is_emg)', bool(o) and o['trade_mode'] == '应急采购' and o['is_emg'] == 1, dict(o) if o else None)

    # ③ 月结汇总能看到这张应急订单(含⚡应急单计数)
    st, rows = api('/api/contracts/monthly-summary')
    g = next((x for x in (rows or []) if x.get('supplier') == SUP), None)
    chk('③月结汇总含该厂家分组', bool(g), rows)
    chk('③分组金额=应急订单金额', bool(g) and bool(o) and abs(float(g.get('amt') or 0) - float(o['total_amount'] or 0)) < 0.01, g)
    chk('③标记含⚡应急单', bool(g) and int(g.get('emg_cnt') or 0) >= 1, g)
    chk('③明细带⚡标识', bool(g) and '⚡' in str(g.get('detail') or ''), g.get('detail') if g else None)

    # ④ 常规订单编辑: 交易模式选月结 → 结算方式自动=月结(修复前会留在现结, 月结汇总漏单)
    c = db()
    c.execute("""INSERT INTO purchase_orders(order_no,item_name,spec,quantity,unit,price,total_amount,supplier,owner,owner_id,trade_mode,status,created_at,settle_type)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'),?)""",
              ('TEST-V11336', M + '常规物资', 'T336', 2, '个', 100, 200, SUP, '温丽', 1, '货到付款', '草稿', '现结'))
    oid2 = c.execute("SELECT id FROM purchase_orders WHERE order_no='TEST-V11336'").fetchone()[0]
    c.commit(); c.close()
    st, r = api('/api/docs/purchase_order/%d/update' % oid2,
                {'trade_mode': '月结', 'items': [{'item_name': M + '常规物资', 'spec': 'T336', 'unit': '个', 'quantity': 2, 'price': 100}]},
                method='POST')
    chk('④订单编辑保存', st == 200 and bool(r.get('success')), (st, r))
    c = db()
    o2 = c.execute("SELECT trade_mode,settle_type FROM purchase_orders WHERE id=?", (oid2,)).fetchone()
    c.close()
    chk('④交易模式=月结→结算方式自动=月结', bool(o2) and o2['settle_type'] == '月结', dict(o2) if o2 else None)

    # ⑤ 反向: 改回货到付款 → 结算方式自动=现结
    st, r = api('/api/docs/purchase_order/%d/update' % oid2,
                {'trade_mode': '货到付款', 'items': [{'item_name': M + '常规物资', 'spec': 'T336', 'unit': '个', 'quantity': 2, 'price': 100}]},
                method='POST')
    c = db()
    o2 = c.execute("SELECT trade_mode,settle_type FROM purchase_orders WHERE id=?", (oid2,)).fetchone()
    c.close()
    chk('⑤改回货到付款→结算方式=现结', bool(o2) and o2['settle_type'] == '现结', dict(o2) if o2 else None)

    cleanup()
    print('\n=== 结果: %d 项通过, %d 项失败 ===' % (len(ok), len(bad)))
    if bad:
        for b in bad:
            print('  失败:', b)
        sys.exit(1)
    print('✅ 全部通过')


if __name__ == '__main__':
    main()
