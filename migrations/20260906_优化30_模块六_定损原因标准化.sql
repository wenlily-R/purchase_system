-- V11.224 采购优化3.0 模块六: 定损单「设备损坏原因」标准化(一级+二级, ISO14224参考)
-- 说明: damage_reason_cat/damage_reason_sub/damage_reason_note 三列的补列由 app.py init_db 幂等执行
--       (老库启动/重启时自动补充, 与模块一/三/五同机制), 本文件仅为双机迁移记录占位, 不含重复ALTER
SELECT 1;
