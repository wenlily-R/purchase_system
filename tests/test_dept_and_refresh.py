#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: 申请部门下拉(新增部门/拼音首字母排序/可滚动) + 各业务模块页刷新按钮 (V11.293)

锁定三件事:
  1. 8个新增部门由 init_db 幂等补种(空库/老库都对), 空库首跑不再因 contract_invoices 补列早于建表而崩溃
  2. /api/departments 按拼音首字母排序(A→Z)
  3. 18个业务页标题左侧各1个刷新按钮(样式同三方询价), 点击调对应 loader 且**沿用当前筛选条件**;
     申请部门为可滚动面板(原生 select 弹层无法限高), 值为只读输入框(取值/提交口径不变)

跑法: .venv/Scripts/python.exe tests/test_dept_and_refresh.py   (Mac: python3 tests/test_dept_and_refresh.py)
全程 tempfile 副本库, 真实库零写入。
"""
import os, io, re, sys, json, hashlib, shutil, sqlite3, subprocess, tempfile, contextlib

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(BASE, 'data', 'purchase.db')
IDX = os.path.join(BASE, 'templates', 'index.html')
PY = sys.executable
NEW8 = ['机电部', '磅房', '采购部', '安监部', '人事部', '厨房', '绿化部', '生产车队']
EXPECT_ORDER = ['安监部', '磅房', '采购部', '财务部', '厨房', '工程部', '后勤部', '机电部', '绿化部',
                '人事部', '生产部', '生产车队', '维修车间', '信息部', '综合办']
BUSINESS = ['pprequests', 'pinquiries', 'porders', 'papprovals', 'pcontracts', 'preceivings', 'ppayments',
            'prequisitions', 'preturns', 'pcounts', 'pexpenses', 'pinventory', 'psuppliers', 'psettlements',
            'pinvoices', 'pledger', 'psmall', 'palerts']
NOT_BUSINESS = ['psystem', 'preports', 'pdingtalk', 'pfeishu']
P, F = [], []


def ck(n, c, e=''):
    (P if c else F).append(n)
    print(('  OK   ' if c else '  FAIL ') + n + (('  | ' + str(e)[:200]) if e else ''))


def main():
    tmp = tempfile.mkdtemp(prefix='test_dept_and_refresh-')
    sys.path.insert(0, BASE)
    import app as A

    live = sqlite3.connect(LIVE)
    before = live.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
    live.close()

    print('[1] 项目自检')
    r = subprocess.run([PY, 'check_code.py'], cwd=BASE, capture_output=True, text=True, encoding='utf-8', errors='replace')
    ck('check_code 0错误0警告', r.returncode == 0 and '0 错误, 0 警告' in (r.stdout or ''))

    print('[2] 部门: 空库首跑 + 幂等补种')
    tpl = os.path.join(BASE, 'uploads', 'tpl_default.docx')
    tpl_h = hashlib.sha256(open(tpl, 'rb').read()).hexdigest() if os.path.exists(tpl) else ''
    empty = os.path.join(tmp, 'empty.db')
    A.DB = empty
    crashed = None
    _buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(_buf), contextlib.redirect_stderr(_buf):
            A.init_db()
    except Exception as e:
        crashed = e
    r_stdout = _buf.getvalue()
    ck('空库 init_db 不再崩溃(原 contract_invoices 补列早于建表)', crashed is None, crashed)
    c = sqlite3.connect(empty)
    names = [x[0] for x in c.execute("SELECT name FROM departments").fetchall()]
    ck('空库部门=5基础+10补种=15 且与生产库一致', sorted(names) == sorted(EXPECT_ORDER), names)
    ck('8个新增部门 + 工程部/信息部 均入库', all(d in names for d in NEW8 + ['工程部', '信息部']), names)
    cols = [x[1] for x in c.execute("PRAGMA table_info(contract_invoices)").fetchall()]
    ck('contract_invoices.node_id 已补列', 'node_id' in cols, cols)
    ck('空库 init_db 不再有 "no such table" 告警(两块已挪到建表之后)',
       'no such table' not in (r_stdout or ''), r_stdout[-160:] if r_stdout else '')
    c.close()
    ck('init_db 未重写内置模板(仓库 uploads/ 不再被改脏)',
       hashlib.sha256(open(tpl, 'rb').read()).hexdigest() == tpl_h, hashlib.sha256(open(tpl, 'rb').read()).hexdigest()[:16])

    copy = os.path.join(tmp, 'live.db')
    shutil.copy(LIVE, copy)
    A.DB = copy
    c = sqlite3.connect(copy)
    base = [x[0] for x in c.execute("SELECT name FROM departments ORDER BY id").fetchall()]
    c.close()
    A.init_db()
    c = sqlite3.connect(copy)
    after = [x[0] for x in c.execute("SELECT name FROM departments ORDER BY id").fetchall()]
    c.close()
    ck('老库原部门未被改动', base == [x for x in after if x in base], base)
    ck('老库补齐8个新部门', all(d in after for d in NEW8), [d for d in NEW8 if d not in after])
    A.init_db()
    c = sqlite3.connect(copy)
    again = [x[0] for x in c.execute("SELECT name FROM departments ORDER BY id").fetchall()]
    c.close()
    ck('重复 init_db 幂等', again == after, '%d → %d' % (len(after), len(again)))

    print('[3] /api/departments 拼音首字母排序')
    cli = A.app.test_client()
    ck('登录(mujiao)', (cli.post('/api/login', json={'username': 'mujiao', 'password': '123456'}).get_json() or {}).get('success'))
    got = [d['name'] for d in (cli.get('/api/departments').get_json() or [])]
    ck('顺序=拼音首字母(A安→B磅→C采/财/厨→…→Z综)', got == EXPECT_ORDER, '实际: %s' % ' '.join(got))

    print('[4] 前端真实代码(node): 刷新按钮 + 可滚动部门下拉')
    src = open(IDX, encoding='utf-8').read()
    ck('三方询价工具栏旧刷新按钮已移除', 'onclick="loadInquiries()"' not in src)
    dsel = re.search(r"// ── V11\.293: 申请部门下拉\(可滚动\).*?(?=\n// Forms)", src, re.S)
    rfs = re.search(r"// ── V11\.293b:.*?try\{injectRefreshBtns\(\)\}catch\(e\)\{\}", src, re.S)
    ck('切出两段真实实现', bool(dsel) and bool(rfs))
    js = os.path.join(tmp, 'run.js')
    open(js, 'w', encoding='utf-8').write((r'''
let CALLS=[],P=0,F=0;
const ck=(n,c,e)=>{c?(P++,console.log('  OK   '+n)):(F++,console.log('  FAIL '+n+(e!==undefined?'  | '+JSON.stringify(e):'')))};
const BUSINESS=__BUSINESS__, NOT_BUSINESS=__NOTBUSINESS__, DEPTS=__DEPTS__;
/* ---------- DOM 桩 ---------- */
const REG={},FRAG=[];
function CL(){const s=new Set();return{add(...c){c.forEach(x=>s.add(x))},remove(...c){c.forEach(x=>s.delete(x))},contains:c=>s.has(c),toggle(c,on){on?s.add(c):s.delete(c)}}}
function mkEl(id){const e={id,value:'',_html:'',_kids:[],className:'',type:'',textContent:'',onclick:null,classList:CL(),
 closest:()=>null,getBoundingClientRect:()=>({top:0,bottom:0,left:0,right:0,height:0}),getAttribute:()=>'',
 querySelectorAll:sel=>sel==='.o'?e._kids:[]};
 Object.defineProperty(e,'innerHTML',{set(v){e._html=v;e._kids=[];
   const re=/<div class="(o[^"]*)"[^>]*onclick="([^"]*)"[^>]*>([^<]*)<\/div>/g;let g;
   while((g=re.exec(v))){const k={className:g[1],textContent:g[3],onclick:g[2],classList:CL()};
     g[1].split(/\s+/).filter(Boolean).forEach(x=>k.classList.add(x));e._kids.push(k)}},get(){return e._html}});
 REG[id]=e;return e}
const FILTERS={ordTypeFilter:mkEl('ordTypeFilter'),ordStatusFilter:mkEl('ordStatusFilter'),rcvFDept:mkEl('rcvFDept'),
 rcvFCat:mkEl('rcvFCat'),rcvFType:mkEl('rcvFType'),invCatFilter:mkEl('invCatFilter'),inqTypeFilter:mkEl('inqTypeFilter'),
 reqTypeFilter:mkEl('reqTypeFilter'),retStatusF:mkEl('retStatusF')};
FILTERS.ordTypeFilter.value='物资采购';FILTERS.ordStatusFilter.value='待审批';FILTERS.rcvFDept.value='生产部';
FILTERS.rcvFCat.value='BB';FILTERS.rcvFType.value='est';FILTERS.invCatFilter.value='BB';FILTERS.inqTypeFilter.value='物资采购';
FILTERS.reqTypeFilter.value='物资采购';FILTERS.retStatusF.value='待审批';
function mkPage(id){const h={children:[{textContent:'标题'+id}],get firstChild(){return h.children[0]},
  insertBefore(n,ref){const i=h.children.indexOf(ref);h.children.splice(i<0?0:i,0,n);n.parent=h;return n},
  querySelector:sel=>sel==='.pgRefresh'?h.children.find(x=>String(x.className||'').includes('pgRefresh'))||null:null};
 const pg={id,querySelector:sel=>sel==='h2'?h:(sel==='.pgRefresh'?h.querySelector('.pgRefresh'):null)};return pg}
const PAGES=BUSINESS.concat(NOT_BUSINESS).map(mkPage), ACT_TAB={dataset:{z:'list'}};
global.document={querySelectorAll:sel=>sel==='.page'?PAGES:(sel==='#reqTabs button.act'?[ACT_TAB]:[]),
 querySelector:sel=>sel==='#reqTabs button.act'?ACT_TAB:null,getElementById:id=>FILTERS[id]||REG[id]||null,
 createElement:t=>({tagName:t,type:'',className:'',textContent:'',onclick:null}),addEventListener:(e,fn)=>{global.__ready=fn}};
const id=s=>document.getElementById(s);
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const alert=m=>CALLS.push(['alert',String(m)]);
const spy=(n,f)=>function(){CALLS.push([n].concat(f?f():[]))};
const loadPRequests=spy('loadPRequests',()=>[id('reqTypeFilter').value]), loadInquiries=spy('loadInquiries',()=>[id('inqTypeFilter').value]),
 loadOrders=spy('loadOrders',()=>[id('ordTypeFilter').value,id('ordStatusFilter').value]), loadApprovals=spy('loadApprovals'),
 loadContracts=spy('loadContracts'), loadReceivings=spy('loadReceivings',()=>[id('rcvFDept').value,id('rcvFCat').value,id('rcvFType').value]),
 loadPayments=spy('loadPayments'), reqTab=z=>CALLS.push(['reqTab',z]), loadReturns=spy('loadReturns',()=>[id('retStatusF').value]),
 loadCounts=spy('loadCounts'), loadAdjustments=spy('loadAdjustments'), loadExpenses=spy('loadExpenses'),
 loadInventory=c=>CALLS.push(['loadInventory',c]), loadSuppliers=spy('loadSuppliers'), loadSettlements=spy('loadSettlements'),
 loadInvoicePage=spy('loadInvoicePage'), loadLedger=spy('loadLedger'), loadSmallLedger=spy('loadSmallLedger'), loadAlertCenter=spy('loadAlertCenter');
__DSEL__
__RFS__
const btnOf=p=>p.querySelector('h2').children[0];
/* ---------- 刷新按钮 ---------- */
ck('18个业务页各1个刷新按钮', PAGES.filter(p=>BUSINESS.includes(p.id)).every(p=>p.querySelector('.pgRefresh')), null);
ck('按钮在标题左侧(第一个子节点)', PAGES.filter(p=>BUSINESS.includes(p.id)).every(p=>btnOf(p).className.includes('pgRefresh')), null);
ck('样式/文案同三方询价原按钮', PAGES.filter(p=>BUSINESS.includes(p.id)).every(p=>{const b=btnOf(p);
  return b.className==='btn btn-w btn-sm pgRefresh'&&b.textContent==='🔄 刷新'&&b.type==='button'}), null);
ck('非业务页不加按钮', PAGES.filter(p=>NOT_BUSINESS.includes(p.id)).every(p=>!p.querySelector('.pgRefresh')), null);
const n0=PAGES.map(p=>p.querySelector('h2').children.length); injectRefreshBtns();
ck('重复注入幂等', JSON.stringify(PAGES.map(p=>p.querySelector('h2').children.length))===JSON.stringify(n0), null);
ck('注册 DOMContentLoaded 兜底', typeof global.__ready==='function');
CALLS=[]; PAGES.filter(p=>BUSINESS.includes(p.id)).forEach(p=>btnOf(p).onclick());
ck('18次点击各自调 loader 且无报错', CALLS.filter(x=>x[0]==='alert').length===0 && new Set(CALLS.map(x=>x[0])).size>=15,
   CALLS.filter(x=>x[0]==='alert'));
ck('出库按当前标签页刷新(reqTab list)', CALLS.some(x=>x[0]==='reqTab'&&x[1]==='list'), null);
CALLS=[]; btnOf(PAGES.find(p=>p.id==='porders')).onclick();
ck('刷新采购订单沿用筛选(物资采购/待审批)', CALLS.some(x=>x[0]==='loadOrders'&&x[1]==='物资采购'&&x[2]==='待审批'), CALLS[0]);
CALLS=[]; btnOf(PAGES.find(p=>p.id==='preceivings')).onclick();
ck('刷新入库验收沿用筛选(生产部/BB/est)', CALLS.some(x=>x[0]==='loadReceivings'&&x[1]==='生产部'&&x[2]==='BB'&&x[3]==='est'), CALLS[0]);
CALLS=[]; btnOf(PAGES.find(p=>p.id==='pinventory')).onclick();
ck('刷新库存带当前分类(BB)', CALLS.some(x=>x[0]==='loadInventory'&&x[1]==='BB'), CALLS[0]);
ck('刷新不改动筛选控件', FILTERS.ordTypeFilter.value==='物资采购'&&FILTERS.invCatFilter.value==='BB', null);
/* ---------- 可滚动部门下拉 ---------- */
const h1=dselHtml('fReqDept',DEPTS,'','请选择（选填）','reqNoByDept');
ck('部门字段=只读输入框(非原生select)', h1.includes('id="fReqDept"')&&h1.includes('readonly')&&!h1.includes('<select'), null);
ck('面板结构可滚动(class=dselP)', h1.includes('class="dselP"'), null);
ck('含"请选择"清空项 + 15个部门选项', h1.includes('class="o mut"')&&DEPTS.every(d=>h1.includes(d)), null);
ck('选项顺序=接口拼音序(安监部在前/综合办在后)', h1.indexOf('安监部')<h1.indexOf('磅房')&&h1.indexOf('磅房')<h1.indexOf('采购部')&&h1.indexOf('综合办')>h1.indexOf('信息部'), null);
ck('选中回调=reqNoByDept(选部门刷新申请单号)', h1.includes("dselPick('fReqDept',this,'reqNoByDept')"), null);
const h2=dselHtml('fReqDept',DEPTS,'财务部','请选择（选填）','');
ck('编辑回显选中部门', h2.includes('value="财务部"')&&/class="o on"[^>]*>财务部</.test(h2), null);
mkEl('fReqDept'); const panel=mkEl('fReqDept_p'); panel.innerHTML=h1;
global.window={reqNoByDept:()=>{global.__n=(global.__n||0)+1}};
const opt=panel._kids.find(k=>k.textContent==='机电部'); dselPick('fReqDept',opt,'reqNoByDept');
ck('点选项→值写入+dselPick回调', id('fReqDept').value==='机电部'&&global.__n===1, id('fReqDept').value);
dselPick('fReqDept',panel._kids.find(k=>k.classList.contains('mut')),'');
ck('点"请选择"→清空', id('fReqDept').value==='', id('fReqDept').value);
dselSet('fReqDept','生产车队');
ck('程序化设值(草稿回填)同步高亮', id('fReqDept').value==='生产车队'&&panel._kids.find(k=>k.textContent==='生产车队').classList.contains('on'), null);
console.log('--- node: '+P+' 通过 / '+F+' 失败 ---');process.exit(F?1:0);
''').replace('__BUSINESS__', json.dumps(BUSINESS)).replace('__NOTBUSINESS__', json.dumps(NOT_BUSINESS))
     .replace('__DEPTS__', json.dumps(EXPECT_ORDER, ensure_ascii=False))
     .replace('__DSEL__', dsel.group(0)).replace('__RFS__', rfs.group(0)))
    r = subprocess.run(['node', js], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print('  node stderr: ' + r.stderr[:400])
    ck('node 断言全部通过', r.returncode == 0)

    print('[5] 静态: 面板 CSS / 三处改用面板 / 真实库未写入')
    ck('CSS 面板限高198px+纵向滚动', re.search(r'\.dselP\{[^}]*max-height:198px[^}]*overflow-y:auto', src) is not None)
    ck('采购申请(新建/编辑)+维修计划 三处改用面板', src.count("dselHtml('fReqDept'") == 2 and src.count("dselHtml('rpDept'") == 1)
    live = sqlite3.connect(LIVE)
    ck('真实库部门数未被本测试改动', live.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == before, before)
    live.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print('=== 结果: %d 通过 / %d 失败 ===' % (len(P), len(F)))
    if F:
        print('失败项: ' + ' | '.join(F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
