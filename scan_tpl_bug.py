# -*- coding: utf-8 -*-
"""扫描 index.html 中「单引号字符串内使用 ${}」的 bug 模式"""
import re

src = open('templates/index.html', encoding='utf-8').read()
lines = src.split('\n')
hits = []
for i, ln in enumerate(lines, 1):
    if '${' not in ln:
        continue
    pre = ln[:ln.index('${')]
    sq = pre.count("'")
    dq = pre.count('"')
    bt = ln.count('`')
    # 单引号未闭合(奇数) 且 反引号成对(不在模板串内) → 单引号串内插值
    if sq % 2 == 1 and bt % 2 == 0 and dq % 2 == 0:
        hits.append((i, ln.strip()[:180]))
print('命中', len(hits), '处:')
for i, t in hits:
    print(f'  L{i}: {t}')
