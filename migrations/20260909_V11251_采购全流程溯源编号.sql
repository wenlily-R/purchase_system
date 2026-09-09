-- V11.251 采购全流程统一溯源编号: 各业务环节表挂 trace_no(源=采购订单号, 分批入库=订单号-批次序)
-- 溯源根=采购订单号(order_no 本身); 询价(定标后回填)/合同/入库/库存台账/库存流水/出库明细 关联该编号
ALTER TABLE inquiries ADD COLUMN trace_no TEXT DEFAULT '';
ALTER TABLE contracts ADD COLUMN trace_no TEXT DEFAULT '';
ALTER TABLE receivings ADD COLUMN trace_no TEXT DEFAULT '';
ALTER TABLE inventory ADD COLUMN trace_no TEXT DEFAULT '';
ALTER TABLE inventory_flows ADD COLUMN trace_no TEXT DEFAULT '';
ALTER TABLE requisition_items ADD COLUMN trace_no TEXT DEFAULT '';
