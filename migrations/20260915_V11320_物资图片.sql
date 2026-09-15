-- V11.320 物资图片(需求文档模块六.3): 物资档案图片, 入库/库存查询展示核对
--   图片文件走通用上传 /api/upload(存 uploads/), 本表登记图片与物资(名称+规格)的绑定关系
CREATE TABLE IF NOT EXISTS material_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL,
    spec TEXT DEFAULT '',
    file_path TEXT NOT NULL,
    doc_type TEXT DEFAULT '档案',
    doc_id INTEGER DEFAULT 0,
    doc_no TEXT DEFAULT '',
    uploader TEXT DEFAULT '',
    remark TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_matimg_name ON material_images(item_name, spec);
