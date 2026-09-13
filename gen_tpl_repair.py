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
import re
import sys

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

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

# 5) 字体统一(用户 2026-09-13 要求): 全篇中文(含标题、表格标题行)一律 仿宋;
#    含西文数字/字母的 run 用 Times New Roman(与《买卖合同》模板/公文口径一致)。
#    只改 w:rFonts, 不动字号/加粗/下划线/对齐 —— 原稿里 P0 标题与"维修明细单"标题是宋体(或继承 docDefaults 宋体), 此处一并归到仿宋。
FANGSONG, LATIN = '仿宋', 'Times New Roman'


def _norm_run(run):
    """run 级字体归一: 中文仿宋 + 西文数字 Times New Roman(返回 0/1 表示是否改动)"""
    if not run.text.strip():
        return 0
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn('w:rFonts')) if rpr is not None else None
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.insert(0, rf)
    has_latin = bool(re.search(r'[0-9A-Za-z]', run.text))
    want = (LATIN if has_latin else FANGSONG, FANGSONG)
    cur = (rf.get(qn('w:ascii')), rf.get(qn('w:eastAsia')))
    if cur == want:
        return 0
    rf.set(qn('w:ascii'), want[0])
    rf.set(qn('w:hAnsi'), want[0])
    rf.set(qn('w:eastAsia'), want[1])
    return 1


_n_font = 0
for p in d.paragraphs:
    for r in p.runs:
        _n_font += _norm_run(r)
for tb in d.tables:
    for row in tb.rows:
        for c in row.cells:
            for pp in c.paragraphs:
                for r in pp.runs:
                    _n_font += _norm_run(r)
# 样式兜底: Normal 样式/文档默认字体也归到同一口径(防将来新增 run 继承宋体)
_st = d.styles['Normal']
try:
    _st.font.name = LATIN
    _sp = _st.element.get_or_add_rPr()
    _srf = _sp.find(qn('w:rFonts'))
    if _srf is None:
        _srf = OxmlElement('w:rFonts')
        _sp.insert(0, _srf)
    _srf.set(qn('w:ascii'), LATIN)
    _srf.set(qn('w:hAnsi'), LATIN)
    _srf.set(qn('w:eastAsia'), FANGSONG)
except Exception as _e:
    log.append('Normal 样式字体归一失败: %s' % _e)
# 最后兜底: docDefaults 的 eastAsia 原为宋体(无 rFonts 的 run 最终落到这里) → 一并归到仿宋
try:
    _dd = d.styles.element.find(qn('w:docDefaults'))
    _rp = _dd.find(qn('w:rPrDefault')) if _dd is not None else None
    _rp = _rp.find(qn('w:rPr')) if _rp is not None else None
    _drf = _rp.find(qn('w:rFonts')) if _rp is not None else None
    if _drf is not None:
        _drf.set(qn('w:eastAsia'), FANGSONG)
        log.append('docDefaults eastAsia → %s' % FANGSONG)
except Exception as _e:
    log.append('docDefaults 字体归一失败: %s' % _e)
log.append('字体归一: 改动 %d 个 run (中文→%s / 含数字字母→%s)' % (_n_font, FANGSONG, LATIN))

d.save(OUT)
print('模板已生成:', OUT)
print('大小:', os.path.getsize(OUT), 'bytes')
for line in log:
    print(' -', line)
