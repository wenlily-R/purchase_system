-- V11.225 发票回收节点2.0: 合同发票「顺序节点」计划
-- 需求: 不同合同发票开具节点不一样(预付30%/按付款进度/验收后全额等), 每份合同内明确发票回收时间与顺序节点
-- 说明: contract_inv_nodes 新表 + contract_invoices.node_id 补列由 app.py init_db 幂等执行
--       (老库启动/重启自动补充, 与模块一/三/六同机制), 本文件为双机迁移记录; 下方 SQL 幂等可安全重复执行
CREATE TABLE IF NOT EXISTS contract_inv_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 1,
    trigger_desc TEXT DEFAULT '',
    amount REAL DEFAULT 0,
    plan_date TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inv_nodes ON contract_inv_nodes(contract_id);
