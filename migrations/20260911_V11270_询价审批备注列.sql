-- V11.270: 询价单审批备注列(审批人分项定标留痕: 记录每项选定的供应商)
-- 该列原由"按各物资最低价"审批分支按需 ALTER 添加, 各机库不一致; 此处纳入迁移保证三机对齐
ALTER TABLE inquiries ADD COLUMN approve_note TEXT DEFAULT '';
