#!/usr/bin/env python3
"""网址自检脚本 — 每次交付链接前运行:
1. 读当前公网地址
2. 验证首页 200
3. 验证最新询价的三家供应商链接
4. 输出交付文本(可直接复制给用户)
"""
import sqlite3, os, subprocess, sys, urllib.request

BASE = '/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system'
PUB = os.path.join(BASE, 'data/public_url.txt')
DB = os.path.join(BASE, 'data/purchase.db')

def check(url, timeout=15):
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except Exception as e:
        return f'FAIL({type(e).__name__})'

def main():
    if not os.path.exists(PUB):
        print('❌ 无地址文件'); sys.exit(1)
    base = open(PUB).read().strip()
    print('=== 网址自检 ===')
    print(f'当前地址: {base}')
    home = check(base + '/')
    print(f'首页: {home}')
    ok = (home == 200)
    # 三方链接
    try:
        conn = sqlite3.connect(DB, timeout=30)
        rows = conn.execute(
            "SELECT i.inq_no, s.supplier_name, s.token FROM inquiries i "
            "JOIN inquiry_suppliers s ON s.inquiry_id=i.id "
            "ORDER BY i.id DESC LIMIT 3").fetchall()
        conn.close()
        print('\n最新询价商家链接:')
        for inq, sup, tok in rows:
            st = check(f'{base}/inq/{tok}')
            print(f'  {inq} | {sup}: {st}')
            if st != 200:
                ok = False
    except Exception as e:
        print(f'三方链接检查失败: {e}')
    print('\n' + ('✅ 全部可用, 可以交付' if ok else '⚠️ 有异常, 需重连隧道'))
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
