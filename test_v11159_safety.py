# -*- coding: utf-8 -*-
"""V11.159 数据安全专项: 带部门+钉钉绑定的用户, 只改角色→部门/钉钉必须保留"""
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
      await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({username:'admin', password:'admin123'}), credentials:'include'});
    }""")
    page.reload(wait_until='domcontentloaded')
    time.sleep(3)

    # 建测试用户(带部门id=4综合办 + 钉钉绑定)
    page.evaluate("""async () => {
      await fetch('/api/users', {method:'POST', headers:{'Content-Type':'application/json'},
        credentials:'include', body: JSON.stringify({username:'test_v11159f', name:'测试用户F【测试】',
        role:'员工', dept_id:4, phone:'13900000000', password:'test123456'})});
    }""")
    # 直接塞钉钉绑定(模拟已绑定用户)
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("UPDATE users SET dingtalk_userid='TESTDING1234567890' WHERE username='test_v11159f'")
    conn.commit(); conn.close()
    uid = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159f');
      return u? u.id : null;
    }""")
    check('测试用户已建(部门4+钉钉绑定)', uid is not None, f"uid={uid}")

    # 编辑弹窗应显示部门=综合办/钉钉绑定=TESTDING...
    page.evaluate(f"editUser({uid})")
    time.sleep(1.2)
    m0 = page.evaluate("""() => ({
      dept: document.getElementById('euDept').value,
      ding: document.getElementById('euDing').value
    })""")
    check('弹窗正确显示部门与钉钉绑定', m0['dept']=='4' and m0['ding']=='TESTDING1234567890', str(m0))
    # 只改角色, 点保存
    page.evaluate("""() => {
      document.getElementById('euRole').value='库管员';
      document.querySelector('#dmFooter .btn-p').click();
    }""")
    time.sleep(2.0)
    saved = page.evaluate("""async () => {
      const us = await (await fetch('/api/users', {credentials:'include'})).json();
      const u = us.find(x=>x.username==='test_v11159f');
      return u? {role:u.role, dept_id:u.dept_id, dingtalk_userid:u.dingtalk_userid} : null;
    }""")
    check('角色已改为库管员', saved and saved['role']=='库管员', json.dumps(saved, ensure_ascii=False))
    check('部门未被清空(仍=4)', saved and saved['dept_id']==4, f"dept_id={saved and saved['dept_id']}")
    check('钉钉绑定未被清空', saved and saved['dingtalk_userid']=='TESTDING1234567890', f"ding={saved and saved['dingtalk_userid']!r}")

    # 清理
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("DELETE FROM users WHERE username='test_v11159f'")
    conn.commit(); conn.close()
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM users),0) WHERE name='users'")
    conn.commit(); conn.close()
    check('测试用户已清理', True)
    browser.close()

print()
fails = [r for r in results if not r[1]]
print(f"结果: {len(results)-len(fails)}/{len(results)} 通过")
if fails:
    for n, ok, e in fails: print('  ❌', n, e)
    raise SystemExit(1)
