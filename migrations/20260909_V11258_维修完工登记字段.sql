-- V11.258: 维修完工登记字段(需求: 小额直接委托/维修订单 完工验收闭环, 不进库存)
-- 补充: 维修类采购申请记录 实际完工日期/维修结果
ALTER TABLE purchase_requests ADD COLUMN repair_done_date TEXT DEFAULT '';
ALTER TABLE purchase_requests ADD COLUMN repair_result TEXT DEFAULT '';
