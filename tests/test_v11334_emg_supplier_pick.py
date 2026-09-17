#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试: V11.334 应急询价/采购接单 供应商「下拉选择系统供应商」

用户需求(2026-09-17 截图): 应急采购 → 应急询价弹窗的「供应商（限1家）」原为纯手工输入框, 要能下拉选择系统供应商。

锁定五件事:
  1. 新接口 GET /api/suppliers/pick — 应急采购侧角色(EMG_BUY_ROLES: 采购员/库管员/部门负责人/分管领导/总经理/系统管理员)
     可取候选供应商, 且只返回选择所需非敏感字段(id/name/contact/phone)
  2. 原 GET /api/suppliers(V11.281: 含银行账号/税号, 仅 采购员/分管领导/总经理/系统管理员) 未被放宽 ——
     部门负责人走原接口仍为空, 证明本接口的必要性, 也证明敏感字段没外扩
  3. 停用供应商不进候选; 非白名单角色取到空数组; 未登录 401
  4. 前端真实代码(node 跑发货代码本身): 应急询价弹窗与采购接单弹窗均为「下拉 select + 输入框」,
     选项来自档案(名称｜联系人｜电话), 已存值回显选中/不在档案时补「（当前）」项, 空档案给手工输入文案,
     选中下拉自动填入输入框, 空供应商前端拦截
  5. 端到端: 下拉选中的供应商提交后仍能正常保存到应急单(inq_supplier)

