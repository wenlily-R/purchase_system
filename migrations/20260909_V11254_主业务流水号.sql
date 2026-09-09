-- V11.254 主业务流水号(全链路溯源): 采购申请发起时生成唯一主业务流水号(公司简码+YYYYMMDD+当日3位流水)
-- 订单/入库单经 req_id/order_id 关系链继承该号; 合同使用公司-类目档案编号(独立体系, 经订单关联)
ALTER TABLE purchase_requests ADD COLUMN biz_no TEXT DEFAULT '';
