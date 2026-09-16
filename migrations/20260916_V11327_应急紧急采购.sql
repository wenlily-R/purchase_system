-- V11.327 应急紧急采购专项通道: 独立单据表 + 全流程留痕表 + 临时入库标记
-- 说明: 主结构由 app.py init_db 幂等创建(并灌入阈值参数 emg_limit_a/emg_limit_b/emg_month_freq/emg_price_dev/emg_promise_days
--       与四条独立审批链 emergency_temp/emergency_formal/emergency_finance/emergency_extend 的金额分级配置),
--       本文件保证另一台机 pull 后 migrate_db.py 也能补齐结构。
CREATE TABLE IF NOT EXISTS emergency_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emg_no TEXT UNIQUE,
    project TEXT DEFAULT '',
    dept TEXT DEFAULT '',
    requester TEXT DEFAULT '',
    requester_id INTEGER DEFAULT 0,
    item_name TEXT DEFAULT '', spec TEXT DEFAULT '', unit TEXT DEFAULT '个', quantity REAL DEFAULT 0,
    est_amount REAL DEFAULT 0, actual_amount REAL DEFAULT 0,
    need_arrive TEXT DEFAULT '', reason TEXT DEFAULT '',
    attachments TEXT DEFAULT '',
    status TEXT DEFAULT '待临时审批',
    label TEXT DEFAULT '应急采购 - 待转正',
    temp_approved_by TEXT DEFAULT '', temp_approved_at TEXT DEFAULT '',
    supplier TEXT DEFAULT '', quote_amt REAL DEFAULT 0, quote_files TEXT DEFAULT '',
    no_compare_reason TEXT DEFAULT '', supplier_confirmed_at TEXT DEFAULT '',
    temp_receive_id INTEGER DEFAULT 0, temp_receive_no TEXT DEFAULT '',
    temp_received_at TEXT DEFAULT '', deadline TEXT DEFAULT '',
    extend_count INTEGER DEFAULT 0, extend_reason TEXT DEFAULT '', extend_status TEXT DEFAULT '',
    formal_docs TEXT DEFAULT '', formal_submitted_at TEXT DEFAULT '', formal_approved_at TEXT DEFAULT '',
    finance_status TEXT DEFAULT '', finance_remark TEXT DEFAULT '',
    price_ref REAL DEFAULT 0, price_dev_pct REAL DEFAULT 0, price_note TEXT DEFAULT '',
    reject_count INTEGER DEFAULT 0, abnormal INTEGER DEFAULT 0,
    linked_order_no TEXT DEFAULT '', linked_contract_no TEXT DEFAULT '',
    converted_at TEXT DEFAULT '', locked_at TEXT DEFAULT '', lock_reason TEXT DEFAULT '',
    void_reason TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_emg_status ON emergency_purchases(status);
CREATE INDEX IF NOT EXISTS idx_emg_project ON emergency_purchases(project);

CREATE TABLE IF NOT EXISTS emergency_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emg_id INTEGER NOT NULL,
    action TEXT DEFAULT '', operator TEXT DEFAULT '', detail TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_emg_logs ON emergency_logs(emg_id);

-- 临时入库单标记(转正后 is_emg=0 / is_emg_converted=1)
ALTER TABLE receivings ADD COLUMN is_emg INTEGER DEFAULT 0;
ALTER TABLE receivings ADD COLUMN is_emg_converted INTEGER DEFAULT 0;
ALTER TABLE receivings ADD COLUMN emg_no TEXT DEFAULT '';
