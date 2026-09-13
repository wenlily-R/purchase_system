#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把用户提供的《修理修缮合同》docx 转为系统占位符模板 → contract_templates/修理修缮合同.docx

原则: 只在空白/下划线处插占位符、清掉模板里的示例数字; 其余原文、字体、对齐、下划线、表格结构一律不动。
占位符:
  合同头部        {乙方名称}  {维修标的物}
  修理修缮时间    {维修开始年}{维修开始月}{维修开始日}{维修结束年}{维修结束月}{维修结束日}
  第七条收款账户  {收款账号名称} {收款账号} {收款银行} {收款行号}
金额/税金/不含税/大写/明细表格由 app.py 的 api_contract_generate 渲染时自动填入(无需占位符)。

用法: python gen_tpl_repair.py [源docx路径]   (默认 桌面/修理修缮合同.docx)
"""
import os
import sys

import docx

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.expanduser('~'), 'Desktop', '修理修缮合同.docx')
OUT = os.path.join(BASE, 'contract_templates', '修理修缮合同.docx')

d = docx.Document(SRC)
log = []

# 1) 合同头部: 乙方名称 + 委托修理标的物名称
for i, p in enumerate(d.paragraphs):
    t = p.text
    if t.strip() == '乙方：' and p.runs:
        p.runs[-1].text = p.runs[-1].text + '{乙方名称}'
        log.append('P%d 乙方名称占位' % i)
    if '就甲方委托乙方修理' in t:
        ok = False
        for r in p.runs:
            if r.text.strip() == '' and r.text != '' and r.underline:
                r.text = '{维修标的物}'
                ok = True
                break
        log.append('P%d 维修标的物占位=%s' % (i, ok))

# 2) 修理修缮时间: 下划线空白分6组 → 年/月/日 ×2
for i, p in enumerate(d.paragraphs):
    t = p.text
    if ('年' in t and '月' in t and '日至' in t and len(t) < 60):
        groups, cur = [], []
        for ri, r in enumerate(p.runs):
            if r.text.strip() == '':
                cur.append(ri)
            elif cur:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        keys = ['{维修开始年}', '{维修开始月}', '{维修开始日}',
                '{维修结束年}', '{维修结束月}', '{维修结束日}']
        if len(groups) == 6:
            for gi, grp in enumerate(groups):
                p.runs[grp[0]].text = keys[gi]
                for rx in grp[1:]:
                    p.runs[rx].text = ''
            log.append('P%d 修理修缮时间 6 占位 OK' % i)
        else:
            log.append('P%d !! 修理修缮时间空白组数=%d (未处理)' % (i, len(groups)))

# 3) 第七条 收款账户信息 4 项
for i, p in enumerate(d.paragraphs):
    for r in p.runs:
        for lab, ph in (('收款账户名称：', '{收款账号名称}'), ('收款账号：', '{收款账号}'),
                        ('收款银行：', '{收款银行}'), ('银行行号：', '{收款行号}')):
            if r.text.strip().endswith(lab) and ph not in r.text:
                r.text = r.text + ph
                log.append('P%d %s → %s' % (i, lab, ph))

# 4) 维修明细单: 清掉模板里的示例合计数字(渲染时会按订单明细重算)
for ti, tb in enumerate(d.tables):
    for row in tb.rows:
        for c in row.cells:
            _t = c.text.strip().replace(',', '')
            if _t and _t.replace('.', '', 1).isdigit():
                for pp in c.paragraphs:
                    for rr in pp.runs:
                        rr.text = ''
                log.append('T%d 清除示例数字 %r' % (ti, _t))

d.save(OUT)
print('模板已生成:', OUT)
print('大小:', os.path.getsize(OUT), 'bytes')
for line in log:
    print(' -', line)
