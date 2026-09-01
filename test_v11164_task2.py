# -*- coding: utf-8 -*-
"""V11.164 任务2端到端验证: /inq/<token> 外网商家报价页
A. 本地路由级: 无效链接/已结束/已截止/已删除/空数量/已报价回显 各场景
B. 公网完整流程: 建测试询价→公网打开→提交报价(含税单价/含运总价/厂家备注)→系统入库验证→清理"""
import sys, os, json, sqlite3, urllib.request, urllib.parse, urllib.error, http.cookiejar, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as appmod
appmod.start_instances = lambda *a, **k: None

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'data/purchase.db')
PUB = open(os.path.join(BASE, 'data/public_url.txt')).read().strip().rstrip('/')

def q(sql, args=()):
    c = sqlite3.connect(DB, timeout=30); c.row_factory = sqlite3.Row
    r = c.execute(sql, args).fetchall(); c.close(); return r
def ex(sql, args=()):
    c = sqlite3.connect(DB, timeout=30); c.execute(sql, args); c.commit(); c.close()

passed = []
def check(name, cond, extra=''):
    passed.append((name, cond))
    print(('✅' if cond else '❌'), name, ('' if cond else (' | ' + str(extra)[:200])))

# 前置清理(幂等): 清掉上次崩溃残留
for rid_row in q("SELECT id FROM purchase_requests WHERE req_no LIKE 'SC-TEST-WL%' OR purpose LIKE '%【测试】%任务2%'"):
    ex("DELETE FROM inquiry_suppliers WHERE inquiry_id IN (SELECT id FROM inquiries WHERE req_id=?)", (rid_row['id'],))
    ex("DELETE FROM inquiries WHERE req_id=?", (rid_row['id'],))
    ex("DELETE FROM request_items WHERE req_id=?", (rid_row['id'],))
ex("DELETE FROM purchase_requests WHERE req_no LIKE 'SC-TEST-WL%' OR purpose LIKE '%【测试】%任务2%'")
ex("DELETE FROM logs WHERE detail LIKE '%SC-TEST-WL%' OR detail LIKE '%【测试】%任务2%'")

# ================= A. 本地路由级场景 =================
client = appmod.app.test_client()
r = client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
check('A0 admin登录', r.status_code == 200, r.text[:80])

# 造测试申请(已通过) + 明细
ex("INSERT INTO purchase_requests(req_no,dept,requester,requester_id,purpose,status,apply_date) VALUES('SC-TEST-WL-01','测试部','测试员',1,'【测试】外网链接验证-任务2','已通过','2026-09-01')")
prid = q("SELECT id FROM purchase_requests WHERE req_no='SC-TEST-WL-01'")[0]['id']
ex("INSERT INTO request_items(req_id,item_name,spec,quantity,unit,total_price) VALUES(?,'【测试】外网链接验证物资','A型',2,'个',100)", (prid,))
ex("INSERT INTO request_items(req_id,item_name,spec,quantity,unit,total_price) VALUES(?,'【测试】轴承','6205',5,'个',250)", (prid,))

# 建询价(3家, 截止=7天后)
r = client.post('/api/inquiries', json={
    'req_id': prid, 'deadline': '2026-09-09',
    'suppliers': [{'name': '【测试】厂家A', 'contact': '张三', 'phone': '13800000001'},
                  {'name': '【测试】厂家B', 'contact': '李四', 'phone': '13800000002'},
                  {'name': '【测试】厂家C', 'contact': '王五', 'phone': '13800000003'}]})
check('A1 建询价成功', r.status_code == 200, r.text[:120])
iid = r.get_json()['id']
tokA = q("SELECT token FROM inquiry_suppliers WHERE inquiry_id=? AND supplier_name='【测试】厂家A'", (iid,))[0]['token']

# 场景1: 正常询价中页面
r = client.get('/inq/' + tokA)
html = r.get_data(as_text=True)
check('A2 正常页面200含表单', r.status_code == 200 and '含税单价' in html and 'shipTotal' in html and 'supRemark' in html and '提交报价' in html)
check('A3 页面无None裸值', 'None' not in html)
check('A4 页面无内网地址写死', ('127.0.0.1' not in html) and ('172.16.' not in html) and ('192.168.' not in html) and ('lhr.life' not in html))

# 场景2: 无效token
r = client.get('/inq/deadbeef' + uuid.uuid4().hex)
check('A5 无效token友好提示', r.status_code == 200 and '报价链接无效或已失效' in r.get_data(as_text=True))

