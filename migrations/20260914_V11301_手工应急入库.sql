-- V11.301 库房手工应急入库（货先到、单后到）
-- 背景: 现场存在「加急件上午报下午到」「五金耗材月结合同后置」「中间商送货单滞后2-3天」「大件设备现场直装后补单」，
--       货已到库房但系统无采购订单，无法入库 → 影响生产领料。整改: 库房可无订单先入库, 后期必须补关联采购订单/合同。
-- 字段: 是否手工应急 / 供应商 / 事由 / 关联补全状态(待补关联·已补关联) / 超限领导确认人+时间
ALTER TABLE receivings ADD COLUMN is_manual INTEGER DEFAULT 0;
ALTER TABLE receivings ADD COLUMN manual_supplier TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN manual_reason TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN link_status TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN manual_ok_by TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN manual_ok_at TEXT DEFAULT '';
-- 金额上限(超过需领导确认): 默认 2000 元, 可在系统设置/库里改
INSERT INTO sys_config(key, value)
SELECT 'manual_recv_limit', '2000'
WHERE NOT EXISTS (SELECT 1 FROM sys_config WHERE key='manual_recv_limit');
