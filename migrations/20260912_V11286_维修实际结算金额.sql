-- V11.286 维修完工登记: 实际结算金额字段(用于 预估/定损/实际 三口径对比与差异报表)
ALTER TABLE purchase_requests ADD COLUMN repair_actual_amt REAL DEFAULT 0;
