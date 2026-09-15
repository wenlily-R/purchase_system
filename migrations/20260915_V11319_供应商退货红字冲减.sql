-- V11.319 供应商退货(需求文档模块三.2): 当月作废入库单 / 跨月红字入库冲减
--   场景: 质量不合格 / 供应商多发 / 买错 → 退回供应商
--   当月未结账: 直接作废对应入库单, 库存数量回退
--   跨月退货: 生成红字入库单(负数), 冲减库存数量与成本, 同步采购端订单
--   已部分领用: 先冲销对应出库记录(按领用人留痕), 再冲销入库记录
--   审批: biz_type=supplier_return(1级部门负责人), 审批通过后自动执行冲减
CREATE TABLE IF NOT EXISTS supplier_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_no TEXT UNIQUE NOT NULL,
    supplier TEXT DEFAULT '',
    order_id INTEGER DEFAULT 0,
    order_no TEXT DEFAULT '',
    contract_no TEXT DEFAULT '',
    receiving_id INTEGER DEFAULT 0,
    receiving_no TEXT DEFAULT '',
    trace_no TEXT DEFAULT '',
    warehouse TEXT DEFAULT '主库房',
    mode TEXT DEFAULT '跨月红字',
    part_used INTEGER DEFAULT 0,
    used_note TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    total_amount REAL DEFAULT 0,
    status TEXT DEFAULT '草稿',
    requester TEXT DEFAULT '',
    red_no TEXT DEFAULT '',
    flush_note TEXT DEFAULT '',
    reject_count INTEGER DEFAULT 0,
    rejected_reason TEXT DEFAULT '',
    attachments TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS supplier_return_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    spec TEXT DEFAULT '',
    unit TEXT DEFAULT '个',
    qty REAL DEFAULT 0,
    price REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    src_receiving_id INTEGER DEFAULT 0,
    src_receiving_no TEXT DEFAULT '',
    restore_out INTEGER DEFAULT 0,
    restore_note TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label)
    SELECT 'supplier_return',1,'部门负责人',0,1000000,'供应商退货审批-1级'
    WHERE NOT EXISTS (SELECT 1 FROM approval_flow_config WHERE biz_type='supplier_return');
