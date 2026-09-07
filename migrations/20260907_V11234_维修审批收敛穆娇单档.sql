-- V11.234 维修审批收敛为「穆娇 1 级全档」(2026-09-07, 用户拍板方案A)
-- 背景: V11.232 曾按说明书配"普通=部长/高金额或大件=三级", 但迁移曾写死旧账号名 zhangjl(已废弃, 任何人机均不应再建该账号) 且与 09-07 08:41 用户拍板的"穆娇单级全档"冲突
-- 收敛: 删除 repair_plan 全部审批档 → 重建单档: 取本机 users 表 name='穆娇' 且启用的账号(role/username 取该行实际值, 双机各自命中), 金额 0~999999999 全档
-- 若某机 users 表尚无姓名='穆娇'的账号则不插(该机需先建穆娇账号), 不会产生悬空审批人
DELETE FROM approval_flow_config WHERE biz_type='repair_plan';
INSERT INTO approval_flow_config(biz_type,level_no,role,approver,min_amount,max_amount,label)
SELECT 'repair_plan',1,role,username,0,999999999,'穆娇-维修审批-1级全档'
FROM users WHERE name='穆娇' AND is_active=1 LIMIT 1;
