#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成正式买卖合同模板 tpl_maimai.docx（话术逐字对齐已签合同照片 HQZC-CLCG-067-2026）
甲方固定: 河曲县正成洗选煤有限责任公司
乙方: 占位符 {乙方名称} 等, 生成时按订单供应商自动填入
明细: 表格自动填充(order_items 逐行)
"""
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import os

BASE = '/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system'
OUT = os.path.join(BASE, 'uploads', 'tpl_maimai.docx')

doc = Document()

# 默认字体: 仿宋, 小四
style = doc.styles['Normal']
style.font.name = '仿宋'
style.font.size = Pt(12)
style._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋')

def para(text='', bold=False, align=None, size=12, space_after=6, font='仿宋'):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.4
    if align is not None:
        p.alignment = align
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = font
    r._element.rPr.rFonts.set(qn('w:eastAsia'), font)
    return p

def heading(text):
    return para(text, bold=True, size=12, space_after=4)

def set_cell_border(cell):
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right'):
        el = OxmlElement(f'w:{edge}')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '6')
        el.set(qn('w:color'), '000000')
        borders.append(el)
    tcPr.append(borders)

def set_cell_text(cell, text, bold=False, size=10.5, center=True):
    cell.text = ''
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(text)
    r.bold = bold
    r.font.size = Pt(size)
    r.font.name = '仿宋'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '仿宋')

# ============ 标题 + 合同编号（同一行: 标题居中, 编号右侧） ============
head = doc.add_table(rows=1, cols=2)
head.alignment = WD_TABLE_ALIGNMENT.CENTER
set_cell_text(head.rows[0].cells[0], '买卖合同', bold=True, size=18, center=True)
set_cell_text(head.rows[0].cells[1], '合同编号：{合同编号}', bold=False, size=10.5, center=False)
head.rows[0].cells[0].width = Cm(10)
head.rows[0].cells[1].width = Cm(7)
doc.add_paragraph()

# ============ 双方信息（照片格式: 甲方：公司名 / 乙方：公司名, 无买方卖方标注） ============
para('甲方：河曲县正成洗选煤有限责任公司', size=12)
para('乙方：{乙方名称}', size=12)
doc.add_paragraph()

# 前言（照片原文）
para('甲乙双方秉承自愿、公平、诚实守信的原则，根据《中华人民共和国民法典》及相关法律法规的规定，经平等协商签订本合同明确双方权利义务，以资共同信守。', size=12)
doc.add_paragraph()

# ============ 一、标的、规格、数量、价款 ============
heading('一、标的、规格、数量、价款')
table = doc.add_table(rows=3, cols=7)
table.style = 'Table Grid'
table.alignment = WD_TABLE_ALIGNMENT.CENTER
headers = ['标的物', '规格型号', '计量单位', '数量', '单价', '金额', '备注']
for j, h in enumerate(headers):
    set_cell_text(table.rows[0].cells[j], h, bold=True)
for j in range(7):
    set_cell_text(table.rows[1].cells[j], '')
set_cell_text(table.rows[2].cells[0], '合计', bold=True)
for j in range(1, 5):
    set_cell_text(table.rows[2].cells[j], '')
set_cell_text(table.rows[2].cells[5], '', bold=True)
set_cell_text(table.rows[2].cells[6], '')
widths = [Cm(3.2), Cm(2.8), Cm(1.8), Cm(1.4), Cm(2.2), Cm(2.6), Cm(2.0)]
for row in table.rows:
    for j, w in enumerate(widths):
        row.cells[j].width = w
for row in table.rows:
    for cell in row.cells:
        set_cell_border(cell)
doc.add_paragraph()

# 合计金额说明（_apply_ct 自动填税金/不含税/大写; 照片格式: 一行连续文本自动换行）
para('合计金额：¥    元（大写金额：人民币     ）。税金（税率 13%）为：¥    元（大写金额：人民币     ）；不含税价款为：¥    元（大写金额：人民币     ）。', size=12)
doc.add_paragraph()

# ============ 二、质量标准 ============
heading('二、质量标准')
para('标的物质量标准：国标。标的物应完全符合国家、行业及企业的现行规范标准或执行项目技术协议。')
doc.add_paragraph()

# ============ 三、包装 ============
heading('三、包装')
para('乙方应采用国家或行业标准措施进行包装，使包装完全适应于远距离运输，保证货物安全。')
doc.add_paragraph()

# ============ 四、交付 ============
heading('四、交付')
para('1、乙方应于合同签订后  日内交付至甲方指定地点。')
para('2、乙方应同时向甲方提供标的物的产品质量检验合格证书、出厂检验报告、产品使用说明书、技术资料及甲方要求提供的其他证明。')
para('3、本合同价格已包含设计、制造、运输、卸货搬运、安装、调试、售后服务等所有费用，若甲方对数量进行调整，单价按本合同单价执行，并按甲方确认的实际数量进行结算。')
doc.add_paragraph()

# ============ 五、运输 ============
heading('五、运输')
para('1、乙方负责运输至甲方指定地点。')
para('2、运输方式：汽运。')
para('3、标的物交付前的一切费用及风险由乙方负责。')
doc.add_paragraph()

# ============ 六、验收 ============
heading('六、验收')
para('标的物运抵甲方指定地点后  日内，甲方应对货物的外观质量、数量、规格型号进行验收，验收合格后由甲方出具签收单。如验收不合格，乙方应在  日内更换、补发。')
doc.add_paragraph()

# ============ 七、质量保证 ============
heading('七、质量保证')
para('所有产品质保期为  年，自甲方验收合格之日起开始计算，质保期内乙方应负责免费更换。')
doc.add_paragraph()

# ============ 八、结算 ============
heading('八、结算')
para('1、签订合同后  日内，乙方向甲方提供全额13%增值税专用发票。')
para('2、甲方自收到发票后  日内支付合同总价的  %，乙方安排发货。')
para('3、收款账户信息')
para('收款账户名称：')
para('收款账号：')
para('收款银行：')
para('银行行号：')
doc.add_paragraph()

# ============ 九、违约责任 ============
heading('九、违约责任')
para('1、乙方迟延交货应承担合同额的  %的违约金。')
para('2、标的物质量、规格、数量等不符合约定要求，乙方除应按约定期限及时更换、补发，同时应承担上述迟延交货的违约责任。')
para('3、因乙方违约造成甲方损失的，违约金不足以弥补甲方全部损失时，乙方应承担继续赔偿的责任。')
para('4、乙方在货物装卸、运输、安装、调试等过程中发生的一切安全事故及第三人的人身或财产损失的，均由其自行负责，与甲方无关。')
para('5、甲方因维护自身权利支出的包括但不限于诉讼费、律师费、评估费、鉴定费、保全费等由乙方承担。')
doc.add_paragraph()

# ============ 十、合同解除 ============
heading('十、合同解除')
para('1、乙方延迟交付货物超过  天，甲方有权单方面解除合同。')
para('2、乙方在质量保证期内，拒不履行"三包"义务，甲方有权单方面解除合同。')
para('3、乙方更换标的物后仍有瑕疵，甲方有权单方面解除合同。')
para('4、其他致使合同目的不能实现的情况，任意一方可单方面解除合同，但需提前  天通知对方。')
doc.add_paragraph()

# ============ 十一、知识产权 ============
heading('十一、知识产权')
para('乙方提供的货物须保证不侵犯任何第三方的知识产权，如因乙方提供的货物存在侵权行为导致甲方承担赔偿责任的，乙方应全额赔偿并承担由此导致的甲方全部损失。')
doc.add_paragraph()

# ============ 十二、争议解决方式 ============
heading('十二、争议解决方式')
para('本合同在履行过程中发生争议的，由双方当事人协商解决；协商不成的，双方均可向甲方所在地人民法院提起诉讼。')
doc.add_paragraph()

# ============ 十三、其他 ============
heading('十三、其他')
para('1、本合同自双方法定代表人或委托代理人签字并加盖单位印章之日起生效，若非真实签字、签章，本合同无效。（若此合同为法定代表人授权的委托代理人，必须将授权书附后，方可签订合同）')
para('2、本合同未尽事宜，可以另行签订补充协议，补充协议与本合同具有同等法律效力。')
para('3、本合同一式肆份，甲方执叁份，乙方执壹份，各份具有同等法律效力。')
doc.add_paragraph()

# ============ 落款（照片: 甲方(签字并盖章)下方直接日期, 无"日期:"字样） ============
sign = doc.add_table(rows=3, cols=2)
for row in sign.rows:
    for cell in row.cells:
        cell.text = ''
set_cell_text(sign.rows[0].cells[0], '甲方（签字并盖章）：', bold=False, center=False)
set_cell_text(sign.rows[0].cells[1], '乙方（签字并盖章）：', bold=False, center=False)
set_cell_text(sign.rows[1].cells[0], '    年    月    日', center=False)
set_cell_text(sign.rows[1].cells[1], '    年    月    日', center=False)
set_cell_text(sign.rows[2].cells[0], '', center=False)
set_cell_text(sign.rows[2].cells[1], '', center=False)
for row in sign.rows:
    for cell in row.cells:
        cell.width = Cm(8)

doc.save(OUT)
print('模板已生成:', OUT)
print('大小:', os.path.getsize(OUT), 'bytes')
