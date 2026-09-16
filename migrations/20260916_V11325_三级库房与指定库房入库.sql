-- V11.325 需求模块一/二/三: 三级库房体系(仓库-库区-库位) + 入库指定库房(库区/库位) + 历史导入数据标记
-- 说明: 主结构由 app.py init_db 幂等创建(并自动灌入默认仓库/库区/库位种子数据), 本文件保证另一台机 pull 后 migrate_db.py 也能补齐结构。
CREATE TABLE IF NOT EXISTS warehouse_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    code TEXT DEFAULT '',
    name TEXT NOT NULL,
    wh TEXT DEFAULT '',
    status TEXT DEFAULT '启用',
    color_tag TEXT DEFAULT 'normal',
    sort_no INTEGER DEFAULT 0,
    remark TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_whnode_parent ON warehouse_nodes(parent_id);

ALTER TABLE receivings ADD COLUMN zone TEXT DEFAULT '';
ALTER TABLE inventory ADD COLUMN zone TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN data_source TEXT DEFAULT '系统';
ALTER TABLE inventory ADD COLUMN data_source TEXT DEFAULT '系统';
