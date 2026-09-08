-- V11.233 合同模块改造: 合同表记录选用的模板名称(现结/按月结算/预付款+验收后尾款)
ALTER TABLE contracts ADD COLUMN template_name TEXT DEFAULT '';