跑法: .venv/Scripts/python.exe tests/test_v11334_emg_supplier_pick.py   (Mac: python3 tests/test_v11334_emg_supplier_pick.py)
全程 tempfile 副本库, 真实库零写入。
"""
import os, re, sys, json, shutil, sqlite3, subprocess, tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.path.join(BASE, 'data', 'purchase.db')
JSF = os.path.join(BASE, 'static', 'app.js')
PY = sys.executable
SUP_FIX = [{'id': 9001, 'name': '测试供应商甲', 'contact': '刘经理', 'phone': '13800000001'},
           {'id': 9002, 'name': '测试供应商乙', 'contact': '王老板', 'phone': '13800000002'}]
P, F = [], []


def ck(n, c, e=''):
    (P if c else F).append(n)
    print(('  OK   ' if c else '  FAIL ') + n + (('  | ' + str(e)[:220]) if e else ''))


def main():
    tmp = tempfile.mkdtemp(prefix='test_v11334_emg_sup-')
    sys.path.insert(0, BASE)
    import app as A

    live = sqlite3.connect(LIVE)
    live_before = live.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    live.close()

    print('[1] 项目自检')
    r = subprocess.run([PY, 'check_code.py'], cwd=BASE, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    ck('check_code 0错误0警告', r.returncode == 0 and '0 错误, 0 警告' in (r.stdout or ''),
       (r.stdout or '')[-160:])

    print('[2] 接口权限与字段边界(副本库)')
    copy = os.path.join(tmp, 'live.db')
    shutil.copy(LIVE, copy)
    A.DB = copy
    c = sqlite3.connect(copy)
    cols = [x[1] for x in c.execute("PRAGMA table_info(suppliers)").fetchall()]
    for s in SUP_FIX:
        c.execute("""INSERT INTO suppliers(name,contact,phone,status,bank,account,tax_id)
                     VALUES(?,?,?,?,?,?,?)""", (s['name'], s['contact'], s['phone'], '正常',
                                                '中国银行太原分行', '62220202020', '91140000TEST'))
    c.execute("INSERT INTO suppliers(name,contact,phone,status) VALUES(?,?,?,?)",
              ('测试供应商停用', '停用联系人', '13800000003', '停用'))
    # 固定测试账号密码, 走真实 /api/login 链路(不改真实库)
    for u in ('caigou_ceshi', 'mujiao', 'yuangong'):
        c.execute("UPDATE users SET password=? WHERE username=?", (A.hash_password('123456'), u))
    c.commit()
    ck('夹具已备(2家在档 + 1家停用)', c.execute(
        "SELECT COUNT(*) FROM suppliers WHERE name LIKE '测试供应商%'").fetchone()[0] == 3, cols)
    c.close()

    def login(user):
        cli = A.app.test_client()
        j = (cli.post('/api/login', json={'username': user, 'password': '123456'}).get_json() or {})
        return cli, j

    cli_buyer, jb = login('caigou_ceshi')
    ck('采购员登录成功', bool(jb.get('success')), jb)
    pick = cli_buyer.get('/api/suppliers/pick').get_json()
    names = [x.get('name') for x in (pick or [])]
    ck('采购员可取候选供应商(200 且含在档夹具)', isinstance(pick, list)
       and all(s['name'] in names for s in SUP_FIX), names[:6])
    ck('候选只含非敏感字段(id/name/contact/phone)',
       all(set(x.keys()) == {'id', 'name', 'contact', 'phone'} for x in (pick or [])),
       (pick or [{}])[0].keys() if pick else '空')
    ck('停用供应商不进候选', '测试供应商停用' not in names, names[:6])
    full = cli_buyer.get('/api/suppliers').get_json()
    ck('原 /api/suppliers 对采购员仍开放且含敏感字段', any('bank' in x and 'tax_id' in x for x in (full or [])),
       (full or [{}])[0].keys() if full else '空')

    cli_dept, jd = login('mujiao')
    ck('部门负责人登录成功', bool(jd.get('success')), jd)
    ck('部门负责人(应急侧角色)可取候选', len(cli_dept.get('/api/suppliers/pick').get_json() or []) >= 2)
    ck('V11.281 未被放宽: 部门负责人走原 /api/suppliers 仍为空',
       cli_dept.get('/api/suppliers').get_json() == [])

    cli_staff, js = login('yuangong')
    ck('员工登录成功', bool(js.get('success')), js)
    ck('非白名单角色(员工)取候选=空数组', cli_staff.get('/api/suppliers/pick').get_json() == [])
    ck('未登录取候选=401', A.app.test_client().get('/api/suppliers/pick').status_code == 401)
    ck('白名单口径 = EMG_BUY_ROLES(采购/库管/部门负责人/分管领导/总经理/管理员)',
       all(x in A.EMG_BUY_ROLES for x in ('采购员', '库管员', '部门负责人', '分管领导', '总经理', '系统管理员')),
       A.EMG_BUY_ROLES)

    print('[3] 端到端: 下拉选中供应商 → 应急询价保存落库')
    c2 = sqlite3.connect(copy)
    c2.row_factory = sqlite3.Row
    eid = c2.execute("""INSERT INTO emergency_purchases(emg_no,project,item_name,quantity,unit,status,
                        est_amount,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                     ('YJ-TEST-V11334', '回归测试', '测试物资', 10, '个', '待询价', 1000.0,
                      '2026-09-17 09:00:00', '2026-09-17 09:00:00')).lastrowid
    c2.commit(); c2.close()
    resp = cli_buyer.post('/api/emergency/%d/inquiry' % eid, json={
        'supplier': SUP_FIX[0]['name'], 'amount': 1130, 'tax_rate': 13,
        'valid_until': '2026-10-01', 'delivery_days': '2', 'pay_method': '月结30天'})
    jr = resp.get_json() or {}
    ck('询价保存成功(下拉选中的供应商被接受)', resp.status_code == 200 and jr.get('success'), jr)
    ck('含税/不含税/税额自动拆分正确', jr.get('amount_ex') == 1000.0 and jr.get('tax') == 130.0, jr)
    c2 = sqlite3.connect(copy); c2.row_factory = sqlite3.Row
    row = c2.execute("SELECT inq_supplier,status FROM emergency_purchases WHERE id=?", (eid,)).fetchone()
    ck('落库: inq_supplier=所选档案供应商 + 状态转「待定标」',
       row['inq_supplier'] == SUP_FIX[0]['name'] and row['status'] == '待定标', dict(row))
    ck('供应商已同步进档案(_emg_ensure_supplier 幂等)', c2.execute(
        "SELECT COUNT(*) FROM suppliers WHERE name=?", (SUP_FIX[0]['name'],)).fetchone()[0] == 1)
    c2.execute("DELETE FROM emergency_purchases WHERE id=?", (eid,))
    c2.commit(); c2.close()

    print('[4] 前端真实代码(node): 下拉选择 · 回显 · 自动填入 · 拦截')
    src = open(JSF, encoding='utf-8').read()
    help_blk = re.search(r"// V11\.334 应急询价/采购接单.*?(?=\nfunction emgInqCalc\()", src, re.S)
    form_blk = re.search(r"async function emgInquiryForm\(eid\)\{.*?(?=\nasync function emgInquirySave\(eid\)\{)", src, re.S)
    save_blk = re.search(r"async function emgInquirySave\(eid\)\{.*?(?=\nasync function emgAward\(eid\)\{)", src, re.S)
    supf_blk = re.search(r"async function emgSupplierForm\(eid\)\{.*?(?=\nasync function emgSupplierReject\(eid\)\{)", src, re.S)
    sups_blk = re.search(r"async function emgSupplierSave\(eid\)\{.*?(?=\n// ---- 临时入库 ----)", src, re.S)
    ck('切出5段真实实现(3处辅助 + 两个弹窗及其保存)',
       all(x for x in (help_blk, form_blk, save_blk, supf_blk, sups_blk)),
       [bool(x) for x in (help_blk, form_blk, save_blk, supf_blk, sups_blk)])

    js = os.path.join(tmp, 'run.js')
    open(js, 'w', encoding='utf-8').write(r'''
let API_CALLS=[],ALERTS=[],DM={t:'',h:'',f:''},P=0,F=0;
const ck=(n,c,e)=>{c?(P++,console.log('  OK   '+n)):(F++,console.log('  FAIL '+n+(e!==undefined?'  | '+JSON.stringify(e):'')))};
const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const CL=()=>({add(){},remove(){},toggle(){},contains:()=>false});
const REG={};
function mkEl(i){const e={id:i,value:'',innerHTML:'',textContent:'',classList:CL(),style:{},checked:false,files:[],addEventListener(){},insertAdjacentHTML(){}};REG[i]=e;return e}
global.window={_emgRows:[]};
global.document={getElementById:i=>REG[i]||mkEl(i),createElement:()=>({})};
const id=s=>document.getElementById(s);
function showDetail(t,h,f){DM={t:t,h:h,f:f}}
function closeMod(){} function loadEmergency(){} function toast(){} function emgInqCalc(){}
function alert(m){ALERTS.push(String(m))}
const SUP_FIX=__SUPFIX__;
async function api(p,o){API_CALLS.push([p,o||null]); if(p==='/suppliers/pick')return SUP_FIX; return {success:true,message:'已保存 ¥1000.00'}}
__HELP__
__FORM__
__SAVE__
__SUPF__
__SUPS__
const Q=s=>DM.h.indexOf(s)>=0;   /* 直接在渲染出的 HTML 上找子串(不 JSON 转义, 否则 " 变成 \" 假失败) */
async function main(){
  /* ---- 应急询价弹窗: 下拉选择 ---- */
  window._emgRows=[{id:1,emg_no:'YJ-20260917-0001',item_name:'打印机',spec:'',quantity:5,unit:'个',inq_supplier:'',inq_amount:0,est_amount:0,inq_tax_rate:13}];
  API_CALLS=[]; await emgInquiryForm(1);
  ck('弹窗拉取 /suppliers/pick 供应商档案', API_CALLS.some(x=>x[0]==='/suppliers/pick'), API_CALLS.map(x=>x[0]));
  ck('供应商字段=下拉select(不再是纯输入框)', Q('<select class="fc" id="emgSupSel"')&&Q('id="emgInqSup"'), DM.h.slice(DM.h.indexOf('供应商')));
  ck('下拉选项含档案供应商(名称｜联系人｜电话)', SUP_FIX.every(s=>Q('>'+s.name+'｜'+s.contact+'｜'+s.phone)));
  ck('选项含"从系统供应商档案选择"占位项', Q('— 从系统供应商档案选择 —'));
  ck('下拉 onchange 自动填入输入框', Q("onchange=\"emgPickSup('emgInqSup')\""));
  ck('保留手工输入入口(placeholder 说明)', Q('也可手工输入新供应商'));

  /* ---- 已存供应商回显选中 ---- */
  EMG_SUP_ARCHIVE=SUP_FIX;
  window._emgRows=[{id:3,emg_no:'YJ-3',item_name:'x',spec:'',quantity:1,unit:'个',inq_supplier:SUP_FIX[1].name,inq_amount:1000,inq_tax_rate:13}];
  await emgInquiryForm(3);
  ck('已存供应商在档案中→下拉选中该家', new RegExp('value="'+SUP_FIX[1].name+'" selected').test(DM.h), null);
  ck('输入框同步回显该供应商', Q('value="'+SUP_FIX[1].name+'"'), null);

  /* ---- 历史值不在档案 → 补「（当前）」项, 不丢值 ---- */
  EMG_SUP_ARCHIVE=[];
  window._emgRows=[{id:2,emg_no:'YJ-2',item_name:'x',spec:'',quantity:1,unit:'个',inq_supplier:'老供应商X',inq_amount:100,inq_tax_rate:13}];
  await emgInquiryForm(2);
  ck('档案为空→提示手工输入', Q('暂无档案供应商（请手工输入）'), null);
  ck('档案为空仍保留已存供应商(「（当前）」项 + 输入框回显)', Q('老供应商X（当前）')&&Q('value="老供应商X"'), null);

  /* ---- 选中 → 自动填入 ---- */
  mkEl('emgSupSel').value=SUP_FIX[1].name; mkEl('emgInqSup').value='';
  emgPickSup('emgInqSup');
  ck('选中下拉→名称写入输入框', id('emgInqSup').value===SUP_FIX[1].name, id('emgInqSup').value);
  id('emgSupSel').value=''; id('emgInqSup').value='保留值';
  emgPickSup('emgInqSup');
  ck('选占位项(空值)→不改动输入框', id('emgInqSup').value==='保留值', id('emgInqSup').value);
  id('emgSupSel').value=SUP_FIX[0].name; mkEl('emgSupplier').value='';
  emgPickSup('emgSupplier');
  ck('同一函数供采购接单复用(目标框可指定)', id('emgSupplier').value===SUP_FIX[0].name, id('emgSupplier').value);

  /* ---- 保存: 空供应商拦截 / 正常提交带 supplier ---- */
  ALERTS=[]; API_CALLS=[];
  mkEl('emgInqSup').value='   ';
  await emgInquirySave(9);
  ck('空供应商→前端拦截不发请求', ALERTS.length===1&&!API_CALLS.some(x=>String(x[0]).indexOf('/inquiry')>=0), ALERTS);
  ALERTS=[]; API_CALLS=[];
  mkEl('emgInqSup').value=' '+SUP_FIX[0].name+' ';
  mkEl('emgInqAmt').value='1130'; mkEl('emgInqRate').value='13';
  await emgInquirySave(9);
  const _p=API_CALLS.find(x=>String(x[0]).indexOf('/inquiry')>=0);
  ck('保存提交 → POST /emergency/9/inquiry 且 supplier 去空格', !!_p&&JSON.parse(_p[1].body).supplier===SUP_FIX[0].name, _p);

  /* ---- 采购接单弹窗同口径 ---- */
  EMG_SUP_ARCHIVE=SUP_FIX;
  window._emgRows=[{id:5,emg_no:'YJ-5',project:'p',item_name:'i',spec:'',quantity:2,unit:'个',need_arrive:'',est_amount:100}];
  await emgSupplierForm(5);
  const H=REG.dmBody.innerHTML;
  ck('采购接单弹窗也是下拉选择 + 输入框', H.indexOf('<select class="fc" id="emgSupSel"')>=0&&H.indexOf('id="emgSupplier"')>=0, null);
  ck('接单下拉含档案供应商', SUP_FIX.every(s=>H.indexOf('>'+s.name+'｜'+s.contact)>=0), null);
  ALERTS=[]; API_CALLS=[];
  mkEl('emgSupplier').value='';
  await emgSupplierSave(5);
  ck('接单空供应商→前端拦截不发请求', ALERTS.length===1&&!API_CALLS.some(x=>String(x[0]).indexOf('/supplier')>=0), ALERTS);
  console.log('--- node: '+P+' 通过 / '+F+' 失败 ---');process.exit(F?1:0);
}
main().catch(e=>{console.log('node 运行异常: '+e);process.exit(9)});
'''.replace('__SUPFIX__', json.dumps(SUP_FIX, ensure_ascii=False))
        .replace('__HELP__', help_blk.group(0)).replace('__FORM__', form_blk.group(0))
        .replace('__SAVE__', save_blk.group(0)).replace('__SUPF__', supf_blk.group(0))
        .replace('__SUPS__', sups_blk.group(0)))
    r = subprocess.run(['node', js], capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print('  node stderr: ' + r.stderr[:500])
    ck('node 断言全部通过', r.returncode == 0)

    print('[5] 真实库零写入 + 清理')
    live = sqlite3.connect(LIVE)
    live_after = live.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    residual = live.execute("SELECT COUNT(*) FROM suppliers WHERE name LIKE '测试供应商%'").fetchone()[0]
    emg = live.execute("SELECT COUNT(*) FROM emergency_purchases WHERE emg_no='YJ-TEST-V11334'").fetchone()[0]
    live.close()
    ck('真实库供应商数未变', live_after == live_before, '%s → %s' % (live_before, live_after))
    ck('真实库无测试夹具残留', residual == 0 and emg == 0, 'suppliers=%s emg=%s' % (residual, emg))
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print('=== 结果: %d 通过 / %d 失败 ===' % (len(P), len(F)))
    if F:
        print('失败项: ' + ' | '.join(F))
    return 1 if F else 0


if __name__ == '__main__':
    sys.exit(main())
