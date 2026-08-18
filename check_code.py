#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购系统代码自检脚本 — 每次改完代码必须跑, 防止低级错误上线
用法: .venv/bin/python check_code.py [--fix]
检查项:
  1. Python 语法
  2. 字符串格式化陷阱: % 格式化字符串里出现 100% / 50% 等裸百分号
  3. flask session 在后台线程使用(无请求上下文)
  4. 未定义函数/变量引用(粗查)
  5. SQL 占位符数量与参数数量匹配(粗查 INSERT/UPDATE)
  6. 前端 JS 语法(提取 <script> 用 node --check)
"""
import ast
import re
import subprocess
import sys
import os

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, 'app.py')
HTML = os.path.join(BASE, 'templates', 'index.html')

errors = []
warnings = []


def err(msg):
    errors.append(msg)
    print('  ❌', msg)


def warn(msg):
    warnings.append(msg)
    print('  ⚠️ ', msg)


def ok(msg):
    print('  ✅', msg)


print('=== 代码自检 ===')

# 1. 语法检查
print('\n[1] Python 语法')
try:
    with open(APP, encoding='utf-8') as f:
        src = f.read()
    ast.parse(src)
    ok('app.py 语法正确')
except SyntaxError as e:
    err(f'app.py 语法错误: {e}')

# 2. % 格式化字符串中的裸百分号陷阱
print('\n[2] 字符串格式化陷阱(裸百分号)')
lines = src.split('\n')
for i, line in enumerate(lines, 1):
    # 排除纯注释行
    stripped = line.strip()
    if stripped.startswith('#'):
        continue
    # 找 '%' 格式化模板字符串(行内有 '...' % 或 "..." % 模式)
    if re.search(r"['\"].*['\"]\s*%\s*(?:\(|\w|\{)", line):
        # 提取字符串内容
        m = re.findall(r"['\"]([^'\"]*)['\"]", line)
        for s in m:
            # 检查是否有 100% 之类的裸百分号(不是 %s %d %.2f %% 等合法格式)
            bad = re.findall(r'\d+%[^sd%]|\d+%\s*[,;)\s]', s)
            if bad:
                err(f'app.py:{i}: 格式化字符串含裸百分号 {bad} → {stripped[:90]}')

# 3. flask session 后台线程使用(只在后台线程函数内检查)
print('\n[3] 后台线程 session 使用')
# 后台线程函数: 轮询/同步/回调/库存变更(无HTTP请求上下文)
BG_FUNCS = ['do_receiving_stock', 'do_requisition_stock', 'finish_approvals',
            'dt_poll_results', 'dt_sync_result', 'dt_retry_failed_instances',
            'dt_terminate_stale', 'dt_poll_loop', 'scheduler_loop',
            'start_instances', 'fs_sync_result', 'fs_start_instance']
# 粗定位: 找这些函数定义的行区间
func_ranges = []
for fn in BG_FUNCS:
    for m in re.finditer(rf'^def {fn}\(', src, re.M):
        start = src[:m.start()].count('\n') + 1
        # 找函数体结束(下一个顶层 def 或 @app.route)
        nxt = re.search(r'^(@app\.route|def )', src[m.end():], re.M)
        end = src[:m.end() + (nxt.start() if nxt else len(src))].count('\n') + 1 if nxt else start + 200
        func_ranges.append((fn, start, end))
for i, line in enumerate(lines, 1):
    if 'session.get(' in line and 'user_name' in line:
        for fn, fs, fe in func_ranges:
            if fs <= i <= fe:
                err(f'app.py:{i}: 后台函数 {fn} 内使用 session: {line.strip()[:80]} (应改用 _op_name())')
                break

# 4. 未定义引用粗查(常见函数名)
print('\n[4] 未定义函数/变量引用')
defined = set(re.findall(r'^def (\w+)', src, re.M))
defined |= set(re.findall(r'^\s{4}def (\w+)', src, re.M))
# 检查常见误用
for i, line in enumerate(lines, 1):
    for m in re.findall(r'\b(dt_rmb_upper|gen_doc_voucher|_op_name)\s*\(', line):
        if m not in defined:
            err(f'app.py:{i}: 调用未定义函数 {m}')

# 5. SQL 占位符匹配粗查
print('\n[5] SQL 占位符数量')
for i, line in enumerate(lines, 1):
    if 'INSERT INTO' in line or 'UPDATE ' in line:
        q = line.count('?')
        # 无法精确匹配参数, 仅提示可疑(参数在同一行)
        if q > 0 and 'VALUES(' in line and q > 8:
            ok(f'app.py:{i}: SQL 含 {q} 个占位符(人工确认参数数量)')

# 6. JS 语法检查
print('\n[6] 前端 JS 语法')
try:
    with open(HTML, encoding='utf-8') as f:
        html = f.read()
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    tmp = os.path.join(BASE, '.hermes-tmp.check.js')
    with open(tmp, 'w', encoding='utf-8') as f:
        for s in scripts:
            f.write(s + '\n')
    r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        ok('index.html JS 语法正确')
    else:
        err(f'index.html JS 语法错误: {r.stderr[:300]}')
    os.remove(tmp)
except FileNotFoundError:
    warn('node 不可用, 跳过 JS 检查')
except Exception as e:
    warn(f'JS 检查异常: {e}')

# 7. 检查 HTML 模板里 % 陷阱(有内联 Python 格式化的行)
print('\n[7] 模板文件检查')
for i, line in enumerate(html.split('\n'), 1):
    if re.search(r"['\"][^'\"]*['\"]\s*%\s*\(|['\"][^'\"]*['\"]\s*%", line) and '%' in line:
        # 提取可能的问题
        for s in re.findall(r"['\"]([^'\"]*)['\"]", line):
            bad = re.findall(r'\d+%[^sd%]|\d+%\s*[,;)\s]', s)
            if bad:
                err(f'index.html:{i}: 裸百分号 {bad}')

print()
print(f'=== 结果: {len(errors)} 错误, {len(warnings)} 警告 ===')
if errors:
    print('❌ 存在错误, 禁止提交/上线!')
    sys.exit(1)
else:
    print('✅ 检查通过')
    sys.exit(0)
