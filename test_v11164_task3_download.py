# -*- coding: utf-8 -*-
"""V11.164 任务3: 公网下载合同文件完整性验证(隧道传输是否截断/损坏)"""
import os, hashlib, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(BASE, 'uploads')
PUB = open(os.path.join(BASE, 'data/public_url.txt')).read().strip().rstrip('/')

def md5(b):
    return hashlib.md5(b).hexdigest()

for fn in ['contract_HQZC-SBCG-001-2026.docx', 'tpl_maimai.docx', '20260829092550_买卖合同.docx']:
    local = open(os.path.join(UP, fn), 'rb').read()
    url = PUB + '/uploads/' + urllib.parse.quote(fn)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=60) as r:
            remote = r.read()
        ok = (len(remote) == len(local)) and (md5(remote) == md5(local))
        print(f"{'✅' if ok else '❌'} {fn}: 本地{len(local)}B vs 公网下载{len(remote)}B md5={'一致' if md5(remote)==md5(local) else '不一致!'} HTTP={r.status}")
        if not ok:
            print('   → 隧道传输截断/损坏! 需关注')
    except Exception as e:
        print(f'❌ {fn}: 下载失败 {e}')
