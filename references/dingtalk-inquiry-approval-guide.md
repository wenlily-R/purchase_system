# 钉钉审批模板配置指南（V11.76 询价审批流程）

## 业务背景

**V11.76 核心需求**：三方询价完成后，领导在钉钉上直接选择供应商，审批通过自动生成采购订单。采购订单本身不再需要审批。

**关键区分**：
- ❌ **旧流程**：三方报价→自动创建采购订单草稿→发起采购订单审批→领导审批→订单生效
- ✅ **新流程**：三方报价→发起询价审批(新模板)→领导在钉钉选供应商→审批通过→自动创建订单并生效

---

## 新审批模板配置

### 模板信息

- **模板名称**：采购比价审批（建议名称）
- **流程编号(Proc Code)**：`PROC-3153AC73-0054-4F09-803B-D416EC485F2D`
- **回调地址**：`http://127.0.0.1:5899/api/dingtalk/callback`（或公网隧道地址）

### 表单字段设计

系统会自动填充以下字段，领导只需选择供应商：

| 控件名 | 类型 | 说明 | 是否必填 |
|--------|------|------|----------|
| 询价单号 | 单行文本 | 系统自动填充（如 XJ-202608-0001） | 否 |
| 物资名称 | 单行文本 | 系统自动填充 | 否 |
| 报价详情 | 多行文本 | 三家供应商报价明细 | 否 |
| **选定供应商** | **单选** | **测试厂家1/测试厂家2/测试厂家3** | **是** |
| 备注 | 多行文本 | 说明文字 | 否 |

### ⚠️ 关键：选定供应商控件配置

这是整个流程的核心！领导必须在钉钉上能选择供应商。

**单选控件配置**：
- 选项1：`测试厂家1`（或实际供应商名称）
- 选项2：`测试厂家2`
- 选项3：`测试厂家3`
- **默认值**：空（让领导必须选）
- **校验**：必填

**⚠️ 注意**：选项文本必须与 `inquiry_suppliers.supplier_name` 字段一致（或能映射到系统内的供应商ID）。系统当前测试数据用"测试厂家1/2/3"，正式上线要换成真实供应商名称。

---

## 审批流程配置

### 审批节点

- **审批人**：分管领导（邢果）
- **处理方式**：单人审批
- **是否会签**：否

### 回调处理

审批完成后，钉钉会回调 `/api/dingtalk/callback`，系统解析 `formComponentValues`：
- 读取 `选定供应商` 字段的 value
- 找到对应的 `inquiry_suppliers.id`
- 创建采购订单（status='已通过'）
- 更新询价单状态为'已生成订单'

---

## 系统配置更新

代码已更新 `sys_config.dingtalk_approval_codes`：

```json
{
  "purchase_request": "PROC-C6EC44EF-BE32-4C54-A14A-9F8CD085F739",
  "purchase_order": "PROC-29D5B047-04C9-4687-815E-821A63CC3CEC",  // 已停用
  "contract": "PROC-21EA081B-705F-4DE0-A341-FA59433E5151",
  "inquiry_approval": "PROC-3153AC73-0054-4F09-803B-D416EC485F2D"  // 新增
}
```

**关键**：三档报价完成后，系统发起 `biz_type='inquiry_approval'` 的审批实例，使用新 Proc Code。

---

## 端到端测试流程

### 测试数据准备

```sql
-- 1. 确保有已通过状态的申请单
UPDATE purchase_requests SET status='已通过' WHERE req_no='SC2026082101';

-- 2. 发起三方询价
-- 前端：申请单详情页 → 发起三方询价 → 添加3家供应商

-- 3. 商家报价（模拟或真报价）
-- POST /api/inquiry/vendor/{token}/quote
-- {"quote_price": 1500} 等
```

### 测试步骤

1. **完成三家报价**
   - 预期：询价单状态变为「定标审批中」
   - 预期：自动生成丁钉钉审批实例（新模板）

2. **查看钉钉审批**
   - 登录邢果钉钉 → 工作台 → 审批
   - 应看到「采购比价审批」待办
   - 表单应显示三家报价和选定供应商下拉

3. **领导选择供应商**
   - 选择一家供应商（如"测试厂家3"）
   - 提交审批

4. **验证系统结果**
   ```sql
   -- 查询订单状态
   SELECT order_no, status, total_amount, supplier 
   FROM purchase_orders 
   ORDER BY id DESC LIMIT 3;
   
   -- 查询询价单状态
   SELECT inq_no, status, selected_supplier_id 
   FROM inquiries WHERE id=1;
   
   -- 查询审批实例
   SELECT biz_type, biz_id, status FROM dingtalk_instances ORDER BY id DESC LIMIT 3;
   ```
   
   - 预期：订单 status='已通过'
   - 预期：询价单 status='已生成订单'
   - 预期：选定供应商ID匹配

---

## 问题排查

### 问题1：审批还是用旧模板

**症状**：钉钉收到的是"采购订单审批"而不是"采购比价审批"

**排查**：
```sql
SELECT key, value FROM sys_config WHERE key='dingtalk_approval_codes';
```

确保 `inquiry_approval` 对应正确的 Proc Code。

### 问题2：审批通过后订单没生成

**排查**：
```sql
-- 查看审批实例状态
SELECT * FROM dingtalk_instances WHERE biz_type='inquiry_approval' ORDER BY id DESC;

-- 查看询价审批记录
SELECT * FROM inquiry_approvals ORDER BY id DESC;

-- 查看日志
SELECT * FROM logs WHERE detail LIKE '%询价审批%' ORDER BY id DESC LIMIT 10;
```

**常见原因**：
- `选定供应商` 控件名填错（必须是表单设计里的准确名称）
- 审批回调未正确解析 formComponentValues

### 问题3：表单里看不到报价详情

**排查**：检查 `dt_build_form` 函数中 `inquiry_approval` 分支的输出格式。

---

## 上线注意事项

1. **供应商名称同步**：钉钉表单的选定供应商选项要换成真实供应商名称
2. **审批人绑定**：确保邢果的 dingtalk_userid 已绑定到 users 表
3. **回调地址**：公网隧道地址变更时，需在钉钉后台更新回调地址
4. **模板发布**：审批模板必须在钉钉后台「发布」才能生效

---

## 相关版本

- **V11.75**：三方报价完成自动创建采购订单草稿 + 发起钉钉审批
- **V11.76**：改用新审批模板，领导选供应商 → 订单自动生效
