-- 20260905 设备维修2.0改造(审批双中心/维修独立/大小件验收/加急线下/使用周期)
-- 双机迁移: 本机执行 migrate_db.py, Mac pull 后执行 python3 migrate_db.py

-- ① repair_plans 补列(幂等, 老单默认值兼容)
ALTER TABLE repair_plans ADD COLUMN item_class TEXT DEFAULT '';      -- 维修物件分类 小件/大件(2.0验收分轨)
ALTER TABLE repair_plans ADD COLUMN accept_scene TEXT DEFAULT '';    -- 验收场景 服务商出厂验收/回厂安装后验收
ALTER TABLE repair_plans ADD COLUMN accept_signs TEXT DEFAULT '';    -- JSON 大件三方签字 [{role,name,opinion,time}]
ALTER TABLE repair_plans ADD COLUMN offline_papers TEXT DEFAULT '';  -- JSON 加急线下签字纸质件归档 [{name,path,by,opinion,time}]
ALTER TABLE repair_plans ADD COLUMN prev_gap_days INTEGER DEFAULT 0; -- 距上次维修完成天数(设备使用周期,服务商质量评估)

-- ② 维修审批链默认升级为三级岗位链(部门主管→机电厂长→生产厂长→采购执行)
--    仅当现配置仍为出厂默认(1级分管领导0-100万)时替换, 用户已自定义则跳过
--    角色映射(系统设置可视化可改): 部门主管=部门负责人(穆娇), 机电厂长=分管领导(赵培姝), 生产厂长=分管领导(邢果, 总经理账号开通后可在系统设置换)
UPDATE approval_flow_config SET role=(SELECT role FROM users WHERE name='穆娇' AND is_active=1 LIMIT 1), approver=(SELECT username FROM users WHERE name='穆娇' AND is_active=1 LIMIT 1), label='部门主管-1级' WHERE biz_type='repair_plan' AND level_no=1 AND role='分管领导' AND max_amount=1000000 AND label='维修定损-1级' AND EXISTS(SELECT 1 FROM users WHERE name='穆娇' AND is_active=1);
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver)
  SELECT 'repair_plan',2,'分管领导',0,1000000,'机电厂长-2级','lizong' WHERE NOT EXISTS(SELECT 1 FROM approval_flow_config WHERE biz_type='repair_plan' AND level_no=2);
INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver)
  SELECT 'repair_plan',3,'分管领导',0,1000000,'生产厂长-3级','xingguo' WHERE NOT EXISTS(SELECT 1 FROM approval_flow_config WHERE biz_type='repair_plan' AND level_no=3);
