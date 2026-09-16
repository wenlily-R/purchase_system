# -*- coding: utf-8 -*-
"""V11.333 验证：应急采购「接单/询价/定标/发货」角色权限放宽
方法: test_client 伪造 session，打真实接口路由，看返回码
 - 权限通过 → 单据不存在返回 404（说明已越过角色校验）
 - 权限被拒 → 403
"""
import os, sys, json
os.chdir(r'C:\Users\35322\Desktop\purchase_system')
sys.path.insert(0, os.getcwd())
import app as A

CASES = ['系统管理员', '分管领导', '库管员', '部门负责人', '采购员', '员工', '财务']
USE = ('inquiry', 'award', 'ship')

def probe(role):
    cl = A.app.test_client()
    with cl.session_transaction() as s:
        s['user_id'] = 1; s['user_name'] = '权限测试_' + role; s['user_role'] = role
    codes = {}
    for ep in USE:
        r = cl.post('/api/emergency/999999/' + ep, json={})
        codes[ep] = r.status_code
    r2 = cl.post('/api/emergency/999999/supplier', json={})
    codes['supplier(接单)'] = r2.status_code
    meta = cl.get('/api/emergency/meta').get_json() or {}
    codes['meta.can_buy'] = (meta.get('roles') or {}).get('can_buy')
    codes['meta.can_recv'] = (meta.get('roles') or {}).get('can_recv')
    return codes

print('role            | inquiry award ship supplier(接单) | meta.can_buy | meta.can_recv')
print('-' * 100)
for role in CASES:
    c = probe(role)
    print('%-15s | %-7s %-5s %-4s %-15s | %-12s | %s' % (
        role, c['inquiry'], c['award'], c['ship'], c['supplier(接单)'], c['meta.can_buy'], c['meta.can_recv']))

print()
print('判定标准: 404 = 权限通过(单据不存在); 403 = 无权限')
