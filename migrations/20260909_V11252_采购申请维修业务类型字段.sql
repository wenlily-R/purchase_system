-- V11.252 采购申请支持设备维修业务类型(维修并入采购申请全流程)
-- 2026-09-09 需求文档《20260909修改系统.docx》: 取消独立设备维修模块, 维修全部走采购申请
-- 新增维修类字段: 故障设备/故障描述(提报) / 定损项目清单(厂家拆解后) / 直接委托维修商(小额透明) / 委托方式 / 不可修转物资标记
ALTER TABLE purchase_requests ADD COLUMN repair_device TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_fault TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_damage_json TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_vendor TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_amount REAL DEFAULT 0;
ALTER TABLE purchase_requests ADD COLUMN repair_entrust_type TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_converted INTEGER DEFAULT 0;
