-- V11.292 维修合同按维修口径审批: 维修类订单生成的合同 → 分管领导(穆娇)审批
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver) VALUES('repair_contract',1,'分管领导',0,999999999,'维修合同-分管领导-mujiao','mujiao');
