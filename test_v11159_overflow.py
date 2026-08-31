# -*- coding: utf-8 -*-
"""定位 375px 窄屏横向溢出的元素"""
import time, os
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
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
    time.sleep(3.5)
    page.set_viewport_size({'width':375,'height':700})
    time.sleep(1.0)
    wide = page.evaluate("""() => {
      const vw = window.innerWidth;
      const out = [];
      document.querySelectorAll('body *').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width > vw + 2 && r.right > vw + 2) {
          const cls = (el.className && typeof el.className==='string') ? el.className.slice(0,40) : '';
          const idn = el.id || '';
          out.push(`${el.tagName.toLowerCase()}#${idn}.${cls} w=${Math.round(r.width)} left=${Math.round(r.left)} right=${Math.round(r.right)}`);
        }
      });
      return {vw, count: out.length, items: out.slice(0, 25)};
    }""")
    print('viewport:', wide['vw'], '溢出元素数:', wide['count'])
    for it in wide['items']:
        print('  ', it)
    browser.close()
