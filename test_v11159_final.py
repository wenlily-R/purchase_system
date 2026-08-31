# -*- coding: utf-8 -*-
"""V11.159 收尾验证: ①入库列表暂估标签+发票核对按钮 ②钉钉窄屏自适应 ③系统设置技术卡片填充"""
import time, os, json
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
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

    # ① 入库验收列表: 暂估标签 + 发票核对按钮
    page.evaluate("sw('receivings')")
    time.sleep(2.0)
    rcv = page.evaluate("""() => {
      const t = document.getElementById('receivingsTable').innerText;
      const html = document.getElementById('receivingsTable').innerHTML;
      return {
        hasEstTag: /暂估/.test(t),
        hasInvMatch: html.includes('发票核对'),
        estCount: (t.match(/暂估/g)||[]).length,
        topRemind: /发票未回/.test(t)
      };
    }""")
    check('入库列表显示🟠暂估标签', rcv['hasEstTag'], f"出现{rcv['estCount']}处")
    check('暂估行显示🧾发票核对按钮', rcv['hasInvMatch'])
    check('顶部暂估发票未回提醒条', rcv['topRemind'])

    # ② 钉钉内嵌窄屏(375px)自适应
    page.set_viewport_size({'width':375,'height':700})
    page.evaluate("sw('dashboard')")
    time.sleep(1.5)
    narrow = page.evaluate("""() => {
      const doc = document.documentElement;
      const q = document.querySelector('.dash-quick');
      const top = document.querySelector('.dash-top');
      return {
        noHScroll: doc.scrollWidth <= window.innerWidth + 5,
        quickWrap: q ? getComputedStyle(q).flexWrap : '',
        btnCount: document.querySelectorAll('.dq-btn').length
      };
    }""")
    check('375px窄屏无横向溢出', narrow['noHScroll'], f"scrollWidth={narrow['noHScroll']}")
    check('快捷按钮换行显示', narrow['quickWrap']=='wrap' and narrow['btnCount']==4, f"wrap={narrow['quickWrap']}")

    # ③ 系统设置技术卡片填充
    page.set_viewport_size({'width':1440,'height':900})
    page.evaluate("sw('system')")
    time.sleep(2.5)
    tech = page.evaluate("""() => ({
      version: (document.getElementById('techVersion')||{}).value||'',
      db: (document.getElementById('techDb')||{}).value||'',
      backup: (document.getElementById('techBackup')||{}).value||'',
      pub: (document.getElementById('techPubUrl')||{}).value||''
    })""")
    check('系统设置技术卡片-版本已填充', 'V5.1' in tech['version'], tech['version'])
    check('系统设置技术卡片-数据库状态已填充', '✅' in tech['db'], tech['db'])
    check('系统设置技术卡片-备份时间已填充', '✅' in tech['backup'] or '尚未' in tech['backup'], tech['backup'])
    check('系统设置技术卡片-公网地址已填充', tech['pub'].startswith('https://'), tech['pub'])
    page.evaluate("sw('dashboard')")
    time.sleep(1.2)
    # 钉钉/飞书配置页入口可用
    page.evaluate("sw('dingtalk')")
    time.sleep(1.0)
    dtOk = page.evaluate("!!document.getElementById('dAppKey')")
    check('钉钉对接页正常打开', dtOk)
    page.evaluate("sw('feishu')")
    time.sleep(1.0)
    fsOk = page.evaluate("!!document.getElementById('fAppId')")
    check('飞书对接页正常打开', fsOk)

    browser.close()

print()
fails = [r for r in results if not r[1]]
print(f"结果: {len(results)-len(fails)}/{len(results)} 通过")
if fails:
    for n, ok, e in fails: print('  ❌', n, e)
    raise SystemExit(1)
