#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: 审批中心待审批列表"金额/数量"列口径 (V11.286)

背景: 后端 _ap_case('amount') 对 receiving/requisition 取的是"数量"(receivings/requisitions.quantity),
      其余类型才是金额; 前端表头曾写死"金额" → 入库行显示成"¥12"。本测试锁死该口径。

跑法: .venv/Scripts/python.exe tests/test_appr_qty_col.py     (Mac: python3 tests/test_appr_qty_col.py)
全程在 tempfile 副本库上跑, 真实库/线上零写入。
"""
import os, re, sys, json, shutil, sqlite3, subprocess, tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(BASE, 'data', 'purchase.db')
IDX = os.path.join(BASE, 'templates', 'index.html')
PY = sys.executable
QTY_TYPES = ['receiving', 'requisition']
P, F = [], []


def ck(name, cond, extra=''):
    (P if cond else F).append(name)
    print(('  OK   ' if cond else '  FAIL ') + name + (('  | ' + str(extra)[:200]) if extra else ''))


def insert_fixture(conn, table, row):
    """按表实际列裁剪后插入, 返回自增 id(避免因各机表结构演进差异失败)"""
    cols = [x[1] for x in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    row = {k: v for k, v in row.items() if k in cols}
    ks = list(row)
    conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ','.join(ks), ','.join('?' * len(ks))), [row[k] for k in ks])
    return conn.execute("SELECT MAX(id) FROM %s" % table).fetchone()[0]


def main():
    live_before = sqlite3.connect(LIVE)
    before = tuple(live_before.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                   for t in ('approval_instances', 'receivings', 'requisitions'))
    live_before.close()

    tmp = tempfile.mkdtemp(prefix='test_appr_qty_col-')
    copy = os.path.join(tmp, 'purchase.db')
    shutil.copy(LIVE, copy)
    sys.path.insert(0, BASE)
    import app as A
    A.DB = copy

    print('[1] 数据面: 该列对 入库/出库 存的是数量')
    c = sqlite3.connect(copy); c.row_factory = sqlite3.Row
    fixtures = {}
    for t, no_field, no_val, qty in (('receivings', 'receive_no', 'RK-TEST-QTY', 12),
                                     ('requisitions', 'req_no', 'CK-TEST-QTY', 7)):
        rid = insert_fixture(c, t, {no_field: no_val, 'item_name': '【测试】口径物资', 'quantity': qty,
                                    'unit': '个', 'status': '待审批', 'dept': '生产部'})
        biz = 'receiving' if t == 'receivings' else 'requisition'
        insert_fixture(c, 'approval_instances',
                       {'biz_type': biz, 'biz_id': rid, 'level_no': 1, 'role': '系统管理员',
                        'approver': 'mujiao', 'approver_id': 2, 'status': 'pending',
                        'created_at': '2026-01-01 09:00:00'})
        fixtures[biz] = (rid, qty, no_val)
    c.commit(); c.close()

    cli = A.app.test_client()
    ok = (cli.post('/api/login', json={'username': 'mujiao', 'password': '123456'}).get_json() or {}).get('success')
    ck('登录(mujiao)', ok)
    rows = cli.get('/api/approvals/all-pending').get_json() or []
    for biz, (rid, qty, no_val) in fixtures.items():
        hit = [x for x in rows if x.get('biz_type') == biz and x.get('biz_id') == rid]
        ck('%s: biz_amount == %s.quantity(%d) 即数量' % (biz, biz, qty),
           bool(hit) and float(hit[0].get('biz_amount') or 0) == float(qty), hit[0] if hit else None)
        ck('%s: 单据号字段未被破坏(%s)' % (biz, no_val), bool(hit) and hit[0].get('biz_no') == no_val)

    print('[2] 渲染面: 前端真实代码各标签页 表头/单元格')
    lines = open(IDX, encoding='utf-8').read().split('\n')

    def take(start_pat, end_pat=None, include_end=False):
        for i, l in enumerate(lines):
            if re.search(start_pat, l):
                if end_pat is None:
                    return l
                out = [l]
                for l2 in lines[i + 1:]:
                    if re.search(end_pat, l2):
                        if include_end:
                            out.append(l2)
                        break
                    out.append(l2)
                return '\n'.join(out)
        raise RuntimeError('index.html 未找到片段: ' + start_pat)

    slices = [
        take(r'^function esc\(s\)\{'), take(r'^function rnder\(d,k,h,f\)\{'),
        take(r'^const APPR_TM=\{'), take(r"^let APPR_FILTER='';"), take(r"^let APPR_CENTER='buy';"),
        take(r'^const APPR_CENTER_TABS=\{', r'^\};', include_end=True),
        take(r'^const APPR_REPAIR_TYPES='), take(r'^const APPR_QTY_TYPES='),
        take(r'^const _isRepairRow='), take(r'^const _apprMatch='),
        take(r'^function _apprCenterRows\(a\)\{', r'^\}', include_end=True),
        take(r'^async function loadApprovals\(\)\{', r'^function viewBiz\('),
    ]
    ck('APPR_QTY_TYPES 已登记 入库/出库', 'APPR_QTY_TYPES' in '\n'.join(slices) and "'receiving','requisition'" in '\n'.join(slices))

    js = os.path.join(tmp, 'run.js')
    with open(js, 'w', encoding='utf-8') as f:
        f.write("""const ROWS=[
 {biz_type:'receiving',biz_id:1,biz_no:'RK-001',biz_name:'验收物资',biz_amount:12,role:'库管员',approver:'穆娇',approver_id:2,created_at:'2026-01-01 09:00:00'},
 {biz_type:'requisition',biz_id:2,biz_no:'CK-001',biz_name:'领用',biz_amount:7,role:'库管员',approver:'穆娇',approver_id:2,created_at:'2026-01-01 09:01:00'},
 {biz_type:'payment',biz_id:3,biz_no:'FK-001',biz_name:'货款',biz_amount:500,role:'财务',approver:'穆娇',approver_id:2,created_at:'2026-01-01 09:02:00'},
 {biz_type:'purchase_request',biz_id:4,biz_no:'ZH-001',biz_name:'申请',biz_amount:999,role:'部门负责人',approver:'穆娇',approver_id:2,created_at:'2026-01-01 09:03:00'}];
