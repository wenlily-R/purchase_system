#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: 三方询价·商家报价页 计价口径 (定稿 V11.288 = 录含税单价 → 自动倒算不含税)

口径曾在 V11.270(含税) → V11.285(不含税) → V11.288(含税, 现行) 之间被反向改过两次, 本测试锁死现行口径,
任何人把它翻回去(或无声改坏)都会在此失败。口径裁定见 docs/报价页计价口径_定稿_20260912.md。

跑法: .venv/Scripts/python.exe tests/test_quote_price_mode.py   (Mac: python3 tests/test_quote_price_mode.py)
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
IDX_README = 'docs/报价页计价口径_定稿_20260912.md'
P, F = [], []


def ck(name, cond, extra=''):
    (P if cond else F).append(name)
    print(('  OK   ' if cond else '  FAIL ') + name + (('  | ' + str(extra)[:200]) if extra else ''))


def insert_fixture(conn, table, row):
    """按表实际列裁剪后插入, 返回自增 id(容忍各机表结构演进差异)"""
    cols = [x[1] for x in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    row = {k: v for k, v in row.items() if k in cols}
    ks = list(row)
    conn.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, ','.join(ks), ','.join('?' * len(ks))), [row[k] for k in ks])
    return conn.execute("SELECT MAX(id) FROM %s" % table).fetchone()[0]


