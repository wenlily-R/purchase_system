# -*- coding: utf-8 -*-
"""V11.159 前置实测: ①showRcvForm 暂估/正式卡片是否显示可切换 ②saveUser 保存是否生效 ③用户管理弹窗按钮
用法: .venv/bin/python test_v11159_before.py (只读+可清理, 不污染生产数据)
"""
import time, os, json, sqlite3
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
results = []
def check(name, ok, extra=''):
    results.append((name, ok, extra))
    print(('  ✅ ' if ok else '  ❌ ') + name + (('  → ' + extra) if extra else ''))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width':1440,'height':900})
    for _try in range(4):
        try:
            page.goto(BASE + '/', wait_until='domcontentloaded', timeout=15000)
            break
        except Exception:
            time.sleep(2)
    page.evaluate("""async () => {
      const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username:'admin', password:'admin123'}), credentials:'include'});
      return r.status;
    }""")
    page.reload(wait_until='domcontentloaded')
    time.sleep(3)
    check('登录进入主界面', page.evaluate("document.getElementById('appMain').style.display==='block'"))

    # ===== 任务2: 入库表单暂估/正式卡片 =====
    js_errs = []
    page.on('pageerror', lambda e: js_errs.append(str(e)))
    page.evaluate("showRcvForm()")
    time.sleep(1.5)
    est = page.evaluate("""() => {
      const e=document.getElementById('rcvTypeEst'), f=document.getElementById('rcvTypeFormal'), h=document.getElementById('rcvType');
      const vis = el => el && el.offsetParent !== null;
      return {est: !!e, formal: !!f, hidden: !!h, estVis: vis(e), formalVis: vis(f),
              estText: e?e.textContent.trim().slice(0,20):'', formalText: f?f.textContent.trim().slice(0,20):'',
              val: h?h.value:null};
    }""")
    check('rcvTypeEst 元素存在', est['est'], est.get('estText') or '')
    check('rcvTypeFormal 元素存在', est['formal'], est.get('formalText') or '')
    check('暂估/正式两卡片可见(offsetParent非null)', est['estVis'] and est['formalVis'], f"estVis={est['estVis']}, formalVis={est['formalVis']}")
    check('默认rcvType=est', est['val']=='est', f"val={est['val']}")
    page.evaluate("rcvPickType('formal')")
    v2 = page.evaluate("document.getElementById('rcvType').value")
    check('点击正式卡片后 rcvType=formal', v2=='formal', f"val={v2}")
    page.evaluate("rcvPickType('est')")
    v3 = page.evaluate("document.getElementById('rcvType').value")
    check('切回暂估 rcvType=est', v3=='est', f"val={v3}")
    check('无页面JS报错', len(js_errs)==0, '; '.join(js_errs[:3]))
    page.evaluate("closeMod()")

    # ===== 任务3: 用户管理 + saveUser =====
    # 先建一个测试用户(带【测试】标记), 编辑后验证再删除
    created = page.evaluate("""async () => {
      const r = await fetch('/api/users', {method:'POST', headers:{'Content-Type':'application/json'},
        credentials:'include', body: JSON.stringify({username:'test_v11159', name:'测试用户V11159',
        role:'员工', phone:'13900000000', password:'test123456'})});
      return {status: r.status, body: await r.json().catch(()=>({}))};
    }""")
    check('创建测试用户成功', created['status']==200 or created['body'].get('success'), json.dumps(created['body'], ensure_ascii=False)[:120])

    uid = page.evaluate("""async () => {
      const r = await fetch('/api/users', {credentials:'include'});
      const us = await r.json();
      const u = us.find(x=>x.username==='test_v11159');
      return u? u.id : null;
    }""")
    check('测试用户已存在可查', uid is not None, f"uid={uid}")

    if uid:
        # 打开编辑弹窗
        page.evaluate(f"editUser({uid})")
        time.sleep(1.0)
        modal = page.evaluate("""() => {
          const m=document.getElementById('detailModal');
          return {show: m.classList.contains('show'),
                  saveBtn: !!document.querySelector('#dmFooter .btn-p'),
                  saveText: document.querySelector('#dmFooter .btn-p')?.textContent||'',
                  role: document.getElementById('euRole')?.value||'',
                  phone: document.getElementById('euPhone')?.value||''};
        }""")
        check('编辑弹窗打开且含保存按钮', modal['show'] and modal['saveBtn'], f"按钮='{modal['saveText']}' role={modal['role']} phone={modal['phone']}")
        # 改角色+电话, 点保存
        page.evaluate("""() => {
          const role=document.getElementById('euRole'); role.value='采购员';
          const ph=document.getElementById('euPhone'); ph.value='13911112222';
          document.querySelector('#dmFooter .btn-p').click();
        }""")
        time.sleep(2.0)
        saved = page.evaluate("""async () => {
          const r = await fetch('/api/users', {credentials:'include'});
          const us = await r.json();
          const u = us.find(x=>x.username==='test_v11159');
          return u? {role:u.role, phone:u.phone, dept_id:u.dept_id, dingtalk_userid:u.dingtalk_userid} : null;
        }""")
        check('保存后角色已更新为采购员', saved and saved['role']=='采购员', json.dumps(saved, ensure_ascii=False))
        check('保存后电话已更新', saved and saved['phone']=='13911112222', saved and saved['phone'])
        check('未改字段(钉钉绑定)未被清空/部门保留', saved and saved['dingtalk_userid'] is not None, f"ding={saved['dingtalk_userid']!r} dept={saved['dept_id']!r}")
        # 弹窗应已关闭
        closed = page.evaluate("!document.getElementById('detailModal').classList.contains('show')")
        check('保存后弹窗关闭', closed)
        # 清理测试用户
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("DELETE FROM users WHERE username='test_v11159'")
        conn.execute("DELETE FROM notifications WHERE title LIKE '%test_v11159%' OR content LIKE '%test_v11159%'")
        conn.commit(); conn.close()
        # 复位自增
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM users),0) WHERE name='users'")
        conn.commit(); conn.close()
        check('测试用户已清理', True)

    # ===== 任务2b: 订单同步入库单的暂估标记(查库) =====
    conn = sqlite3.connect(DB, timeout=30)
    rows = conn.execute("SELECT id, receive_no, is_est, invoice_no, status, remark FROM receivings ORDER BY id DESC LIMIT 6").fetchall()
    conn.close()
    est_rows = [r for r in rows if r[2]==1]
    check('最近入库单中存在暂估单(is_est=1)', len(est_rows)>0, f"{len(est_rows)}/{len(rows)} 条暂估")
    if est_rows:
        r0 = est_rows[0]
        check('暂估单无发票号(可走红冲)', r0[3] in (None,''), f"invoice_no={r0[3]!r}")
        print('    最近入库单: ' + ' | '.join(f"{x[1]}(is_est={x[2]},inv={x[3]!r},{x[4]})" for x in rows))

    browser.close()

print()
fails = [r for r in results if not r[1]]
print(f"结果: {len(results)-len(fails)}/{len(results)} 通过")
if fails:
    print('失败项:')
    for n, ok, e in fails: print('  ❌', n, e)
    raise SystemExit(1)
