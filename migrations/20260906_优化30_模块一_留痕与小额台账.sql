-- V11.224 采购优化3.0 模块一: 全流程可修改/重提 + 版本留痕 + 操作日志
-- 单据修改操作日志(所有编辑/重提/撤回/删除动作留痕, 含修改前后差异)
CREATE TABLE IF NOT EXISTS doc_edit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    biz_type TEXT NOT NULL,
    biz_id INTEGER NOT NULL,
    doc_no TEXT DEFAULT '',
    action TEXT DEFAULT '修改',          -- 修改/重提/提交/撤回/删除/作废/审批通过/驳回
    operator TEXT DEFAULT '',
    operator_id INTEGER DEFAULT 0,
    status_before TEXT DEFAULT '',
    status_after TEXT DEFAULT '',
    changes TEXT DEFAULT '',            -- 人类可读差异描述(逐字段: 旧→新)
    detail TEXT DEFAULT '',             -- JSON: {fields:{旧:新}, old_summary, new_summary}
    node TEXT DEFAULT '',               -- 重提节点说明(如: 重新提交审批-第1级)
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_doc_edit_logs_doc ON doc_edit_logs(biz_type, biz_id);
CREATE INDEX IF NOT EXISTS idx_doc_edit_logs_time ON doc_edit_logs(created_at);

-- 单据修改前原数据快照(已完成单据修改需保留原数据版本, 全链路可追溯)
CREATE TABLE IF NOT EXISTS doc_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    biz_type TEXT NOT NULL,
    biz_id INTEGER NOT NULL,
    doc_no TEXT DEFAULT '',
    snap_json TEXT DEFAULT '',          -- 修改前整单完整数据(主表+明细+审批节点)
    operator TEXT DEFAULT '',
    reason TEXT DEFAULT '',             -- 触发动作: 修改已完成单据等
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_doc_snapshots_doc ON doc_snapshots(biz_type, biz_id);

-- V11.224 模块五: 小额(<1000元)线下补录台账(模式二: 线下操作后3工作日内补录)
CREATE TABLE IF NOT EXISTS small_purchase_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ledger_no TEXT UNIQUE,              -- XE-YYYYMM-xxxx
    kind TEXT DEFAULT '采购',            -- 采购/维修
    dept TEXT DEFAULT '',
    requester TEXT DEFAULT '',
    requester_id INTEGER DEFAULT 0,
    item_name TEXT DEFAULT '',
    content TEXT DEFAULT '',            -- 采购/维修内容描述
    amount REAL DEFAULT 0,              -- 含税金额(<1000)
    happened_date TEXT DEFAULT '',      -- 线下发生日期
    supplier TEXT DEFAULT '',
    payee_name TEXT DEFAULT '',
    pay_method TEXT DEFAULT '',         -- 现金/微信/支付宝/银行转账/其他
    certificates TEXT DEFAULT '[]',     -- 凭证附件(收据/付款记录/验收单/维修明细)
    status TEXT DEFAULT '待审核',        -- 待审核/审核通过/标记异常
    audit_by TEXT DEFAULT '',
    audit_at TEXT DEFAULT '',
    audit_remark TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_small_ledger_time ON small_purchase_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_small_ledger_status ON small_purchase_ledger(status);

-- 单据补列(幂等, 代码init_db亦有相同逻辑, 此文件供双机同步): 各主表加 最后修改人/时间
ALTER TABLE purchase_requests ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE purchase_orders ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE purchase_orders ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE contracts ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE contracts ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE requisitions ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE requisitions ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE repair_plans ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE repair_plans ADD COLUMN last_edit_at TEXT DEFAULT '';
ALTER TABLE return_requests ADD COLUMN last_edit_by TEXT DEFAULT '';
ALTER TABLE return_requests ADD COLUMN last_edit_at TEXT DEFAULT '';
