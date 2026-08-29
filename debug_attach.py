#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""排查: 询价审批附件为什么钉钉上看不到"""
import os, sys, json
os.chdir('/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system')
sys.path.insert(0, '.')
import app as appmod

with appmod.app.test_request_context():
    # 1. 看表单
    form = appmod.dt_build_form('inquiry_approval', 4, ('XJ-202608-0004', '测试', 0))
    print('=== 表单字段 ===')
    for f in form:
        print(f"  {f['name']!r}: {str(f['value'])[:100]!r}")

    # 2. 手动解析附件
    print()
    print('=== 附件字段处理 ===')
    n = appmod.dt_resolve_attachments(form, 'inquiry_approval', 4)
    print('上传成功数:', n)
    for f in form:
        if f['name'] == '附件':
            print('附件字段最终值:', str(f['value'])[:200])
    if not any(f['name'] == '附件' for f in form):
        print('❌ 附件字段被移除了(上传全部失败)')
