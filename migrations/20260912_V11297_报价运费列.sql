-- V11.297 运费单列: 报价货款与运费分开记录, 含运总价=货款合计+运费
ALTER TABLE inquiry_suppliers ADD COLUMN freight REAL DEFAULT 0;
ALTER TABLE purchase_orders ADD COLUMN freight REAL DEFAULT 0;
