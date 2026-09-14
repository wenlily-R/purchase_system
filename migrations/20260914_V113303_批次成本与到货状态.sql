-- V11.303 批次成本与到货状态（需求文档《库存及全业务链路优化》模块一.3 分批入库强化 + 模块四.2 同品多批次独立管理）
-- 背景: 同品名同规格、不同批次采购价不同时, 原实现按加权平均合并成一条库存, 成本被摊平、无法按批次核算/溯源。
-- 整改: 库存条目按 品名+规格+库房+单价 分条(单价相同才合并), 并绑定 批次号/采购订单号/入库单号; 订单新增到货状态。
ALTER TABLE inventory ADD COLUMN batch_no TEXT DEFAULT '';
ALTER TABLE inventory ADD COLUMN order_no TEXT DEFAULT '';
ALTER TABLE inventory ADD COLUMN receive_no TEXT DEFAULT '';
ALTER TABLE purchase_orders ADD COLUMN rcv_state TEXT DEFAULT '';
