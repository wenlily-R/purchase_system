-- V11.300 维修(加工)申请单: 新增"使用地点"字段(仅维修单使用; 物资采购申请不使用)
ALTER TABLE purchase_requests ADD COLUMN repair_location TEXT DEFAULT '';
