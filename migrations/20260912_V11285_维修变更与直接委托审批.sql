-- V11.285 维修金额变更审批 + 小额直接委托审批
-- 1) 申请上记录: 原审批预估价 / 定损变更额 / 变更状态
ALTER TABLE purchase_requests ADD COLUMN repair_est_orig REAL DEFAULT 0;
ALTER TABLE purchase_requests ADD COLUMN repair_change_amt REAL DEFAULT 0;
ALTER TABLE purchase_requests ADD COLUMN repair_change_status TEXT DEFAULT '';
-- 2) 审批链配置: 均由分管领导(穆娇)确认 (与维修申请审批人一致)
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver) VALUES('repair_change',1,'分管领导',0,999999999,'维修变更-分管领导-mujiao','mujiao');
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver) VALUES('repair_direct',1,'分管领导',0,999999999,'直接委托-分管领导-mujiao','mujiao');
