-- V11.300 供应商档案新增「银行行号」（《修理修缮合同》第七条收款账户信息自动填充用）
-- 背景: 修理修缮合同收款账户信息含 收款账户名称/收款账号/收款银行/银行行号 四项, 系统需从供应商档案读取
ALTER TABLE suppliers ADD COLUMN bank_no TEXT DEFAULT '';
