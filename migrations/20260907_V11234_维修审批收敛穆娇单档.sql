-- V11.234 维修审批收敛为「穆娇 1 级全档」(2026-09-07, 用户拍板方案A)
-- 背景: V11.232 曾按说明书配"普通=部长/高金额或大件=三级", 但①迁移误用旧账号名 zhangjl(Mac 无此账号→悬空审批卡死)②与 09-07 08:41 用户拍板的"穆娇单级全档"冲突
-- 收敛: 删除 repair_plan 全部审批档 → 重建单档: name='穆娇' 且 role='部门负责人' 的账号(双机自适应: Windows 库 username=zhangjl / Mac 库 username=mujiao), 金额 0~999999999 全档
-- 若某机 users 表无穆娇行则不插(该机需先建账号), 不会产生悬空审批人
DELETE FROM approval_flow_config WHERE biz_type='repair_plan';
INSERT INTO approval_flow_config(biz_type,level_no,role,approver,min_amount,max_amount,label)
SELECT 'repair_plan',1,'部门负责人',username,0,999999999,'穆娇-维修审批-1级全档'
FROM users WHERE name='穆娇' AND role='部门负责人' LIMIT 1;