# 场景3: 询价已结束(已生成订单)
ex("UPDATE inquiries SET status='已生成订单' WHERE id=?", (iid,))
r = client.get('/inq/' + tokA)
check('A6 已定标友好提示', r.status_code == 200 and '已完成定标' in r.get_data(as_text=True) and '提交报价' not in r.get_data(as_text=True))
ex("UPDATE inquiries SET status='询价中' WHERE id=?", (iid,))

# 场景4: 已截止(deadline过去)
ex("UPDATE inquiries SET deadline='2026-01-01' WHERE id=?", (iid,))
r = client.get('/inq/' + tokA)
check('A7 已截止友好提示', r.status_code == 200 and '报价已截止' in r.get_data(as_text=True) and '提交报价' not in r.get_data(as_text=True))
ex("UPDATE inquiries SET deadline='2026-09-09' WHERE id=?", (iid,))

# 场景5: 询价单被删(供应商残留)
ex("DELETE FROM inquiries WHERE id=?", (iid,))
r = client.get('/inq/' + tokA)
check('A8 询价单已删友好提示(不500)', r.status_code == 200 and '询价单不存在' in r.get_data(as_text=True))
# 重建询价(同申请, 新id)
r = client.post('/api/inquiries', json={
    'req_id': prid, 'deadline': '2026-09-09',
    'suppliers': [{'name': '【测试】厂家A', 'contact': '张三', 'phone': '13800000001'},
                  {'name': '【测试】厂家B', 'contact': '李四', 'phone': '13800000002'}]})
iid = r.get_json()['id']
tokA = q("SELECT token FROM inquiry_suppliers WHERE inquiry_id=? AND supplier_name='【测试】厂家A'", (iid,))[0]['token']

# 场景6: 数量为NULL
ex("UPDATE request_items SET quantity=NULL WHERE req_id=? AND item_name='【测试】轴承'", (prid,))
r = client.get('/inq/' + tokA)
html = r.get_data(as_text=True)
check('A9 数量NULL不显示None', 'None' not in html)
ex("UPDATE request_items SET quantity=5 WHERE req_id=? AND item_name='【测试】轴承'", (prid,))

# 场景7: 提交报价 + 回显
r = client.post('/api/inquiry/vendor/' + tokA + '/quote', json={
    'quote_price': 358.0, 'quote_remark': '含税含运，7天交货',
    'details': [
        {'unit_price': 12.0, 'qty': 2, 'delivery': '7天', 'warranty': '3个月', 'brand': '测试牌', 'remark': '行备注1'},
        {'unit_price': 66.8, 'qty': 5, 'delivery': '7天', 'warranty': '1年', 'brand': '测试牌', 'remark': ''}]})
check('A10 报价提交成功', r.status_code == 200 and r.get_json().get('success'), r.text[:150])
row = q("SELECT quote_price, quote_details FROM inquiry_suppliers WHERE token=?", (tokA,))[0]
check('A11 quote_price=358入库', abs(row['quote_price'] - 358.0) < 0.001, row['quote_price'])
qd = json.loads(row['quote_details'])
check('A12 quote_details 2行含单价/品牌/备注', len(qd) == 2 and qd[0]['unit_price'] == 12.0 and qd[0]['brand'] == '测试牌' and qd[1]['unit_price'] == 66.8)
r = client.get('/inq/' + tokA)
html = r.get_data(as_text=True)
check('A13 已报价回显+修改提示', '已报价' in html and 'value="12.0"' in html and 'value="66.8"' in html, '')

# ================= B. 公网完整流程 =================
# 造第二条测试申请
ex("INSERT INTO purchase_requests(req_no,dept,requester,requester_id,purpose,status,apply_date) VALUES('SC-TEST-WL-02','测试部','测试员',1,'【测试】公网完整流程-任务2','已通过','2026-09-01')")
prid2 = q("SELECT id FROM purchase_requests WHERE req_no='SC-TEST-WL-02'")[0]['id']
ex("INSERT INTO request_items(req_id,item_name,spec,quantity,unit,total_price) VALUES(?,'【测试】公网报价物资','B型',3,'个',300)", (prid2,))

# 公网登录
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def post(url, data):
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'})
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')
def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

