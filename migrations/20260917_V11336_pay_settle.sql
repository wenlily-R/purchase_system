-- V11.336 付款方式结构化(应急询价付款方式 → 结算方式 → 进月结汇总)
-- 1) 应急询价: 落定结算类型(月结/现结), 与付款方式文本(inq_pay_method, 如"月结30天")一起存
-- 2) 采购订单: 记付款方式文本(账期), 与 settle_type 一起供 月结汇总/月度合同/对账 显示
ALTER TABLE emergency_purchases ADD COLUMN inq_settle_type TEXT DEFAULT '';
ALTER TABLE purchase_orders ADD COLUMN pay_term TEXT DEFAULT '';