def main():
    live = sqlite3.connect(LIVE)
    before = tuple(live.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                   for t in ('purchase_requests', 'request_items', 'inquiries', 'inquiry_suppliers'))
    live.close()

    tmp = tempfile.mkdtemp(prefix='test_quote_price_mode-')
    copy = os.path.join(tmp, 'purchase.db')
    shutil.copy(LIVE, copy)
    sys.path.insert(0, BASE)
    import app as A
    A.DB = copy

    cli = A.app.test_client()
    ok = (cli.post('/api/login', json={'username': 'mujiao', 'password': '123456'}).get_json() or {}).get('success')
    ck('登录(mujiao)', ok)

    def new_inquiry(tag, phones):
        c = sqlite3.connect(copy)
        rid = insert_fixture(c, 'purchase_requests', {'req_no': 'ZH-QPM-' + tag, 'purpose': '【测试】报价口径',
                                                      'status': '已通过', 'dept': '生产部', 'created_by': '穆娇'})
        for nm, qy in (('【测试】电机', 2), ('【测试】轴承', 4)):
            insert_fixture(c, 'request_items', {'req_id': rid, 'item_name': nm, 'spec': 'S', 'quantity': qy,
                                                'unit': '个', 'unit_price': 100, 'total_price': 100 * qy})
        c.commit(); c.close()
        j = cli.post('/api/inquiries', json={'req_id': rid, 'suppliers': [
            {'name': '【测试】%s%d' % (tag, i + 1), 'phone': p} for i, p in enumerate(phones)]}).get_json() or {}
        sup = sqlite3.connect(copy)
        sup.row_factory = sqlite3.Row
        rows = sup.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=? ORDER BY id", (j.get('id'),)).fetchall()
        sup.close()
        return j, rows

    def vendor(row):
        c = A.app.test_client()
        r = c.post('/api/inquiry/vendor/%s/auth' % row['token'],
                   json={'phone': row['phone'], 'code': row['access_code']})
        assert (r.get_json() or {}).get('success'), '外部鉴权失败: %s' % r.get_data(as_text=True)[:120]
        return c

    def db_details(sid):
        c = sqlite3.connect(copy)
        v = c.execute("SELECT quote_details, quote_price FROM inquiry_suppliers WHERE id=?", (sid,)).fetchone()
        c.close()
        return json.loads(v[0] or '[]'), v[1]

    print('[1] 页面形态: 录入=含税单价, 不含税为"自动"列 (V11.288 定稿口径)')
    jA, rows = new_inquiry('A', ['13900000001', '13900000002'])
    ck('发起询价(2家)', jA.get('success'), jA)
    pg = vendor(rows[0]).get('/inq/%s' % rows[0]['token']).get_data(as_text=True)
    ck('录入框=含税单价(id=ipi0, placeholder)', 'placeholder="含税单价"' in pg and 'id="ipi0"' in pg and 'id="ipi1"' in pg)
    ck('自动列=不含税单价(id=exc0, 标"自动")',
       'id="exc0"' in pg and '不含税单价(元)<span style="color:#888;font-size:11px;font-weight:normal">自动</span>' in pg)
    ck('表头含税单价(元)*', '含税单价(元)<span style="color:#e74c3c">*</span>' in pg)
    ck('页头提示=填写含税单价', '请逐项填写<b>含税单价</b>与<b>税率</b>' in pg)
    ck('未被翻回"录不含税"形态(无 placeholder=不含税单价 / id=exi)',
       'placeholder="不含税单价"' not in pg and 'id="exi0"' not in pg, '若失败=口径又被反向改了, 见 ' + IDX_README)
    ck('JS 倒算公式 不含税=含税/(1+税率/100)', 'ip/(1+tr/100)' in pg and '[id^=ipi]' in pg)
    ck('未填校验文案=含税单价', '请填写所有物料的含税单价' in pg)

    print('[2] 服务端: 以含税单价为基准(不信前端传的不含税值)')
    cl = vendor(rows[0])
    det = [{'item_name': '【测试】电机', 'unit_price': 1130, 'excl_price': 9999, 'tax_rate': 13, 'qty': 2},
           {'item_name': '【测试】轴承', 'unit_price': 565, 'excl_price': 9999, 'tax_rate': 13, 'qty': 4}]
    r = cl.post('/api/inquiry/vendor/%s/quote' % rows[0]['token'], json={'quote_price': 4520, 'details': det})
    ck('报价提交成功', (r.get_json() or {}).get('success'), r.get_data(as_text=True)[:160])
    qd, qp = db_details(rows[0]['id'])
    ck('unit_price=前端录入的含税单价(1130/565)', [x['unit_price'] for x in qd] == [1130.0, 565.0], qd)
    ck('excl_price=服务端倒算(1000/500), 不信前端传的9999', [x['excl_price'] for x in qd] == [1000.0, 500.0], qd)
    ck('整单含税含运总价保存', abs((qp or 0) - 4520) < 0.01, qp)

    print('[3] 边界: 税率0%不放大 / 旧式仅传不含税仍兼容')
    jB, rowsB = new_inquiry('B', ['13900000003', '13900000004'])
    clB = vendor(rowsB[0])
    clB.post('/api/inquiry/vendor/%s/quote' % rowsB[0]['token'], json={'quote_price': 500, 'details': [
        {'item_name': 'a', 'unit_price': 500, 'tax_rate': 0, 'qty': 1},
        {'item_name': 'b', 'excl_price': 1000, 'tax_rate': 13, 'qty': 1}]})
    qd2, _ = db_details(rowsB[0]['id'])
    ck('税率0%: 不含税=含税(不放大)', qd2[0]['unit_price'] == 500.0 and qd2[0]['excl_price'] == 500.0, qd2[0])
    ck('旧式仅传不含税1000 → 服务端正算含税1130',
       qd2[1]['excl_price'] == 1000.0 and qd2[1]['unit_price'] == 1130.0, qd2[1])

    print('[3b] 回显: 已报价按含税单价回填录入框')
    pg2 = clB.get('/inq/%s' % rowsB[0]['token']).get_data(as_text=True)
    m = re.findall(r'id="ipi(\d+)" value="([^"]*)"', pg2)
    ck('回显到含税录入框(500 / 1130)', len(m) == 2 and [float(v) for _, v in m] == [500.0, 1130.0], m)

    print('[4] 前端 JS 实跑(真实页面脚本): 倒算与合计')
    js = os.path.join(tmp, 'run.js')
    open(js, 'w', encoding='utf-8').write("""const fs=require('fs'),vm=require('vm');
const code=fs.readFileSync(process.argv[2],'utf8');
let P=0,F=0;const ck=(n,c,e)=>{c?(P++,console.log('  OK   '+n)):(F++,console.log('  FAIL '+n+(e!==undefined?'  | '+JSON.stringify(e):'')))};
const mk=(id,v,q)=>({id,value:v===undefined?'':String(v),textContent:'',getAttribute:k=>k==='data-q'?q:null});
function build(rows){const els={},list=[];rows.forEach((r,i)=>{els['ipi'+i]=mk('ipi'+i,r.ip,r.q);els['tx'+i]=mk('tx'+i,r.tr);
els['exc'+i]=mk('exc'+i);els['ut'+i]=mk('ut'+i);list.push(els['ipi'+i],els['tx'+i],els['exc'+i],els['ut'+i]);});
els['shipTotal']=mk('shipTotal',rows.ship||'');els['total']=mk('total');return{els,exis:rows.map((r,i)=>els['ipi'+i])};}
let DOM=build([{ip:'113',tr:'13',q:'2'},{ip:'56.5',tr:'13',q:'4'}]),ALERT=[],SENT=null;
const g={document:{getElementById:id=>DOM.els[id]||null,querySelectorAll:s=>s==='[id^=ipi]'?DOM.exis:[],querySelector:()=>null},
alert:m=>ALERT.push(String(m)),prompt:()=>g.__p,fetch:(u,o)=>{SENT=JSON.parse(o.body);return Promise.resolve({json:()=>Promise.resolve({success:true})})},
location:{search:'',pathname:'/inq/x',reload(){}},history:{replaceState(){}},setTimeout(){},addEventListener(){},isNaN,parseFloat,Math,JSON,console};
g.window=g;vm.createContext(g);vm.runInContext(code,g);
g.calc();
ck('不含税单价 113/1.13=100.00',DOM.els.exc0.textContent==='100.00',DOM.els.exc0.textContent);
ck('不含税单价 56.5/1.13=50.00',DOM.els.exc1.textContent==='50.00',DOM.els.exc1.textContent);
ck('行总价(含税) 113×2=226.00',DOM.els.ut0.textContent==='226.00',DOM.els.ut0.textContent);
ck('合计(含税) 452.00',DOM.els.total.textContent==='452.00',DOM.els.total.textContent);
DOM.els.tx0.value='0';g.calc();ck('税率0%: 不含税=含税=113.00',DOM.els.exc0.textContent==='113.00',DOM.els.exc0.textContent);
DOM=build([{ip:'',tr:'13',q:'2'},{ip:'',tr:'13',q:'4'}]);g.__p='452';g.quickFill();
ck('填总价452按数量分摊: 每行含税75.33',DOM.els.ipi0.value==='75.33',DOM.els.ipi0.value);
ck('反摊后合计≈452(尾差≤0.05)',Math.abs(parseFloat(DOM.els.total.textContent)-452)<=0.05,DOM.els.total.textContent);
DOM.els.shipTotal.value='452';DOM.els.ipi0.value='113';DOM.els.ipi1.value='56.5';g.sub();
setTimeout(()=>{ck('提交载荷: 含税113 / 不含税100 / 税13',
  SENT&&SENT.details[0].unit_price===113&&SENT.details[0].excl_price===100&&SENT.details[0].tax_rate===13,SENT&&SENT.details[0]);
SENT=null;ALERT=[];DOM.els.ipi1.value='';g.sub();
setTimeout(()=>{ck('有行未填→拦截且提示"含税单价"',SENT===null&&ALERT.length===1&&ALERT[0].includes('含税单价'),ALERT);
console.log('--- node: '+P+' 通过 / '+F+' 失败 ---');process.exit(F?1:0)},30)},30);
""")
    open(os.path.join(tmp, 'vendor_page.js'), 'w', encoding='utf-8').write(
        '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', pg, re.S)))
    try:
        r = subprocess.run(['node', js, os.path.join(tmp, 'vendor_page.js')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        print(r.stdout.rstrip())
        if r.stderr.strip():
            print('  node stderr: ' + r.stderr[:400])
        ck('node 前端实算全部通过', r.returncode == 0)
    except FileNotFoundError:
        print('  ⚠️  node 不可用, 跳过前端实算检查')

    print('[5] 口径裁定文档在位')
    ck('docs/%s 存在(口径唯一裁定记录)' % os.path.basename(IDX_README), os.path.exists(os.path.join(BASE, IDX_README)))

    del cli
    shutil.rmtree(tmp, ignore_errors=True)
    live = sqlite3.connect(LIVE)
    after = tuple(live.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ('purchase_requests', 'request_items', 'inquiries', 'inquiry_suppliers'))
    live.close()
    ck('真实库零写入(测试前后计数一致)', before == after, '%s → %s' % (before, after))

    print()
    print('=== 结果: %d 通过 / %d 失败 ===' % (len(P), len(F)))
    if F:
        print('失败项: ' + ' | '.join(F))
        print('提示: 若为"口径又被反向改"所致, 请先读 ' + IDX_README)
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
