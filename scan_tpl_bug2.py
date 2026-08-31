# -*- coding: utf-8 -*-
"""逐字符扫描: 单引号字符串内出现 ${ 的地方(真正的插值bug)"""
src = open('templates/index.html', encoding='utf-8').read()
lines = src.split('\n')
hits = []
for i, ln in enumerate(lines, 1):
    state = None  # None/'sq'/'dq'/'bt'
    j = 0
    n = len(ln)
    while j < n:
        c = ln[j]
        if state is None:
            if c == "'": state = 'sq'
            elif c == '"': state = 'dq'
            elif c == '`': state = 'bt'
            elif c == '/' and j+1 < n and ln[j+1] == '/':
                break  # 行注释
        elif state == 'sq':
            if c == '\\': j += 1
            elif c == "'": state = None
            elif c == '$' and j+1 < n and ln[j+1] == '{':
                hits.append((i, ln.strip()[:160])); break
        elif state == 'dq':
            if c == '\\': j += 1
            elif c == '"': state = None
        elif state == 'bt':
            if c == '\\': j += 1
            elif c == '`': state = None
            elif c == '$' and j+1 < n and ln[j+1] == '{':
                # 跳过模板插值表达式(简单处理: 跳到匹配的 })
                depth = 1; j += 2
                while j < n and depth:
                    if ln[j] == '{': depth += 1
                    elif ln[j] == '}': depth -= 1
                    j += 1
                continue
        j += 1
print('单引号串内 ${ 命中:', len(hits))
for i, t in hits:
    print(f'  L{i}: {t}')
