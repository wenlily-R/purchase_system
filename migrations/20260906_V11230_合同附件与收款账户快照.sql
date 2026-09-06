-- V11.230 合同模块修复+合同附件 迁移 (2026-09-06)
-- 表结构由 app.py init_db 幂等 DDL 执行(重启自动补齐), 本文件供双机同步/Mac 端核对参考
-- 涉及: ①新表 contract_attachments(合同附件: 发票/扫描件等, 仅系统内留存不进合同正文)
--       ②contracts.bank_info(收款账户快照, 仅系统面板展示, 禁止写入合同docx)
-- 执行方式: python migrate_db.py 或重启 app(init_db 幂等)

CREATE TABLE IF NOT EXISTS contract_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id INTEGER NOT NULL,
    file_name TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_kind TEXT DEFAULT '其他',
    uploaded_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ct_att ON contract_attachments(contract_id);

-- 幂等补列(bank_info)
ALTER TABLE contracts ADD COLUMN bank_info TEXT DEFAULT '';