const BOX={};
global.document={getElementById:id=>BOX[id]||(BOX[id]={innerHTML:''}),createElement:()=>{let t='';return{set textContent(v){t=String(v==null?'':v)},get innerHTML(){return t.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}}}};
const id=s=>document.getElementById(s);
const api=async p=>ROWS;
const canApprove=()=>true;
const ME={name:'穆娇',username:'mujiao',id:2};
const tag=s=>s;
let P=[],F=[];
const ck=(n,c,e)=>{c?P.push(n):F.push(n);console.log((c?'  OK   ':'  FAIL ')+n+(e!==undefined?'  | '+JSON.stringify(e):''));};
""")
        f.write('\n'.join(slices))
        f.write("""
const R=()=>BOX['approvalsTable'].innerHTML;
(async()=>{
 for(const [tab,head,ownQty,money] of [['receiving','数量','>12<',null],['requisition','数量','>7<',null],
   ['payment','金额',null,'¥500'],['purchase_request','金额',null,null],['','金额/数量','>12<','¥500']]){
   APPR_FILTER=tab; await loadApprovals();
   const h=R();
   ck(`[tab=${tab||'全部'}] 表头=<th>${head}</th>`, h.includes(`>${head}</th>`), h.slice(0,200));
   if(ownQty) ck(`[tab=${tab||'全部'}] 数量行=${ownQty.slice(1,-1)} 且不带¥`,
                 h.includes(ownQty)&&!h.includes('¥'+ownQty.slice(1,-1)), null);
   if(tab==='') ck('[tab=全部] 出库数量行=7 且不带¥', h.includes('>7<')&&!h.includes('¥7'), null);
   if(money) ck(`[tab=${tab||'全部'}] 付款行仍为 ${money}`, h.includes(money), null);
 }
 APPR_FILTER='receiving'; await loadApprovals();
 ck('入库页整页无¥(纯数量页)', !R().includes('¥'), null);
 ck('入库页其它列未破坏(单据号/内容/审批人)', R().includes('RK-001')&&R().includes('验收物资')&&R().includes('穆娇'));
 console.log('--- node: '+P.length+' 通过 / '+F.length+' 失败 ---');
 process.exit(F.length?1:0);
})();
""")
    try:
        r = subprocess.run(['node', js], capture_output=True, text=True, encoding='utf-8', errors='replace')
        print(r.stdout.rstrip())
        if r.stderr.strip():
            print('  node stderr: ' + r.stderr[:400])
        ck('node 渲染断言全部通过', r.returncode == 0)
    except FileNotFoundError:
        print('  ⚠️  node 不可用, 跳过渲染面检查')
    except Exception as e:
        ck('node 渲染断言全部通过', False, e)

    del cli
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass
    live_after = sqlite3.connect(LIVE)
    after = tuple(live_after.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ('approval_instances', 'receivings', 'requisitions'))
    live_after.close()
    ck('真实库零写入(测试前后计数一致)', before == after, '%s → %s' % (before, after))

    print()
    print('=== 结果: %d 通过 / %d 失败 ===' % (len(P), len(F)))
    if F:
        print('失败项: ' + ' | '.join(F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
