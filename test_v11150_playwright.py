# -*- coding: utf-8 -*-
"""V11.150 浏览器端到端实测: ①连点详情只弹一个窗/只发一次请求 ②切页列表缓存命中不重复拉取
用法: .venv/bin/python test_v11150_playwright.py"""
import re, time, os
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:5899'
results = []
def check(name, ok, extra=''):
    results.append((name, ok, extra))
    print(('  ✅ ' if ok else '  ❌ ') + name + (('  → ' + extra) if extra else ''))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page()
    reqs = []
    page.on('request', lambda r: reqs.append(r.url))

    # 登录(走页面JS同款 fetch 拿 cookie, 再刷新进主界面)
    # macOS headless chromium 首次启动偶发连不上本机(代理探测竞态), 重试即可
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
    time.sleep(3)  # 等 /me 重试完成进主界面
    check('登录后进入主界面(appMain可见)', page.evaluate("document.getElementById('appMain').style.display==='block'"))

    # 打开询价页
    page.evaluate("sw('inquiries')")
    time.sleep(1.5)
    n_btns = page.evaluate("document.querySelectorAll('#inquiriesTable button.lnk').length")
    check('询价列表有详情按钮', n_btns > 0, f'按钮数={n_btns}')
    if n_btns == 0:
        print('  无询价数据, 跳过连点测试'); browser.close(); raise SystemExit(1)

    base_cnt = sum(1 for u in reqs if re.search(r'/api/inquiries/\d+$', u))
    # 连点3下(同步触发, 模拟隧道慢时用户快速多点)
    page.evaluate("""() => {
      const btn = document.querySelector('#inquiriesTable button.lnk');
      btn.click(); btn.click(); btn.click();
    }""")
    time.sleep(1.5)
    detail_reqs = [u for u in reqs if re.search(r'/api/inquiries/\d+$', u)]
    new_cnt = len(detail_reqs) - base_cnt
    modal_open = page.evaluate("document.getElementById('detailModal').classList.contains('show')")
    check('连点3下详情 → 只发出1次详情请求', new_cnt == 1, f'实际={new_cnt}次')
    check('弹窗正常打开', modal_open)
    title = page.evaluate("document.getElementById('dmTitle').textContent")
    check('弹窗标题为询价单', '询价单' in title, title)

    # 关闭弹窗
    page.evaluate("closeMod()")
    # 切页缓存验证: 记录 /api/inquiries 列表请求数, 切订单页再切回询价页
    list_cnt = sum(1 for u in reqs if u.endswith('/api/inquiries'))
    page.evaluate("sw('orders')"); time.sleep(0.8)
    page.evaluate("sw('inquiries')"); time.sleep(1.2)
    list_cnt2 = sum(1 for u in reqs if u.endswith('/api/inquiries'))
    check('25秒内切回询价页 → 列表不再重复拉取(缓存命中)', list_cnt2 == list_cnt, f'切页前={list_cnt}, 切页后={list_cnt2}')

    # 打开详情后数据正常渲染(供应商报价区存在)
    page.evaluate("""() => {
      const btn = document.querySelector('#inquiriesTable button.lnk');
      btn.click();
    }""")
    time.sleep(1.5)
    has_sup = page.evaluate("document.body.innerHTML.includes('供应商报价对比')")
    check('详情内容正常渲染(供应商报价对比区)', has_sup)
    page.evaluate("closeMod()")

    # JS 运行无致命错误(页面交互函数可用)
    js_ok = page.evaluate("typeof __guardOpen==='function' && typeof __cacheGet==='function' && typeof showInquiry==='function'")
    check('V11.150 全局函数已挂载', js_ok)

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n结果: {len(results)-len(fails)}/{len(results)} 通过")
for name, ok, extra in results:
    print(('✅' if ok else '❌'), name)
raise SystemExit(1 if fails else 0)
