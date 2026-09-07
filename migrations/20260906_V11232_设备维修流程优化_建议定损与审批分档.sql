-- V11.232 设备维修流程优化说明书落地(2026-09-06)
-- 列 repair_suggest/void_reason 由 app.py init_db 幂等 ALTER 补齐(重启自动), 无需在此 ALTER
-- 审批分档(见下方 UPDATE/INSERT) 与 sys_config 阈值 由 migrate_db 执行, 双机同步

-- V11.232 设备维修流程优化(2026-09-06): 列由 init_db 幂等补(repair_suggest/void_reason); 本文件=审批分档+阈值配置
-- ① 审批分档: 原三级全段(0-100万) → 高金额档 10万+(三级: 部门主管→分管领导→分管领导); 新增 普通档 0-<10万(采购部长1级审批)
UPDATE approval_flow_config SET min_amount=100000 WHERE biz_type='repair_plan' AND min_amount=0 AND max_amount=1000000;
INSERT INTO approval_flow_config(biz_type,level_no,role,approver,min_amount,max_amount,label)
SELECT 'repair_plan',1,'部门负责人','zhangjl',0,99999,'部门主管-普通维修(采购部长)-1级'
WHERE NOT EXISTS(SELECT 1 FROM approval_flow_config WHERE biz_type='repair_plan' AND label LIKE '%普通维修%');
-- ② 阈值配置: 小额线下线(1000) / 高金额升级线(10万)
INSERT INTO sys_config(key,value) SELECT 'repair_small_threshold','1000' WHERE NOT EXISTS(SELECT 1 FROM sys_config WHERE key='repair_small_threshold');
INSERT INTO sys_config(key,value) SELECT 'repair_hi_amount','100000' WHERE NOT EXISTS(SELECT 1 FROM sys_config WHERE key='repair_hi_amount');
