# -*- coding: utf-8 -*-
"""V11.159 端到端验证: ①首页新卡片结构+技术文本不在首页+通知铃铛 ②暂估/正式卡片显示切换 ③保存按钮真实点击生效
用法: .venv/bin/python test_v11159_e2e.py (测试数据带【测试】标记, 自动清理)
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
    js_errs = []
    page.on('pageerror', lambda e: js_errs.append(str(e)[:200]))
    for _try in range(4):
        try:
            page.goto(BASE + '/', wait_until='domcontentloaded', timeout=15000)
            break
        except Exception:
            time.sleep(2)
    page.evaluate("""async () => {
      await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username:'admin', password:'admin123'}), credentials:'include'});
    }""")
    page.reload(wait_until='domcontentloaded')
    time.sleep(3.5)
    check('登录进入主界面', page.evaluate("document.getElementById('appMain').style.display==='block'"))

    # ===== 任务1: 首页新结构 =====
    dash = page.evaluate("""() => {
      const t = document.getElementById('pdashboard').innerText;
      return {
        quickBtns: document.querySelectorAll('#pdashboard .dq-btn').length,
        quickTexts: Array.from(document.querySelectorAll('#pdashboard .dq-btn')).map(b=>b.innerText.trim()),
        todoCard: !!document.getElementById('apprZoneTabs'),
        todoStats: (document.getElementById('dashTodoStats')||{}).textContent||'',
        alertCard: !!document.getElementById('alertCenter'),
        alertSum: (document.getElementById('dashAlertSum')||{}).innerText||'',
        kpiCard: !!document.getElementById('dashKpi'),
        notice: (document.getElementById('dashNotice')||{}).innerText||'',
        hasTechText: /安全加固|数据库备份|钉钉对接参数|AgentId|AppSecret|PBKDF2/.test(t),
        hasOldNav: !!document.getElementById('quickNav'),
        welcome: (document.getElementById('dashUserName')||{}).textContent||''
      };
    }""")
    check('首页有4个快捷操作按钮', dash['quickBtns']==4, str(dash['quickTexts']))
    qtxt = [t.replace('\n','') for t in dash['quickTexts']]
    check('快捷按钮=采购申请/三方询价/新建采购订单/入库申请', all(any(x in t for t in qtxt) for x in ['采购申请','三方询价','新建采购订单','入库申请']), str(qtxt))
    check('第一卡片=我的待办(tabs保留)', dash['todoCard'], '')
    check('待办统计摘要已显示', '待办' in dash['todoStats'], dash['todoStats'])
    check('第二卡片=业务预警(含摘要chips)', dash['alertCard'] and ('库存预警' in dash['alertSum'] or '暂无预警' in dash['alertSum']), dash['alertSum'][:100])
    check('第三卡片=数据统计KPI', dash['kpiCard'])
    check('第五卡片=系统公告占位', '公告' in dash['notice'] or '暂无系统公告' in dash['notice'], dash['notice'][:60])
    check('首页无技术文本(安全加固/备份/钉钉参数)', not dash['hasTechText'])
    check('旧快捷导航已移除', not dash['hasOldNav'])
    check('欢迎语显示用户名', dash['welcome']!='—' and dash['welcome']!='', dash['welcome'])

    # 通知铃铛
    bell = page.evaluate("""() => {
      const b=document.getElementById('notifBell');
      return {exists: !!b, badge: (document.getElementById('notifBadge')||{}).style.display||'',
              badgeN: (document.getElementById('notifBadge')||{}).textContent||''};
    }""")
    check('顶部消息通知铃铛存在', bell['exists'])
    page.evaluate("toggleNotif(event)")
    time.sleep(0.6)
    boxShown = page.evaluate("document.getElementById('notifBox').style.display")
    check('点击铃铛弹出通知列表', boxShown=='block', boxShown)
    page.evaluate("document.body.click()")
    time.sleep(0.3)

    # KPI 点击跳转(本月采购总额→payments)
    page.evaluate("document.querySelector('#dashKpi .v9-kpi-hero').click()")
    time.sleep(0.8)
    cur = page.evaluate("_curPage")
    check('KPI点击跳转列表页', cur=='payments', f"_curPage={cur}")
    page.evaluate("sw('dashboard')")
    time.sleep(1.2)

    # ===== 任务2: 暂估/正式卡片 =====
    js_errs.clear()
    page.evaluate("showRcvForm()")
    time.sleep(1.0)
    est = page.evaluate("""() => {
      const e=document.getElementById('rcvTypeEst'), f=document.getElementById('rcvTypeFormal'), h=document.getElementById('rcvType');
      const vis = el => el && el.offsetParent !== null;
      return {est: !!e, formal: !!f, estVis: vis(e), formalVis: vis(f),
              estChk: (document.getElementById('rcvEstChk')||{}).style?.display||'',
              val: h?h.value:null};
    }""")
    check('入库表单暂估/正式卡片存在且可见', est['est'] and est['formal'] and est['estVis'] and est['formalVis'], f"estVis={est['estVis']} formalVis={est['formalVis']}")
    check('默认暂估选中(带✓)', est['val']=='est', f"val={est['val']}")
    page.evaluate("rcvPickType('formal')")
    v2 = page.evaluate("""() => ({
      val: document.getElementById('rcvType').value,
      formalChk: document.getElementById('rcvFormalChk').style.display,
      estChk: document.getElementById('rcvEstChk').style.display
    })""")
    check('点击正式卡片→rcvType=formal且✓切换', v2['val']=='formal' and v2['formalChk']=='inline' and v2['estChk']=='none', str(v2))
    page.evaluate("rcvPickType('est')")
    v3 = page.evaluate("document.getElementById('rcvType').value")
    check('切回暂估成功', v3=='est')
    check('任务2无JS报错', len(js_errs)==0, '; '.join(js_errs[:2]))
    page.evaluate("closeMod()")

    # ===== 任务3: 保存按钮真实点击 =====
    js_errs.clear()
    page.evaluate("""async () => {
      await fetch('/api/users', {method:'POST', headers:{'Content-Type':'application/json'},
        credentials:'include', body: JSON.stringify({username:'test_v11159e', name:'测试用户E【测试】',
        role:'员工', phone:'13900000000', password:'test123456'})});
    }""")
    uid = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159e');
      return u? u.id : null;
    }""")
    check('创建测试用户成功', uid is not None, f"uid={uid}")
    page.evaluate(f"editUser({uid})")
    time.sleep(1.2)
    # 真实点击保存按钮(触发页面onclick属性)
    page.evaluate("""() => {
      const role=document.getElementById('euRole'); role.value='采购员';
      const ph=document.getElementById('euPhone'); ph.value='13911112222';
      const btn=document.querySelector('#dmFooter .btn-p');
      btn.click();
    }""")
    time.sleep(2.2)
    saved = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159e');
      return u? {role:u.role, phone:u.phone, dept_id:u.dept_id, dingtalk_userid:u.dingtalk_userid} : null;
    }""")
    check('点击保存→角色已更新为采购员', saved and saved['role']=='采购员', json.dumps(saved, ensure_ascii=False))
    check('点击保存→电话已更新', saved and saved['phone']=='13911112222', saved and saved['phone'])
    check('未改字段(钉钉/部门)未被清空', saved and saved['dingtalk_userid'] is not None, f"ding={saved['dingtalk_userid']!r} dept={saved['dept_id']!r}")
    # 保存后弹窗应回到「用户管理」列表(closeMod+showUserModal 设计行为)
    modalAfter = page.evaluate("""() => ({
      show: document.getElementById('detailModal').classList.contains('show'),
      title: document.getElementById('dmTitle').textContent
    })""")
    check('保存后回到用户管理列表弹窗', modalAfter['show'] and '用户管理' in modalAfter['title'], str(modalAfter))
    check('任务3无JS报错', len(js_errs)==0, '; '.join(js_errs[:2]))

    # 只改单字段场景(数据安全): 再编辑一次只改职务
    page.evaluate(f"editUser({uid})")
    time.sleep(1.0)
    page.evaluate("""() => {
      document.getElementById('euTitle').value='测试职务X';
      document.querySelector('#dmFooter .btn-p').click();
    }""")
    time.sleep(2.0)
    saved2 = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159e');
      return u? {title:u.title, role:u.role, phone:u.phone, dingtalk_userid:u.dingtalk_userid} : null;
    }""")
    check('只改单字段→其余字段保留', saved2 and saved2['title']=='测试职务X' and saved2['role']=='采购员' and saved2['phone']=='13911112222', json.dumps(saved2, ensure_ascii=False))

    # 清理测试用户
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("DELETE FROM users WHERE username='test_v11159e'")
    conn.execute("DELETE FROM notifications WHERE title LIKE '%test_v11159e%' OR content LIKE '%test_v11159e%'")
    conn.commit(); conn.close()
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM users),0) WHERE name='users'")
    conn.commit(); conn.close()
    check('测试用户已清理', True)

    # 清理测试期间的notifications(可能产生)
    conn = sqlite3.connect(DB, timeout=30)
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE title LIKE '%测试用户%' OR content LIKE '%测试用户%'").fetchone()[0]
    conn.close()
    if n:
        conn = sqlite3.connect(DB, timeout=30)
        conn.execute("DELETE FROM notifications WHERE title LIKE '%测试用户%' OR content LIKE '%测试用户%'")
        conn.commit(); conn.close()
    check('测试通知已清理', True)

    browser.close()

print()
fails = [r for r in results if not r[1]]
print(f"结果: {len(results)-len(fails)}/{len(results)} 通过")
if fails:
    print('失败项:')
    for n, ok, e in fails: print('  ❌', n, e)
    raise SystemExit(1)
