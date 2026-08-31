# -*- coding: utf-8 -*-
"""V11.159 调试2: 精确复现第一次测试的点击保存流程, 抓 PUT 请求与任何报错"""
import time, os, json, sqlite3
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width':1440,'height':900})
    logs = []
    page.on('console', lambda m: logs.append(('console', m.type, m.text[:400])))
    page.on('pageerror', lambda e: logs.append(('pageerror', 'ERR', str(e)[:400])))
    page.on('dialog', lambda d: (logs.append(('dialog', 'MSG', d.message[:300])), d.accept()))
    def on_resp(r):
        if '/api/users' in r.url:
            ct = r.headers.get('content-type') or ''
            body = r.text()[:400] if 'json' in ct else r.headers.get('content-type')
            logs.append(('resp', f"{r.request.method} {r.status} {r.url}", body))
    page.on('response', on_resp)

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
    time.sleep(3)

    page.evaluate("""async () => {
      await fetch('/api/users', {method:'POST', headers:{'Content-Type':'application/json'},
        credentials:'include', body: JSON.stringify({username:'test_v11159c', name:'测试用户C',
        role:'员工', phone:'13900000000', password:'test123456'})});
    }""")
    uid = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159c');
      return u? u.id : null;
    }""")
    print('uid =', uid)
    logs.clear()
    page.evaluate(f"editUser({uid})")
    time.sleep(1.5)
    # 捕获 saveUser 内部状态: 包装 alert 记录调用
    page.evaluate("""() => {
      window.__alerts=[];
      const _a=window.alert; window.alert=function(m){window.__alerts.push(String(m)); _a(m);};
      const _t=window.toast; window.toast=function(m){window.__alerts.push('[toast] '+String(m)); _t(m);};
    }""")
    # 改字段+点保存 (与第一次测试完全一致)
    page.evaluate("""() => {
      const role=document.getElementById('euRole'); role.value='采购员';
      const ph=document.getElementById('euPhone'); ph.value='13911112222';
      document.querySelector('#dmFooter .btn-p').click();
    }""")
    time.sleep(2.5)
    print('alerts/toasts:', page.evaluate("window.__alerts"))
    print('modal show:', page.evaluate("document.getElementById('detailModal').classList.contains('show')"))
    print('--- 网络/日志 ---')
    for t, a, b in logs:
        print(f'[{t}] {a} {b}')
    conn = sqlite3.connect(DB, timeout=30)
    u = conn.execute("SELECT role, phone FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    print('库中用户:', u)
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("DELETE FROM users WHERE username='test_v11159c'")
    conn.commit(); conn.close()
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM users),0) WHERE name='users'")
    conn.commit(); conn.close()
    browser.close()