st, body = post(PUB + '/api/login', {'username': 'admin', 'password': 'admin123'})
check('B1 公网登录', st == 200 and '"success"' in body, body[:100])
st, body = post(PUB + '/api/inquiries', {
    'req_id': prid2, 'deadline': '2026-09-09',
    'suppliers': [{'name': '【测试】公网厂家X', 'contact': '赵六', 'phone': '13800000009'},
                  {'name': '【测试】公网厂家Y', 'contact': '钱七', 'phone': '13800000010'}]})
check('B2 公网建询价', st == 200 and '"success"' in body, body[:150])
iid2 = json.loads(body)['id'] if st == 200 else None
tokB = q("SELECT token FROM inquiry_suppliers WHERE inquiry_id=?", (iid2,))[0]['token'] if iid2 else ''

# 公网打开商家页
st, html = get(PUB + '/inq/' + tokB)
check('B3 公网打开商家页200含表单', st == 200 and '含税单价' in html and 'shipTotal' in html and 'supRemark' in html, html[:80])
check('B4 公网页面无内网地址/None', ('127.0.0.1' not in html) and ('lhr.life' not in html) and ('None' not in html))

# 公网提交报价(模拟商家填表: 含税单价×3行 + 含运总价 + 厂家备注)
st, body = post(PUB + '/api/inquiry/vendor/' + tokB + '/quote', {
    'quote_price': 666.0, 'quote_remark': '含税含运价，款到发货',
    'details': [{'unit_price': 222.0, 'qty': 3, 'delivery': '5天', 'warranty': '6个月', 'brand': '公网牌', 'remark': '公网备注'}]})
check('B5 公网提交报价成功', st == 200 and '"success"' in body, body[:150])
row2 = q("SELECT quote_price, quote_remark, quote_details FROM inquiry_suppliers WHERE inquiry_id=?", (iid2,))[0]
check('B6 公网报价入库 quote_price=666', abs(row2['quote_price'] - 666.0) < 0.001, row2['quote_price'])
check('B7 公网厂家备注入库', row2['quote_remark'] == '含税含运价，款到发货', row2['quote_remark'])
qd2 = json.loads(row2['quote_details'])
check('B8 公网明细含含税单价/交付/质保/品牌/备注', qd2 and qd2[0]['unit_price'] == 222.0 and qd2[0]['delivery'] == '5天' and qd2[0]['warranty'] == '6个月' and qd2[0]['brand'] == '公网牌' and qd2[0]['remark'] == '公网备注')

# 公网无效token
st, html = get(PUB + '/inq/' + '0' * 40)
check('B9 公网无效token友好提示', st == 200 and '报价链接无效或已失效' in html, html[:80])

# 公网已截止
ex("UPDATE inquiries SET deadline='2026-01-01' WHERE id=?", (iid2,))
st, html = get(PUB + '/inq/' + tokB)
check('B10 公网已截止友好提示', st == 200 and '报价已截止' in html, html[:80])
ex("UPDATE inquiries SET deadline='2026-09-09' WHERE id=?", (iid2,))

# ================= 清理链 =================
for rid in (prid, prid2):
    ex("DELETE FROM inquiry_suppliers WHERE inquiry_id IN (SELECT id FROM inquiries WHERE req_id=?)", (rid,))
    ex("DELETE FROM inquiries WHERE req_id=?", (rid,))
    ex("DELETE FROM request_items WHERE req_id=?", (rid,))
    ex("DELETE FROM purchase_requests WHERE id=?", (rid,))
ex("DELETE FROM logs WHERE detail LIKE '%【测试】%' OR detail LIKE '%SC-TEST-WL%'")
for t in ('inquiries', 'inquiry_suppliers', 'request_items', 'purchase_requests'):
    ex(f"UPDATE sqlite_sequence SET seq=COALESCE((SELECT MAX(id) FROM {t}),0) WHERE name='{t}'")
left = q("SELECT COUNT(*) c FROM purchase_requests WHERE req_no LIKE 'SC-TEST-WL%'")[0]['c']
left2 = q("SELECT COUNT(*) c FROM inquiries WHERE inq_no LIKE 'XJ%' AND req_id IN (SELECT id FROM purchase_requests WHERE req_no LIKE 'SC-TEST-WL%')")
left3 = q("SELECT COUNT(*) c FROM purchase_requests WHERE purpose LIKE '%【测试】%'")[0]['c']
check('清理后测试申请零残留', left == 0 and left3 == 0, f'left={left} left3={left3}')

print('\n==== 汇总 ====')
fails = [p for p in passed if not p[1]]
print(f'通过 {len(passed)-len(fails)}/{len(passed)}')
sys.exit(1 if fails else 0)
