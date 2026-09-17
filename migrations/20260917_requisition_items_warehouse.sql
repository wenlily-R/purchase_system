-- V11.335 出库按库房严格扣减(修复实测事故: 应急临时入库的货被常规出库跨库房扣走并叠成负库存)
-- 出库明细行记录库房: 与单据表头/流水/台账同口径; 审批扣减按该库房严格匹配, 未指定库房时走自动FIFO并排除「临时待分配库」
ALTER TABLE requisition_items ADD COLUMN warehouse TEXT DEFAULT '';
