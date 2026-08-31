# -*- coding: utf-8 -*-
"""V11.159 调试: saveUser 点击后到底发生了什么 — 抓网络响应+console+dialog"""
import time, os, json, sqlite3
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width':1440,'height':900})
    logs = []
    page.on('console', lambda m: logs.append(('console', m.type, m.text[:300])))
    page.on('pageerror', lambda e: logs.append(('pageerror', 'ERR', str(e)[:300])))
    page.on('dialog', lambda d: (logs.append(('dialog', 'MSG', d.message[:300])), d.accept()))
    def on_resp(r):
        if 'users' in r.url:
            logs.append(('resp', r.status, r.url))
            try:
                if 'application/json' in (r.headers.get('content-type') or ''):
                    logs.append(('body', r.status, r.text()[:300]))
            except Exception: pass
    page.on('response', on_resp)

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

    # 建测试用户
    page.evaluate("""async () => {
      await fetch('/api/users', {method:'POST', headers:{'Content-Type':'application/json'},
        credentials:'include', body: JSON.stringify({username:'test_v11159b', name:'测试用户B',
        role:'员工', phone:'13900000000', password:'test123456'})});
    }""")
    uid = page.evaluate("""async () => {
      const r = await fetch('/api/users', {credentials:'include'});
      const us = await r.json();
      const u = us.find(x=>x.username==='test_v11159b');
      return u? u.id : null;
    }""")
    print('测试用户 uid =', uid)
    logs.clear()
    page.evaluate(f"editUser({uid})")
    time.sleep(1.2)
    # 手动触发 saveUser 看结果
    ret = page.evaluate("""async () => {
      try {
        await saveUser(14);
        return 'saveUser-resolved';
      } catch(e) { return 'saveUser-threw: ' + (e && e.message || e); }
    }""".replace('14', str(uid)))
    print('saveUser 返回值:', ret)
    time.sleep(1.0)
    # 弹窗状态
    print('弹窗show:', page.evaluate("document.getElementById('detailModal').classList.contains('show')"))
    print('--- 日志 ---')
    for t, a, b in logs:
        print(f'[{t}] {a} {b}')
    # 查库确认
    conn = sqlite3.connect(DB, timeout=30)
    u = conn.execute("SELECT role, phone, dingtalk_userid, dept_id FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    print('库中用户:', u)
    # 清理
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("DELETE FROM users WHERE username='test_v11159b'")
    conn.commit(); conn.close()
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM users),0) WHERE name='users'")
    conn.commit(); conn.close()
    browser.close()
