# -*- coding: utf-8 -*-
"""2026-08-14 用户要求: 清空当前版本全部采购业务数据, 功能(含三方询价)不变
保留: users/suppliers/departments/sys_config/approval_flow_config/contract_templates/categories/items/budget_accounts/_schema_version
清空: 所有业务单据表 + 库存 + 流水 + 审批 + 推送日志 + 操作日志 + 询价
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'purchase.db')
conn = sqlite3.connect(DB, timeout=30)

# 业务数据表(清空)。顺序: 明细/关联表先删 → 主表
TABLES = [
    # 询价(V11.0)
    'inquiry_suppliers', 'inquiries',
    # 明细/关联
    'request_items', 'order_items', 'requisition_items',
    'approval_instances', 'dingtalk_instances', 'feishu_instances',
    'notifications', 'dingtalk_push_log', 'inventory_flows',
    'inventory_count_items', 'alert_items',
    # 主表
    'purchase_requests', 'purchase_orders', 'contracts', 'receivings',
    'requisitions', 'credit_notes', 'payment_requests', 'price_comparisons',
    'inventory_counts', 'deliveries', 'invoices', 'expenses', 'settlements',
    'reminder_log',
    # 操作日志(干净版本)
    'logs',
]

for t in TABLES:
    # 表可能不存在(版本差异)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()
    if exists:
        conn.execute('DELETE FROM "%s"' % t)
        print('  清空 %s' % t)

# 重置自增序列(防单号跳号)
for t in TABLES:
    try:
        conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
    except Exception:
        pass
conn.commit()

# 验证
print('\n=== 清理后剩余数据 ===')
for t in ['purchase_requests','purchase_orders','contracts','receivings','requisitions',
          'inventory','inventory_flows','approval_instances','inquiries','inquiry_suppliers','logs']:
    n = conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    print('  %-22s %d' % (t, n))
print('\n=== 保留的基础配置 ===')
for t in ['users','suppliers','departments','approval_flow_config','contract_templates','sys_config','categories','items']:
    n = conn.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
    print('  %-22s %d' % (t, n))
conn.close()
print('\n完成: 业务数据已清空, 功能配置保留')
