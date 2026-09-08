-- 20260908_V11245_采购申请明细用途列: 页面/导出/钉钉明细"用途"独立成列(此前用途/备注共用 remark)
ALTER TABLE request_items ADD COLUMN usage TEXT;
