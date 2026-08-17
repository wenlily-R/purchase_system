#!/usr/bin/env python3
"""🦐 虾(龙虾) — 采购协同提醒服务
独立于系统本体的提醒引擎: 业务预警 / 待办提醒 / 周期日报 / 每周汇总
由计划任务 ZCE_Xia 每5分钟唤醒; 系统本体只做业务流转, 不做提醒。

启用: 系统配置 xia_enabled=1 (默认已写入)
推送对象: 配置 xia_leader_open_ids(逗号分隔) → 推领导; 不填则取 总经理/分管领导 角色
"""
import sys, os, datetime, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as S  # 复用 app.py 的 db/cfg_get/fs_send/fs_send_reminders

def pushed(c, rule, key, day=None):
    """需求44: 库存预警/待办审批 8小时内持续提醒 → 去重窗口8小时(同规则同对象8h内只推一次)"""
    c.execute("DELETE FROM reminder_log WHERE pushed_at <= datetime('now','localtime','-8 hours')")
    k = f"{(day or datetime.date.today()).isoformat()}:{key}"
    if c.execute("SELECT 1 FROM reminder_log WHERE rule=? AND key=?", (rule, k)).fetchone():
        return True
    c.execute("INSERT INTO reminder_log(rule,key) VALUES(?,?)", (rule, k))
    return False

def leaders(c):
    ids = S.cfg_get('xia_leader_open_ids', '')
    if ids:
        return [x.strip() for x in ids.split(',') if x.strip()]
    return [r['feishu_open_id'] for r in c.execute(
        "SELECT feishu_open_id FROM users WHERE role IN ('总经理','分管领导') "
        "AND feishu_open_id IS NOT NULL AND feishu_open_id!=''").fetchall()]

def main():
    if S.cfg_get('xia_enabled') != '1':
        return
    if not S.feishu_enabled():
        return
    c = S.db()
    now_h = datetime.datetime.now().strftime('%H:%M')
    today = datetime.date.today()

    # ① 业务预警 (审批超时/待采未下单/逾期未回货/订单超期/库存) — 每日去重
    S.fs_send_reminders()

    # ② 待办提醒 09:00 → 领导
    if now_h == '09:00' and not pushed(c, 'daily_todo', '9'):
        n1 = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
        n2 = c.execute("SELECT COUNT(*) FROM deliveries WHERE sign_status='待签收'").fetchone()[0]
        n3 = c.execute("SELECT COUNT(*) FROM payment_requests WHERE status='待审批'").fetchone()[0]
        n4 = c.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0]
        text = (f"📌 **今日待办汇总 {today.isoformat()}**\n"
                f"- 待审批: {n1} 条\n- 待签收: {n2} 单\n- 待付款: {n3} 笔\n- 库存预警: {n4} 项\n"
                f"虾将持续跟进, 请相关责任人及时处理")
        for oid in leaders(c):
            S.fs_send(oid, text, 'blue')

    # ③ 周期日报 18:00 → 领导
    if now_h == '18:00' and not pushed(c, 'daily_report', '18'):
        buy = c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders "
                        "WHERE date(created_at)=date('now','localtime')").fetchone()[0]
        n_in = c.execute("SELECT COUNT(*) FROM receivings WHERE date(created_at)=date('now','localtime')").fetchone()[0]
        n_pay = c.execute("SELECT COUNT(*) FROM payment_requests WHERE date(created_at)=date('now','localtime')").fetchone()[0]
        n_approve = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='approved' "
                              "AND date(processed_at)=date('now','localtime')").fetchone()[0]
        text = (f"📋 **采购日报 {today.isoformat()}**\n"
                f"- 今日订单金额: ¥{buy:,.0f}\n- 今日入库: {n_in} 单\n"
                f"- 今日付款申请: {n_pay} 笔\n- 今日审批完成: {n_approve} 条")
        for oid in leaders(c):
            S.fs_send(oid, text, 'blue')

    # ④ 每周汇总 周一 09:00 → 领导 (系统周报同时推报表群/管理员)
    if today.weekday() == 0 and now_h == '09:00' and not pushed(c, 'weekly_leader', 'wk'):
        buy7 = c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders "
                         "WHERE created_at >= datetime('now','localtime','-7 days')").fetchone()[0]
        n_pend = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
        n_over = c.execute("SELECT COUNT(*) FROM deliveries WHERE sign_status='待签收' AND delivery_date < date('now')").fetchone()[0]
        n_warn = c.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0]
        text = (f"📊 **本周采购汇总 (截至 {today.isoformat()})**\n"
                f"- 近7天订单金额: ¥{buy7:,.0f}\n- 当前待审批: {n_pend} 条\n"
                f"- 逾期未签收: {n_over} 单\n- 库存预警: {n_warn} 项")
        for oid in leaders(c):
            S.fs_send(oid, text, 'blue')

    c.commit(); c.close()

if __name__ == '__main__':
    main()
