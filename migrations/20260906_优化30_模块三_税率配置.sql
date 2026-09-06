-- V11.224 采购优化3.0 模块三: 税率参数配置(参考金蝶采购财务系统)
-- 说明: 表结构与单据补列已在 app.py init_db 幂等处理(老库启动自动补), 本文件仅保证双机迁移记录完整
-- 优先级从高到低: 供应商专票税率 → 物料品类税率 → 单据类型默认税率 → 系统兜底默认税率
CREATE TABLE IF NOT EXISTS tax_rate_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'system',   -- supplier=按供应商 / category=按物料品类 / doc_type=按单据类型 / system=系统兜底
    ref_key TEXT DEFAULT '',                -- 供应商名 / 品类名 / 单据类型(purchase_order等) / 空(系统兜底)
    tax_rate REAL DEFAULT 13,               -- 税率(%)
    taxpayer_type TEXT DEFAULT '一般纳税人',  -- 一般纳税人/小规模纳税人
    remark TEXT DEFAULT '',
    is_active INTEGER DEFAULT 1,
    created_by TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tax_cfg_scope ON tax_rate_config(scope, ref_key);
