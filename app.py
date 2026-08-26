#!/usr/bin/env python3
"""正成能源采购系统 — PDF完整版PRD实现"""
import os, sys, json, datetime, hashlib, uuid, functools, threading, time, base64, re, io
import urllib.request, urllib.parse
import glob, secrets
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, send_from_directory, make_response
from flask_cors import CORS
import sqlite3

app = Flask(__name__, template_folder='templates', static_folder='static')

@app.before_request
def write_guard():
    """安全防护: CSRF同源校验 + 系统写锁定(V5.1)
    白名单: 登录/登出/飞书回调(飞书服务器回调, 非用户操作)
    写锁定可通过系统设置页开关(write_lock), 默认开启"""
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        p = request.path
        if p in ('/api/login', '/api/logout', '/api/feishu/callback', '/api/dingtalk/callback', '/api/dingtalk/sso') or p.startswith('/api/inquiry/vendor/'):
            return None
        # CSRF: 带Origin/Referer的跨站写请求一律拒绝(同源才放行)
        origin = request.headers.get('Origin') or request.headers.get('Referer')
        if origin:
            ohost = urllib.parse.urlparse(origin).netloc
            if ohost and ohost != request.host:
                return jsonify({'error': '请求来源校验失败, 已拒绝'}), 403
        if not can_manage_config():
            # V8.0: 全局写锁默认关闭 — 所有启用账号可进行日常业务操作
            # 管理类操作(配置/删除/撤回/编辑单据)由各接口的 can_manage_config 自行保护
            if cfg_get('write_lock', '0') == '1':
                return jsonify({'error': '系统已锁定：仅系统管理员可操作，如需操作请联系管理员'}), 403
    return None
app.secret_key = 'zhengcheng-purchase-2026-secret-key'
CORS(app, supports_credentials=True)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'data', 'purchase.db')
os.makedirs(os.path.join(BASE, 'data'), exist_ok=True)

def db():
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ── 安全配置: 会话Cookie加固 (V5.1) ──
# V8.0: 会话有效期优化 — 刷新页面保持登录, 8小时不操作才自动登出
app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=24),  # V8.3: 24小时不操作才登出 — 刷新/关浏览器保持登录
    SESSION_COOKIE_NAME='purchase_session',
    SESSION_REFRESH_EACH_REQUEST=False,  # V8.4: 不随每个请求刷新cookie时间戳 — 提交请求后会话不失效(旧cookie始终有效)
)

def now(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def esc_html(s):
    """HTML 转义(商家免登录报价页用, 防注入)"""
    if s is None: return ''
    return str(s).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;').replace("'",'&#39;')
def today(): return datetime.date.today().strftime('%Y-%m-%d')

# ── 密码安全: PBKDF2-SHA256 (V5.1, 兼容旧MD5登录时自动迁移) ──
PBKDF2_ITER = 120000
def hash_password(pwd):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', pwd.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ITER).hex()
    return 'pbkdf2$' + salt + '$' + h

def verify_password(pwd, stored):
    if not stored: return False
    if stored.startswith('pbkdf2$'):
        try:
            _, salt, h = stored.split('$')
            calc = hashlib.pbkdf2_hmac('sha256', pwd.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ITER).hex()
            return h == calc
        except Exception:
            return False
    return hashlib.md5(pwd.encode('utf-8')).hexdigest() == stored

def is_legacy_md5(stored):
    return bool(stored) and len(stored) == 32 and re.match(r'^[0-9a-f]{32}$', stored) is not None

# ── 登录安全: 失败锁定 + 审计 (V5.1) ──
MAX_FAILS, LOCK_MINUTES = 5, 15
def get_client_ip():
    return (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()

def login_fail_count(c, username, ip):
    r = c.execute("SELECT COUNT(*) FROM login_attempts WHERE username=? AND ip=? AND success=0 AND created_at >= datetime('now','localtime',?)",
                  (username, ip, '-%d minutes' % LOCK_MINUTES)).fetchone()[0]
    return r

# ── 自动备份: sqlite backup API, 保留14份 (V5.1) ──
BACKUP_DIR = '/Users/a0/Desktop/正成能源/04_数据库备份/自动备份'  # 2026-08-17 整理: 备份统一放正成能源/04_数据库备份/自动备份
def backup_db():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        name = 'purchase_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S') + '.db'
        tmp = os.path.join(BACKUP_DIR, name + '.tmp')
        src_c = sqlite3.connect(DB, timeout=30)
        dst = sqlite3.connect(tmp)
        src_c.backup(dst)
        dst.close(); src_c.close()
        os.replace(tmp, os.path.join(BACKUP_DIR, name))
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'purchase_*.db')), reverse=True)
        for f in files[14:]:
            try: os.remove(f)
            except Exception: pass
        return name
    except Exception as e:
        return 'ERROR:' + str(e)

def last_backup_name():
    try:
        files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'purchase_*.db')), reverse=True)
        if files:
            return os.path.basename(files[0]).replace('purchase_', '').replace('.db', '')
    except Exception: pass
    return ''

def _startup_backup():
    try:
        if cfg_get('last_backup_date') != today():
            name = backup_db()
            cfg_set('last_backup_date', today())
            print('  [自动备份] 启动备份完成:', name)
    except Exception as e:
        print('  [自动备份] 启动备份失败:', e)

def _daily_backup_loop():
    while True:
        time.sleep(30)
        try:
            if time.localtime().tm_hour == 3 and cfg_get('last_backup_date') != today():
                name = backup_db()
                cfg_set('last_backup_date', today())
                print('  [自动备份] 定时备份完成:', name)
        except Exception:
            pass

def gen_no(prefix, table, col, c=None):
    """通用单号: 前缀-年月-序号, 如 CG-202608-0001
    用 MAX 取当前最大序号(并发安全, 避免 COUNT 竞态导致重复单号)"""
    conn = c if c else db()
    m = datetime.date.today().strftime('%Y%m')
    r = conn.execute(f"SELECT MAX({col}) m FROM {table} WHERE {col} LIKE ?", (f"{prefix}-{m}%",)).fetchone()
    if not c: conn.close()
    cur = int(str(r['m']).split('-')[-1]) if r and r['m'] else 0
    return f"{prefix}-{m}-{cur+1:04d}"

# V11.45: 部门→单号前缀映射(申请单号前两位随部门变化, 如生产=SC 财务=CW)
DEPT_PREFIX = {
    '生产部': 'SC', '财务部': 'CW', '机电部': 'JD', '机电': 'JD', '信息部': 'XX',
    '后勤部': 'HQ', '工程部': 'GC', '维修车间': 'WX', '综合办': 'ZH', '调度': 'DD',
    '化验室': 'HY', '库房': 'KF', '绿化': 'LH', '环卫': 'HW', '生产车队': 'CD',
    '采购部': 'CG', '采购与供应链部': 'CG',
}

def dept_prefix(dept):
    """部门 → 单号前缀, 未知部门默认 SC(采购)"""
    if not dept:
        return 'SC'
    d = str(dept).strip()
    if d in DEPT_PREFIX:
        return DEPT_PREFIX[d]
    # 包含匹配(如 "机电部2" → JD)
    for k, v in DEPT_PREFIX.items():
        if k and k in d:
            return v
    return 'SC'

def gen_req_no(dept=None, c=None):
    """采购申请单号: 部门前缀 + 年月日 + 当日总序号, 如 SC2026082101(生产部当天第1张) / CW2026082102(财务部当天第2张)
    序号=当天所有部门申请单的总序号(不分部门), 前缀按部门; 用 MAX 取当天最大序号(并发安全)"""
    conn = c if c else db()
    m = datetime.date.today().strftime('%Y%m%d')
    prefix = dept_prefix(dept)
    # V11.45b: 序号按当天所有申请单(不分部门前缀), 取当天任意前缀下的最大尾号
    r = conn.execute("SELECT req_no m FROM purchase_requests WHERE req_no LIKE ?", (f"__{m}%",)).fetchall()
    cur = 0
    for row in r:
        no = str(row['m'])
        # 尾号 = 最后2位
        try:
            tail = int(no[-2:])
            if tail > cur:
                cur = tail
        except Exception:
            continue
    if not c: conn.close()
    return f"{prefix}{m}{cur+1:02d}"

def gen_contract_no(c=None):
    """合同编码规则(55.docx需求7): HQZC-SBCG-份号-年份, 如 HQZC-SBCG-019-2026
    份号=当年合同序号, 年份=签订年份; 用 MAX 取当前最大序号(并发安全)"""
    conn = c if c else db()
    year = datetime.date.today().strftime('%Y')
    r = conn.execute("SELECT MAX(contract_no) m FROM contracts WHERE contract_no LIKE ?", (f'HQZC-SBCG-%-{year}',)).fetchone()
    if not c: conn.close()
    cur = 0
    if r and r['m']:
        try: cur = int(str(r['m']).split('-')[-2])
        except Exception: cur = 0
    return f'HQZC-SBCG-{cur+1:03d}-{year}'

def log(op, action, detail, c=None):
    if c: c.execute("INSERT INTO logs(operator,action,detail,created_at) VALUES(?,?,?,?)", (op,action,detail,now()))
    else: cc=db();cc.execute("INSERT INTO logs(operator,action,detail,created_at) VALUES(?,?,?,?)",(op,action,detail,now()));cc.commit();cc.close()

def dict_row(r): return dict(r) if r else None

# ── Auth decorator ──
def login_required(f):
    @functools.wraps(f)
    def wrap(*a,**kw):
        if 'user_id' not in session:
            return jsonify({'error':'login required'}),401
        return f(*a,**kw)
    return wrap

def can_manage_config():
    """系统配置管理权限: 系统管理员 或 sys_config.config_users 里指定的用户名(逗号分隔)"""
    if session.get('user_role') == '系统管理员':
        return True
    extra = cfg_get('config_users', '')
    return session.get('username') in [x.strip() for x in extra.split(',') if x.strip()]

def admin_required(f):
    """需求44-权限强约束: 系统管理员或指定配置管理用户可配置/改基础数据/传模板"""
    @functools.wraps(f)
    def wrap(*a,**kw):
        if not can_manage_config():
            return jsonify({'error':'仅系统管理员可操作'}),403
        return f(*a,**kw)
    return wrap

# ============================================================
# INIT DB
# ============================================================
def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER);
    """)
    ver = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()[0]
    if ver is None:
        # Fresh install or upgrade from v3
        # Drop old v3 tables that have incompatible schemas
        conn.executescript("""
            DROP TABLE IF EXISTS users;
            DROP TABLE IF EXISTS purchase_requests;
            DROP TABLE IF EXISTS approval_instances;
        """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS departments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, code TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
            name TEXT NOT NULL, phone TEXT, role TEXT DEFAULT '员工',
            dept_id INTEGER, title TEXT, is_active INTEGER DEFAULT 1,
            FOREIGN KEY(dept_id) REFERENCES departments(id)
        );
        CREATE TABLE IF NOT EXISTS budget_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            dept_id INTEGER, annual_budget REAL DEFAULT 0, used_budget REAL DEFAULT 0,
            fiscal_year TEXT, FOREIGN KEY(dept_id) REFERENCES departments(id)
        );
        CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, cat_code TEXT NOT NULL, name TEXT NOT NULL, spec TEXT, unit TEXT DEFAULT '个', price REAL DEFAULT 0, safe_stock REAL DEFAULT 0, warehouse TEXT DEFAULT '主库房', supplier TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, contact TEXT, phone TEXT, category TEXT, level TEXT DEFAULT '一般供应商', bank TEXT, account TEXT, tax_id TEXT, invoice_type TEXT DEFAULT '增值税专用发票', rating REAL DEFAULT 4.0, status TEXT DEFAULT '正常');
        CREATE TABLE IF NOT EXISTS purchase_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, req_no TEXT UNIQUE NOT NULL, dept TEXT, requester TEXT, requester_id INTEGER,
            budget_code TEXT, purpose TEXT, target_date TEXT, status TEXT DEFAULT '待审批',
            total_estimated REAL DEFAULT 0, remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS request_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, req_id INTEGER NOT NULL, item_name TEXT NOT NULL, spec TEXT,
            unit TEXT DEFAULT '个', quantity REAL DEFAULT 1, estimated_price REAL DEFAULT 0, total_price REAL DEFAULT 0,
            remark TEXT, FOREIGN KEY(req_id) REFERENCES purchase_requests(id)
        );
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE NOT NULL, req_id INTEGER,
            item_name TEXT, spec TEXT, quantity REAL DEFAULT 1, unit TEXT DEFAULT '个',
            price REAL DEFAULT 0, amount REAL DEFAULT 0, tax_rate REAL DEFAULT 13, tax_amount REAL DEFAULT 0,
            total_amount REAL DEFAULT 0, supplier TEXT, requester TEXT, category TEXT,
            owner TEXT, owner_id INTEGER, target_date TEXT, status TEXT DEFAULT '待审批',
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS price_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT, req_id INTEGER, order_id INTEGER, supplier TEXT,
            price REAL, quantity REAL, total REAL, delivery_cycle TEXT, warranty TEXT,
            quote_file TEXT, is_selected INTEGER DEFAULT 0, remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, contract_no TEXT UNIQUE NOT NULL, order_id INTEGER, contract_name TEXT, supplier TEXT, amount REAL DEFAULT 0, sign_date TEXT, start_date TEXT, end_date TEXT, content TEXT, file_path TEXT, status TEXT DEFAULT '执行中', remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_no TEXT UNIQUE NOT NULL, order_id INTEGER, contract_id INTEGER, supplier TEXT, item_name TEXT, spec TEXT, quantity REAL DEFAULT 0, unit TEXT DEFAULT '个', driver_name TEXT, vehicle_no TEXT, delivery_date TEXT, receiver TEXT, sign_status TEXT DEFAULT '待签收', sign_time TEXT, remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS receivings (id INTEGER PRIMARY KEY AUTOINCREMENT, receive_no TEXT UNIQUE NOT NULL, delivery_id INTEGER, order_id INTEGER, item_name TEXT, spec TEXT, quantity REAL DEFAULT 0, unit TEXT DEFAULT '个', qualified_qty REAL DEFAULT 0, defective_qty REAL DEFAULT 0, inspector TEXT, warehouse TEXT DEFAULT '主库房', status TEXT DEFAULT '待检验', received_at TEXT, remark TEXT, attachments TEXT DEFAULT '', dept TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT, invoice_code TEXT, order_id INTEGER, supplier TEXT, amount REAL DEFAULT 0, tax_amount REAL DEFAULT 0, total_amount REAL DEFAULT 0, invoice_date TEXT, invoice_type TEXT DEFAULT '增值税专用发票', file_path TEXT, status TEXT DEFAULT '待验证', remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS credit_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, credit_no TEXT UNIQUE NOT NULL, order_id INTEGER, category TEXT, supplier TEXT, item_name TEXT, amount REAL DEFAULT 0, invoice_no TEXT, attachments TEXT, status TEXT DEFAULT '待审批', remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS payment_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, payment_no TEXT UNIQUE NOT NULL, credit_id INTEGER, payment_type TEXT DEFAULT '正常付款', supplier TEXT, amount REAL DEFAULT 0, contract_id INTEGER, status TEXT DEFAULT '待审批', paid_at TEXT, remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, item_name TEXT, spec TEXT, cat_code TEXT, unit TEXT DEFAULT '个', quantity REAL DEFAULT 0, safe_stock REAL DEFAULT 0, warehouse TEXT DEFAULT '主库房', price REAL DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime')), UNIQUE(item_name,spec,warehouse));
        CREATE TABLE IF NOT EXISTS requisitions (id INTEGER PRIMARY KEY AUTOINCREMENT, req_no TEXT UNIQUE NOT NULL, dept TEXT, requester TEXT, item_name TEXT, spec TEXT, quantity REAL DEFAULT 0, unit TEXT DEFAULT '个', purpose TEXT, status TEXT DEFAULT '待审批', issued_at TEXT, receiver TEXT DEFAULT '', receive_dept TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS approval_flow_config (id INTEGER PRIMARY KEY AUTOINCREMENT, biz_type TEXT NOT NULL, level_no INTEGER NOT NULL, role TEXT NOT NULL, min_amount REAL DEFAULT 0, max_amount REAL DEFAULT 9999999, label TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS approval_instances (id INTEGER PRIMARY KEY AUTOINCREMENT, biz_type TEXT NOT NULL, biz_id INTEGER NOT NULL, level_no INTEGER NOT NULL, role TEXT, approver TEXT DEFAULT '', approver_id INTEGER, status TEXT DEFAULT 'pending', comment TEXT DEFAULT '', processed_at TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, title TEXT, content TEXT, biz_type TEXT, biz_id INTEGER, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, operator TEXT, action TEXT, detail TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS sys_config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS feishu_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_code TEXT UNIQUE, biz_type TEXT, biz_id INTEGER,
            status TEXT DEFAULT 'pending', error TEXT, created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS dingtalk_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, instance_code TEXT UNIQUE, biz_type TEXT, biz_id INTEGER,
            status TEXT DEFAULT 'pending', error TEXT, created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS dingtalk_push_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, biz_type TEXT, biz_id TEXT, doc_no TEXT,
            push_type TEXT DEFAULT 'auto', target_user TEXT, target_userid TEXT,
            operator TEXT DEFAULT '系统', content TEXT, created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS reminder_log (
            rule TEXT NOT NULL, key TEXT NOT NULL, pushed_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY(rule,key));
        CREATE TABLE IF NOT EXISTS inventory_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, count_no TEXT UNIQUE NOT NULL, status TEXT DEFAULT '盘点中',
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), finished_at TEXT);
        CREATE TABLE IF NOT EXISTS inventory_count_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, count_id INTEGER NOT NULL, inventory_id INTEGER,
            item_name TEXT, book_qty REAL DEFAULT 0, actual_qty REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS inventory_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, adj_no TEXT UNIQUE NOT NULL, adj_type TEXT NOT NULL,
            inventory_id INTEGER, item_name TEXT, spec TEXT, unit TEXT DEFAULT '个', book_qty REAL DEFAULT 0,
            adj_qty REAL DEFAULT 0, reason TEXT, status TEXT DEFAULT '待审批', source TEXT DEFAULT '手动',
            created_by TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), approved_at TEXT);
        CREATE TABLE IF NOT EXISTS contract_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, file_path TEXT,
            version TEXT DEFAULT 'V1', status TEXT DEFAULT '启用', is_default INTEGER DEFAULT 0,
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        -- V5.0 库存流水表: 记录每次入库/出库/作废对库存的影响(溯源用)
        CREATE TABLE IF NOT EXISTS inventory_flows (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT, spec TEXT, unit TEXT,
            flow_type TEXT,        -- 入库 / 出库 / 作废入库 / 作废出库 / 盘点调整
            doc_type TEXT,         -- receiving / requisition / count
            doc_id INTEGER, doc_no TEXT, qty REAL DEFAULT 0, balance_after REAL DEFAULT 0,
            operator TEXT, remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        -- V5.0 出库单明细子表(一张出库单多商品)
        CREATE TABLE IF NOT EXISTS requisition_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requisition_id INTEGER NOT NULL, item_name TEXT, spec TEXT, unit TEXT DEFAULT '个',
            quantity REAL DEFAULT 0, purpose TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        -- V11.0 三方询价
        CREATE TABLE IF NOT EXISTS inquiries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, inq_no TEXT UNIQUE NOT NULL, req_id INTEGER NOT NULL,
            title TEXT, purpose TEXT, status TEXT DEFAULT '询价中', selected_supplier_id INTEGER DEFAULT 0,
            deadline TEXT DEFAULT '', created_by TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), updated_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS inquiry_suppliers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, inquiry_id INTEGER NOT NULL, supplier_name TEXT NOT NULL,
            contact TEXT, phone TEXT, token TEXT UNIQUE NOT NULL,
            quote_price REAL DEFAULT 0, quote_remark TEXT, quote_time TEXT,
            quote_delivery TEXT DEFAULT '', quote_warranty TEXT DEFAULT '',
            is_selected INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')));
    """)
    conn.commit()

    if conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0] == 0:
        conn.execute("INSERT INTO departments VALUES(1,'生产部','SC'),(2,'维修车间','WX'),(3,'后勤部','HQ'),(4,'综合办','ZHB'),(5,'财务部','CW')")
        for _un,_pw,_nm,_ph,_role,_did,_title in [
            ('admin','admin123','系统管理员','13800000000','系统管理员',4,'系统管理员'),
            ('zhangjl','123456','张经理','13800000001','部门负责人',1,'生产部经理'),
            ('lizong','123456','李总','13800000002','分管领导',None,'副总经理'),
            ('wangcw','123456','王财务','13800000003','财务',5,'财务主管'),
            ('zongjl','123456','赵总','13800000004','总经理',None,'总经理'),
            ('yuangong','123456','刘员工','13800000005','员工',1,'操作工'),
        ]:
            conn.execute("INSERT INTO users(username,password,name,phone,role,dept_id,title,is_active) VALUES(?,?,?,?,?,?,?,1)",
                         (_un, hash_password(_pw), _nm, _ph, _role, _did, _title))
        conn.executescript(f"""
            INSERT INTO suppliers VALUES(1,'恒安劳保用品','刘经理','13811111111','后勤类','核心供应商','工行001','62220001','91410100MA3X','增值税专用发票',4.5,'正常');
            INSERT INTO suppliers VALUES(2,'晋工机械有限公司','赵工','13822222222','维修配件类','核心供应商','建行002','62220002','91410100MA5X','增值税专用发票',4.8,'正常');
            INSERT INTO suppliers VALUES(3,'河曲建材批发','王老板','13833333333','建材类','一般供应商','农行003','62220003','','增值税普通发票',3.5,'正常');
            INSERT INTO categories VALUES(1,'BB','备品备件'),(2,'SB','设备'),(3,'JC','建材'),(4,'WX','维修耗材'),(5,'BG','办公用品');
            INSERT INTO items VALUES(1,'BB','轴承6205','6205-2RS','个',58.0,100,'主库房','晋工机械');
            INSERT INTO items VALUES(2,'WX','润滑油46#','46#液压油','桶',320.0,10,'主库房','晋工机械');
            INSERT INTO items VALUES(3,'BG','一次性纸杯','230ml','箱',100.0,50,'后勤库','恒安劳保用品');
            INSERT INTO inventory VALUES(1,1,'轴承6205','6205-2RS','BB','个',200,100,'主库房',58.0,datetime('now','localtime'));
            INSERT INTO inventory VALUES(2,2,'润滑油46#','46#液压油','WX','桶',15,10,'主库房',320.0,datetime('now','localtime'));
            INSERT INTO inventory VALUES(3,3,'一次性纸杯','230ml','BG','箱',80,50,'后勤库',100.0,datetime('now','localtime'));
            INSERT INTO budget_accounts VALUES(1,'SC-BG-2026','生产部办公耗材预算',1,50000,0,'2026');
            INSERT INTO budget_accounts VALUES(2,'SC-WX-2026','生产部维修配件预算',1,200000,0,'2026');
            INSERT INTO budget_accounts VALUES(3,'HQ-BG-2026','后勤部办公预算',3,30000,0,'2026');
            -- 审批流配置：按金额
            INSERT INTO approval_flow_config VALUES(1,'purchase_request',1,'部门负责人',0,5000,'小额-部门审批');
            INSERT INTO approval_flow_config VALUES(2,'purchase_request',2,'财务',5000,20000,'中额-部门+财务');
            INSERT INTO approval_flow_config VALUES(3,'purchase_request',3,'财务',20000,9999999,'大额-部门+财务+领导');
            INSERT INTO approval_flow_config VALUES(4,'purchase_request',4,'分管领导',20000,9999999,'大额-分管领导');
            INSERT INTO approval_flow_config VALUES(5,'purchase_request',5,'总经理',50000,9999999,'超大额-总经理终审');
        """)
        conn.execute("INSERT INTO _schema_version(version) VALUES(4)")
    # ---- V4.1 飞书对接: 用户飞书ID列(幂等) ----
    try:
        conn.execute("ALTER TABLE users ADD COLUMN feishu_open_id TEXT")
    except Exception:
        pass
    # ---- V5.2 钉钉对接: 用户钉钉ID列(幂等) ----
    try:
        conn.execute("ALTER TABLE users ADD COLUMN dingtalk_userid TEXT")
    except Exception:
        pass
    # ---- V8.5 库存按 名称+规格 独立SKU(修复同名不同规格合并): 重建唯一约束 + 按流水拆分历史合并数据 ----
    try:
        if conn.execute("SELECT value FROM sys_config WHERE key='inventory_spec_migrated'").fetchone() is None:
            conn.executescript("""
                CREATE TABLE inventory_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, item_name TEXT, spec TEXT,
                    cat_code TEXT, unit TEXT DEFAULT '个', quantity REAL DEFAULT 0, safe_stock REAL DEFAULT 0,
                    warehouse TEXT DEFAULT '主库房', price REAL DEFAULT 0, updated_at TEXT DEFAULT (datetime('now','localtime')),
                    tax_rate REAL DEFAULT 13, remark TEXT DEFAULT '', max_stock REAL DEFAULT 0, last_move_date TEXT,
                    expiry_date TEXT DEFAULT '', supplier TEXT DEFAULT '', UNIQUE(item_name,spec,warehouse));
            """)
            old_rows = conn.execute("SELECT * FROM inventory").fetchall()
            # 流水分组: (名称,规格) -> 净数量(入库-出库, 流水出库存负数) + 最近变动时间
            groups = {}
            for f in conn.execute("SELECT item_name, spec, unit, qty, created_at FROM inventory_flows").fetchall():
                k = (f['item_name'], f['spec'] or '')
                g = groups.setdefault(k, {'qty': 0.0, 'unit': f['unit'] or '个', 'last': f['created_at'] or ''})
                g['qty'] += float(f['qty'] or 0)
                if f['created_at'] and f['created_at'] > g['last']:
                    g['last'] = f['created_at']
            used = set()
            for r in old_rows:
                k = (r['item_name'], r['spec'] or '')
                if k in groups and k not in used:
                    used.add(k)
                    qty = groups[k]['qty']; unit = groups[k]['unit']; last = groups[k]['last']
                else:
                    # 无流水的历史行原样保留(数量不变)
                    qty = r['quantity'] or 0; unit = r['unit'] or '个'; last = r['last_move_date'] or r['updated_at'] or ''
                conn.execute(
                    "INSERT INTO inventory_new(id,item_id,item_name,spec,cat_code,unit,quantity,safe_stock,warehouse,price,updated_at,tax_rate,remark,max_stock,last_move_date,expiry_date,supplier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r['id'], r['item_id'], r['item_name'], r['spec'] or '', r['cat_code'] or '', unit, qty,
                     r['safe_stock'] or 0, r['warehouse'] or '主库房', r['price'] or 0, now(), r['tax_rate'] or 13,
                     r['remark'] or '', r['max_stock'] or 0, last, r['expiry_date'] or '', r['supplier'] or ''))
            # 拆分出的新规格行(历史合并数据按流水拆开为独立SKU)
            for k, g in groups.items():
                if k in used:
                    continue
                old = next((r for r in old_rows if r['item_name'] == k[0]), None)
                conn.execute(
                    "INSERT INTO inventory_new(item_name,spec,cat_code,unit,quantity,safe_stock,warehouse,price,updated_at,tax_rate,last_move_date,supplier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (k[0], k[1], (old['cat_code'] or '') if old else '', g['unit'], g['qty'],
                     (old['safe_stock'] or 0) if old else 0, (old['warehouse'] or '主库房') if old else '主库房',
                     (old['price'] or 0) if old else 0, now(), (old['tax_rate'] or 13) if old else 13, g['last'],
                     (old['supplier'] or '') if old else ''))
            conn.execute("DROP TABLE inventory")
            conn.execute("ALTER TABLE inventory_new RENAME TO inventory")
            conn.execute("INSERT OR REPLACE INTO sys_config(key,value) VALUES('inventory_spec_migrated','1')")
            conn.commit()
            log('系统', '库存结构迁移', f'库存按 名称+规格 独立SKU 重建完成: 新增拆分出 {len(groups)-len(used)} 个历史规格行')
    except Exception as _e:
        try:
            log('系统', '库存结构迁移异常', str(_e)[:200])
        except Exception:
            pass
    # ---- V5 合同模板: 无模板时自动生成内置默认模板 ----
    if conn.execute("SELECT COUNT(*) FROM contract_templates").fetchone()[0] == 0:
        try:
            from docx import Document
            doc = Document()
            doc.add_heading('采购合同', 0)
            doc.add_paragraph('合同编号：{合同编号}    订单编号：{订单编号}')
            doc.add_paragraph('甲方（采购方）：{甲方名称}')
            doc.add_paragraph('地址：{甲方地址}    联系人：{甲方联系人}    电话：{甲方电话}')
            doc.add_paragraph('乙方（供应方）：{乙方名称}')
            doc.add_paragraph('地址：{乙方地址}    联系人：{乙方联系人}    电话：{乙方电话}')
            doc.add_paragraph('开户行：{乙方开户行}    账号：{乙方账号}')
            doc.add_paragraph('下单日期：{下单日期}    预计交货日期：{预计交货日期}')
            doc.add_paragraph('结算方式：{结算方式}')
            doc.add_paragraph('采购明细清单：')
            doc.add_paragraph('{明细清单}')
            doc.add_paragraph('合计金额：{合计金额}')
            doc.add_paragraph('本协议一式两份，甲乙双方各执一份，签字盖章后生效。')
            os.makedirs(os.path.join(BASE, 'uploads'), exist_ok=True)
            doc.save(os.path.join(BASE, 'uploads', 'tpl_default.docx'))
            conn.execute("INSERT INTO contract_templates(name,file_path,version,status,is_default,remark) VALUES('标准采购合同模板','tpl_default.docx','V1','启用',1,'系统内置默认模板, 可上传替换')")
        except Exception as e:
            print('内置模板生成失败:', e)
    conn.commit()
    # 各业务审批流种子(幂等): 订单/合同/挂账/付款 默认按金额5档审批
    for _biz in ('purchase_order','contract','credit','payment'):
        if conn.execute("SELECT COUNT(*) FROM approval_flow_config WHERE biz_type=?", (_biz,)).fetchone()[0] == 0:
            conn.execute(f"""INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label) VALUES
                ('{_biz}',1,'部门负责人',0,5000,'小额-部门审批'),
                ('{_biz}',2,'财务',5000,20000,'中额-部门+财务'),
                ('{_biz}',3,'财务',20000,9999999,'大额-部门+财务+领导'),
                ('{_biz}',4,'分管领导',20000,9999999,'大额-分管领导'),
                ('{_biz}',5,'总经理',50000,9999999,'超大额-总经理终审'""")
    # V5.0: 入库/出库审批流(1级部门负责人即可, 出库金额0)
    for _biz in ('receiving','requisition'):
        if conn.execute("SELECT COUNT(*) FROM approval_flow_config WHERE biz_type=?", (_biz,)).fetchone()[0] == 0:
            conn.execute(f"""INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label) VALUES
                ('{_biz}',1,'部门负责人',0,9999999,'入库/出库-部门审批')""")
    # ---- V4.4 需求文档补充: 交易模式/对账/合并开票/强制关联(幂等迁移) ----
    for _tbl, _col, _ddl in [
        # ---- V55 需求变更列(全部幂等) ----
        ('purchase_requests', 'attachments', "ALTER TABLE purchase_requests ADD COLUMN attachments TEXT DEFAULT ''"),
        ('purchase_requests', 'urgent', "ALTER TABLE purchase_requests ADD COLUMN urgent INTEGER DEFAULT 0"),
        ('purchase_orders', 'urgent', "ALTER TABLE purchase_orders ADD COLUMN urgent INTEGER DEFAULT 0"),
        ('purchase_orders', 'attachments', "ALTER TABLE purchase_orders ADD COLUMN attachments TEXT DEFAULT ''"),
        ('contracts', 'urgent', "ALTER TABLE contracts ADD COLUMN urgent INTEGER DEFAULT 0"),
        ('contracts', 'attachment', "ALTER TABLE contracts ADD COLUMN attachment TEXT DEFAULT ''"),
        ('payment_requests', 'urgent', "ALTER TABLE payment_requests ADD COLUMN urgent INTEGER DEFAULT 0"),
        ('payment_requests', 'payment_reason', "ALTER TABLE payment_requests ADD COLUMN payment_reason TEXT DEFAULT ''"),
        ('payment_requests', 'expect_pay_date', "ALTER TABLE payment_requests ADD COLUMN expect_pay_date TEXT DEFAULT ''"),
        ('payment_requests', 'invoice_type', "ALTER TABLE payment_requests ADD COLUMN invoice_type TEXT DEFAULT '专票'"),
        ('payment_requests', 'has_contract', "ALTER TABLE payment_requests ADD COLUMN has_contract TEXT DEFAULT '否'"),
        ('payment_requests', 'contract_attachment', "ALTER TABLE payment_requests ADD COLUMN contract_attachment TEXT DEFAULT ''"),
        ('payment_requests', 'payee_name', "ALTER TABLE payment_requests ADD COLUMN payee_name TEXT DEFAULT ''"),
        ('payment_requests', 'payee_account', "ALTER TABLE payment_requests ADD COLUMN payee_account TEXT DEFAULT ''"),
        ('payment_requests', 'attachments', "ALTER TABLE payment_requests ADD COLUMN attachments TEXT DEFAULT ''"),
        ('inventory', 'tax_rate', "ALTER TABLE inventory ADD COLUMN tax_rate REAL DEFAULT 13"),
        ('inventory', 'remark', "ALTER TABLE inventory ADD COLUMN remark TEXT DEFAULT ''"),
        ('inventory', 'max_stock', "ALTER TABLE inventory ADD COLUMN max_stock REAL DEFAULT 0"),
        ('inventory', 'last_move_date', "ALTER TABLE inventory ADD COLUMN last_move_date TEXT DEFAULT ''"),
        ('inventory', 'expiry_date', "ALTER TABLE inventory ADD COLUMN expiry_date TEXT DEFAULT ''"),
        ('inventory', 'supplier', "ALTER TABLE inventory ADD COLUMN supplier TEXT DEFAULT ''"),
        ('approval_flow_config', 'approver', "ALTER TABLE approval_flow_config ADD COLUMN approver TEXT DEFAULT ''"),
        ('receivings', 'remark', "ALTER TABLE receivings ADD COLUMN remark TEXT DEFAULT ''"),
        ('receivings', 'urgent', "ALTER TABLE receivings ADD COLUMN urgent INTEGER DEFAULT 0"),
        ('purchase_orders', 'trade_mode', "ALTER TABLE purchase_orders ADD COLUMN trade_mode TEXT DEFAULT '货到付款'"),
        ('suppliers', 'created_at', "ALTER TABLE suppliers ADD COLUMN created_at TEXT DEFAULT ''"),
        ('deliveries', 'contract_no', "ALTER TABLE deliveries ADD COLUMN contract_no TEXT DEFAULT ''"),
        ('receivings', 'contract_id', "ALTER TABLE receivings ADD COLUMN contract_id INTEGER"),
        ('receivings', 'contract_no', "ALTER TABLE receivings ADD COLUMN contract_no TEXT DEFAULT ''"),
        ('invoices', 'order_ids', "ALTER TABLE invoices ADD COLUMN order_ids TEXT DEFAULT ''"),
        ('invoices', 'contract_id', "ALTER TABLE invoices ADD COLUMN contract_id INTEGER"),
        ('invoices', 'contract_no', "ALTER TABLE invoices ADD COLUMN contract_no TEXT DEFAULT ''"),
        ('payment_requests', 'trade_mode', "ALTER TABLE payment_requests ADD COLUMN trade_mode TEXT DEFAULT '货到付款'"),
        ('payment_requests', 'settlement_id', "ALTER TABLE payment_requests ADD COLUMN settlement_id INTEGER"),
        ('credit_notes', 'contract_no', "ALTER TABLE credit_notes ADD COLUMN contract_no TEXT DEFAULT ''"),
        ('purchase_requests', 'rejected_reason', "ALTER TABLE purchase_requests ADD COLUMN rejected_reason TEXT DEFAULT ''"),
        ('purchase_requests', 'apply_date', "ALTER TABLE purchase_requests ADD COLUMN apply_date TEXT DEFAULT ''"),
        ('contracts', 'updated_at', "ALTER TABLE contracts ADD COLUMN updated_at TEXT"),
        ('receivings', 'updated_at', "ALTER TABLE receivings ADD COLUMN updated_at TEXT"),
        ('requisitions', 'issued_at', "ALTER TABLE requisitions ADD COLUMN issued_at TEXT"),
        ('request_items', 'category', "ALTER TABLE request_items ADD COLUMN category TEXT DEFAULT ''"),
        ('request_items', 'brand_param', "ALTER TABLE request_items ADD COLUMN brand_param TEXT DEFAULT ''"),
        ('request_items', 'arrival_date', "ALTER TABLE request_items ADD COLUMN arrival_date TEXT DEFAULT ''"),
    ]:
        _cols = [r[1] for r in conn.execute(f"PRAGMA table_info({_tbl})").fetchall()]
        if _col not in _cols:
            conn.execute(_ddl)
    # ---- V5.1 安全加固: 登录审计/系统元数据(幂等) ----
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, ip TEXT,
            success INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            item_name TEXT, spec TEXT, unit TEXT DEFAULT '个',
            quantity REAL DEFAULT 0, price REAL DEFAULT 0, amount REAL DEFAULT 0,
            tax_rate REAL DEFAULT 13, tax_amount REAL DEFAULT 0, total_amount REAL DEFAULT 0,
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT, expense_no TEXT UNIQUE NOT NULL,
            expense_date TEXT, category TEXT, amount REAL DEFAULT 0, reason TEXT,
            supplier TEXT, invoice_no TEXT, remark TEXT, created_by TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT, settlement_no TEXT UNIQUE NOT NULL,
            period TEXT, supplier TEXT, contract_ids TEXT, order_ids TEXT,
            total_amount REAL DEFAULT 0, status TEXT DEFAULT '待确认',
            remark TEXT, created_at TEXT DEFAULT (datetime('now','localtime')), confirmed_at TEXT);
        -- V55 预警中心: 预警记录(首页集中展示/标记已处理/日志留存)
        CREATE TABLE IF NOT EXISTS alert_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, alert_type TEXT, level TEXT DEFAULT 'orange',
            title TEXT, content TEXT, biz_type TEXT, biz_id INTEGER,
            status TEXT DEFAULT 'pending', created_at TEXT DEFAULT (datetime('now','localtime')),
            processed_at TEXT, processed_by TEXT);
    """)
    conn.commit(); conn.close()

# ============================================================
# ── AUTH API ──
# ============================================================

def search_brand_info(supplier_name, category):
    """V11.91: 简单品牌优缺点分析（本地数据，不调用AI）"""
    # 行业常见品牌知识
    brand_knowledge = {
        '长城': {'优点': '国产老牌，性价比高', '缺点': '精度一般'},
        '恒力': {'优点': '民营石化龙头，品质稳定', '缺点': '价格略高'},
        '中石化': {'优点': '央企，质量可靠', '缺点': '交货周期长'},
        '宝钢': {'优点': '国产钢材龙头', '缺点': '价格偏高'},
        '鞍钢': {'优点': '北方钢厂，性价比高', '缺点': '运输距离远'},
        '柳工': {'优点': '国产工程机械龙头', '缺点': '二手保值率一般'},
        '徐工': {'优点': '规模大，服务网点多', '缺点': '价格中等'},
        '三一': {'优点': ' innovation强', '缺点': '售后需预约'},
        '施耐德': {'优点': '国际品牌，质量稳定', '缺点': '价格高'},
        '西门子': {'优点': '德系品质，可靠性高', '缺点': '价格昂贵'},
        'ABB': {'优点': '电力领域领先', '缺点': '交期长'},
        '海尔': {'优点': '国产家电龙头', '缺点': '工业品线弱'},
        '美的': {'优点': '性价比高', '缺点': '高端线弱'},
        '格力': {'优点': '空调技术强', '缺点': '品类单一'},
        '华为': {'优点': '技术领先', '缺点': '价格高，供货紧'},
        '中兴': {'优点': '性价比高', '缺点': '品牌力弱'},
        '联想': {'优点': '国内PC龙头', '缺点': '高端线弱'},
        '戴尔': {'优点': '商务稳定', '缺点': '价格高'},
        '惠普': {'优点': '外设强', '缺点': 'PC线一般'},
        '金立': {'优点': '备用机', '缺点': '已退市'},
    }
    # 匹配供应商名称
    for brand, info in brand_knowledge.items():
        if brand in supplier_name or supplier_name in brand:
            return info
    # 按行业类别推荐
    if category:
        if '钢材' in category or '建材' in category:
            return {'优点': '本地供应', '缺点': '需验厂'}
        if '仪表' in category or '阀门' in category:
            return {'优点': '专业厂家', '缺点': '交期1-2周'}
    return {'优点': '', '缺点': ''}

@app.route('/api/login', methods=['POST'])
def api_login():
    d = request.json or {}
    username = (d.get('username') or '').strip()
    ip = get_client_ip()
    conn = db()
    # 失败锁定: 15分钟内同一账号+IP失败>=5次则锁定
    if login_fail_count(conn, username, ip) >= MAX_FAILS:
        conn.close()
        log('系统', '登录锁定', '账号 %s (IP %s) 触发失败锁定' % (username, ip))
        return jsonify({'error': '登录失败次数过多，账号已临时锁定%d分钟，请稍后再试' % LOCK_MINUTES}), 429
    u = conn.execute("SELECT u.*,d.name as dept_name FROM users u LEFT JOIN departments d ON u.dept_id=d.id WHERE u.username=? AND u.is_active=1", (username,)).fetchone()
    if not u or not verify_password(d.get('password',''), u['password']):
        conn.execute("INSERT INTO login_attempts(username,ip,success) VALUES(?,?,0)", (username, ip))
        conn.commit()
        fails = login_fail_count(conn, username, ip)
        conn.close()
        left = MAX_FAILS - fails
        msg = '用户名或密码错误'
        if left > 0: msg += '（还有%d次机会，连续失败%d次将锁定%d分钟）' % (left, MAX_FAILS, LOCK_MINUTES)
        else: msg += '（已锁定%d分钟）' % LOCK_MINUTES
        log('系统', '登录失败', '账号 %s (IP %s) 密码错误' % (username, ip))
        return jsonify({'error': msg}), 401
    # 旧MD5密码自动升级为PBKDF2(一次登录即完成迁移)
    if is_legacy_md5(u['password']):
        conn.execute("UPDATE users SET password=? WHERE id=?", (hash_password(d.get('password','')), u['id']))
    conn.execute("DELETE FROM login_attempts WHERE username=? AND ip=?", (username, ip))
    conn.execute("INSERT INTO login_attempts(username,ip,success) VALUES(?,?,1)", (username, ip))
    conn.commit(); conn.close()
    # 会话固定防护: 登录成功后重建会话
    session.clear()
    session['user_id'] = u['id']; session['username'] = u['username']; session['user_name'] = u['name']; session['user_role'] = u['role']
    session['dept_id'] = u['dept_id']; session['dept_name'] = u['dept_name'] or ''
    session['login_time'] = now()
    session.permanent = True  # V8.0: 持久会话 — 刷新保持登录, 8小时不操作才登出
    log('系统', '登录成功', '%s (%s) 登录系统' % (u['name'], u['role']))
    return jsonify({'success':True, 'user':{'id':u['id'],'username':u['username'],'name':u['name'],'role':u['role'],'dept_name':u['dept_name'] or '','can_config': can_manage_config()}})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    log('系统', '退出登录', '%s 退出系统' % session.get('user_name',''))
    session.clear(); return jsonify({'success':True})

@app.route('/api/me')
@login_required
def api_me():
    return jsonify({'id':session['user_id'],'name':session['user_name'],'role':session['user_role'],'dept_id':session['dept_id'],'dept_name':session['dept_name'],'can_config': can_manage_config()})

@app.route('/api/users')
@login_required
def api_users():
    # V11.64: 用户列表仅 管理员/领导/财务 可见(防账号信息泄露)
    if session.get('user_role') not in ('系统管理员', '分管领导', '总经理', '财务'):
        return jsonify([])
    conn = db(); rows = conn.execute("SELECT u.*,d.name as dept_name FROM users u LEFT JOIN departments d ON u.dept_id=d.id ORDER BY u.id").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/users', methods=['POST'])
@admin_required
def api_users_create():
    d = request.json or {}
    username = (d.get('username') or '').strip()
    name = (d.get('name') or '').strip()
    role = d.get('role') or '员工'
    if not username or not name:
        return jsonify({'error': '用户名和姓名必填'}), 400
    pw = d.get('password') or '123456'
    if len(pw) < 6:
        return jsonify({'error': '初始密码长度至少6位'}), 400
    conn = db()
    if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        conn.close(); return jsonify({'error': '用户名已存在'}), 400
    conn.execute("INSERT INTO users(username,password,name,phone,role,dept_id,title,is_active) VALUES(?,?,?,?,?,?,?,1)",
                 (username, hash_password(pw), name, d.get('phone',''), role, d.get('dept_id'), d.get('title','')))
    conn.commit(); conn.close()
    log(session.get('user_name',''), '新增用户', '创建账号 %s (%s, %s)' % (username, name, role))
    return jsonify({'success': True, 'message': '用户已创建, 初始密码: %s' % pw})

@app.route('/api/users/<int:uid>/reset-password', methods=['POST'])
@admin_required
def api_reset_password(uid):
    d = request.json or {}
    new_pw = d.get('new_password') or ''
    if len(new_pw) < 6:
        return jsonify({'error': '新密码长度至少6位'}), 400
    conn = db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        conn.close(); return jsonify({'error': '用户不存在'}), 404
    conn.execute("UPDATE users SET password=? WHERE id=?", (hash_password(new_pw), uid))
    conn.commit(); conn.close()
    log(session.get('user_name',''), '重置密码', '管理员重置用户 %s 的密码' % u['name'])
    return jsonify({'success': True, 'message': '密码已重置'})

@app.route('/api/change-password', methods=['POST'])
@login_required
def api_change_password():
    d = request.json or {}
    new_pw = d.get('new_password') or ''
    if len(new_pw) < 6:
        return jsonify({'error': '新密码长度至少6位'}), 400
    if new_pw == d.get('old_password'):
        return jsonify({'error': '新密码不能与旧密码相同'}), 400
    conn = db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    if not u or not verify_password(d.get('old_password',''), u['password']):
        conn.close(); return jsonify({'error': '旧密码不正确'}), 400
    conn.execute("UPDATE users SET password=? WHERE id=?", (hash_password(new_pw), u['id']))
    conn.commit(); conn.close()
    log(session.get('user_name',''), '修改密码', '用户 %s 修改了登录密码' % u['name'])
    return jsonify({'success': True, 'message': '密码已更新'})

@app.route('/api/departments')
@login_required
def api_departments():
    conn = db(); rows = conn.execute("SELECT * FROM departments").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

# ============================================================
# ── APPROVAL FLOW HELPER ──
# ============================================================
def get_approval_config(biz_type, amount):
    conn = db()
    rows = conn.execute("SELECT * FROM approval_flow_config WHERE biz_type=? AND ?>=min_amount AND ?<=max_amount ORDER BY level_no", (biz_type, amount, amount)).fetchall()
    conn.close()
    return rows

def create_approvals(biz_type, biz_id, amount):
    """V5.0: 按审批流配置生成审批实例
    - 节点配置了具体审批人(approver=用户名) → 绑定该用户
    - 未配置(留空) → 按角色在 users 表找有效用户
    支持每个环节独立配置/更换审批负责人, 页面可视化维护, 无需改代码"""
    configs = get_approval_config(biz_type, amount)
    conn = db()
    for cfg in configs:
        approver_name = ''
        approver_id = None
        if cfg['approver'] and str(cfg['approver']).strip():
            u = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (str(cfg['approver']).strip(),)).fetchone()
            if u:
                approver_name = u['name'] or u['username']
                approver_id = u['id']
        if approver_id is None:
            # 按角色自动找有效用户
            u2 = conn.execute("SELECT * FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1", (cfg['role'],)).fetchone()
            if u2:
                approver_name = u2['name'] or u2['username']
                approver_id = u2['id']
        conn.execute("INSERT INTO approval_instances(biz_type,biz_id,level_no,role,approver,approver_id) VALUES(?,?,?,?,?,?)",
                     (biz_type, biz_id, cfg['level_no'], cfg['role'], approver_name, approver_id))
    conn.commit(); conn.close()

def check_all_approved(biz_type, biz_id):
    conn = db()
    r = conn.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending'", (biz_type, biz_id)).fetchone()[0]
    conn.close()
    return r == 0

def do_approve(biz_type, biz_id, approver, approver_id, action='approved', comment='', signature=''):
    """V7.0: 审批操作权限锁死 — 仅当前节点指定审批人/系统管理员可同意驳回
    - 当前节点审批人 = 审批实例 approver(节点配置指定的人) 或 角色对应有效用户
    - 其余用户(含其他角色领导)一律拒绝; 钉钉端权限与系统完全同步
    - V11.2: signature 电子签名(dataURL图片)随审批记录保存"""
    conn = db()
    cur = conn.execute("SELECT * FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no LIMIT 1", (biz_type, biz_id)).fetchone()
    if not cur:
        conn.close(); return {'success':False,'error':'无待审批节点'}
    # ── 权限校验(7.0优化2): 仅当前节点指定审批人/系统管理员 ──
    me = conn.execute("SELECT * FROM users WHERE id=?", (approver_id,)).fetchone() if approver_id else None
    is_admin = me and me['role'] == '系统管理员'
    node_approver_id = cur['approver_id'] if cur['approver_id'] else None
    if not node_approver_id:
        # 审批实例未绑定具体人(按角色) → 解析角色对应有效用户
        u = conn.execute("SELECT * FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1", (cur['role'],)).fetchone()
        node_approver_id = u['id'] if u else None
    if not is_admin and (not me or me['id'] != node_approver_id):
        conn.close()
        return {'success':False,'error':'仅当前审批节点指定审批人可操作'}
    # 签名限制: 图片dataURL太长(可达几十KB), 截断保护
    sig = (signature or '').strip()
    if sig and not sig.startswith('data:image/'):
        sig = ''
    if len(sig) > 200000:
        sig = sig[:200000]
    conn.execute("UPDATE approval_instances SET status=?, approver=?, approver_id=?, comment=?, processed_at=?, signature=? WHERE id=?",
                 (action, approver, approver_id, comment, now(), sig, cur['id']))
    conn.commit(); conn.close()
    return {'success':True}

# ============================================================
# V4.1 ── 飞书对接: 审批同步 + 机器人预警 (零依赖, urllib)
# ============================================================
FS_API = 'https://open.feishu.cn/open-apis'
FS_BIZ = {  # biz_type -> (审批定义名称, 表单控件前缀)
    'purchase_request': ('采购申请审批', 'SQ'),
    'purchase_order':   ('采购订单审批', 'CG'),
    'contract':         ('合同审批', 'HT'),
    'credit':           ('挂账审批', 'GZ'),
    'payment':          ('付款审批', 'FK'),
    'receiving':        ('入库审批', 'RK'),
    'requisition':      ('出库审批', 'CK'),
}
FS_PRE = {'purchase_request': 'SQ', 'purchase_order': 'CG', 'contract': 'HT', 'credit': 'GZ', 'payment': 'FK', 'receiving': 'RK', 'requisition': 'CK'}
FS_NODE_MAX = 3  # 审批定义中的审批节点数(与系统链路最大级数一致)

def cfg_get(key, default=''):
    c = db()
    try: r = c.execute("SELECT value FROM sys_config WHERE key=?", (key,)).fetchone()
    except Exception: r = None
    c.close()
    return r['value'] if r else default

def cfg_set(key, value):
    c = db()
    c.execute("INSERT INTO sys_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    c.commit(); c.close()

def feishu_enabled():
    return cfg_get('feishu_enabled') == '1'

_TOKEN = {'t': None, 'exp': 0}
def fs_token():
    if _TOKEN['t'] and time.time() < _TOKEN['exp'] - 120: return _TOKEN['t']
    app_id = cfg_get('feishu_app_id'); app_secret = cfg_get('feishu_app_secret')
    if not app_id or not app_secret: raise RuntimeError('飞书应用未配置: 请先在"飞书设置"填写 app_id / app_secret')
    code, resp = fs_post('/auth/v3/tenant_access_token/internal', {'app_id': app_id, 'app_secret': app_secret}, auth=False)
    if code != 0: raise RuntimeError(f'获取tenant_access_token失败(code={code}): {resp.get("msg","")}')
    _TOKEN['t'] = resp.get('tenant_access_token'); _TOKEN['exp'] = time.time() + int(resp.get('expire', 7200))
    return _TOKEN['t']

def fs_post(path, payload, auth=True, timeout=12):
    body = json.dumps(payload).encode('utf-8')
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if auth:
        try: headers['Authorization'] = 'Bearer ' + fs_token()
        except Exception as e: return -1, {'msg': str(e)}
    req = urllib.request.Request(FS_API + path, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return d.get('code', -1), d
    except Exception as e:
        return -1, {'msg': f'网络错误: {e}'}

def fs_get(path, timeout=12):
    headers = {}
    try: headers['Authorization'] = 'Bearer ' + fs_token()
    except Exception as e: return -1, {'msg': str(e)}
    req = urllib.request.Request(FS_API + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return d.get('code', 0), d
    except Exception as e:
        return -1, {'msg': f'网络错误: {e}'}

def find_user_by_role(role):
    """审批角色 → 具体用户(严格校验): 必须 is_active=1 有效用户
    V5.0+: 不再静默回退 admin 顶替 — 角色无有效用户时返回 None, 由调用方决定发起失败"""
    c = db()
    u = c.execute("SELECT * FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1", (role,)).fetchone()
    c.close()
    return u

def find_approver_for_role(role, channel='dingtalk'):
    """钉钉/飞书审批发起时的审批人解析(严格): 有效用户 + 已绑定对应通道ID
    返回 None 表示该角色无满足条件的审批人(需在通讯录/系统配置中补齐)"""
    c = db()
    if channel == 'dingtalk':
        u = c.execute("SELECT * FROM users WHERE role=? AND is_active=1 AND dingtalk_userid IS NOT NULL AND dingtalk_userid!='' ORDER BY id LIMIT 1", (role,)).fetchone()
    else:
        u = c.execute("SELECT * FROM users WHERE role=? AND is_active=1 AND feishu_open_id IS NOT NULL AND feishu_open_id!='' ORDER BY id LIMIT 1", (role,)).fetchone()
    c.close()
    return u

def find_user_by_name(name):
    c = db()
    u = c.execute("SELECT * FROM users WHERE name=? AND is_active=1 ORDER BY id LIMIT 1", (name,)).fetchone()
    c.close()
    return u

def conn_check_user(username):
    """审批流配置时校验: 指定审批人用户名存在且为有效用户; 返回 users Row 或 None"""
    conn = db()
    u = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
    conn.close()
    return u

def admin_open_id():
    c = db()
    u = c.execute("SELECT * FROM users WHERE feishu_open_id IS NOT NULL AND feishu_open_id!='' ORDER BY id LIMIT 1").fetchone()
    c.close()
    return u['feishu_open_id'] if u else ''

def fs_approval_codes():
    try: return json.loads(cfg_get('feishu_approval_codes', '{}'))
    except Exception: return {}

def fs_approval_code(biz_type):
    return fs_approval_codes().get(biz_type, '')

# ---- 审批定义: 一键创建(幂等) ----
def fs_form_controls(biz):
    pre = FS_PRE[biz]
    return [
        {'id': pre+'_no', 'label': {'text': '单据编号'}, 'type': 'input', 'required': True, 'placeholder': '如 SQ-202608-0001'},
        {'id': pre+'_name', 'label': {'text': '内容摘要'}, 'type': 'input', 'required': True},
        {'id': pre+'_amount', 'label': {'text': '金额(元)'}, 'type': 'number', 'required': True},
        {'id': pre+'_applicant', 'label': {'text': '申请人'}, 'type': 'input', 'required': True},
        {'id': pre+'_date', 'label': {'text': '提交时间'}, 'type': 'input', 'required': False},
    ]

def fs_init_definitions():
    """调用飞书API创建5个审批定义(幂等); 若某定义已存在则沿用其代码"""
    ao = admin_open_id()
    if not ao: return {'success': False, 'error': '请先在②用户飞书绑定中绑定至少一个用户的 open_id'}
    out = {}
    for biz, (title, pre) in FS_BIZ.items():
        ac = 'zhengcheng_' + biz
        code_r, resp = fs_post('/approval/v4/approvals/create', {
            'approval_name': title,
            'approval_code': ac,
            'description': '正成能源智慧采购系统 - ' + title,
            'form': {'form_controls': fs_form_controls(biz)},
            'node_list': [
                {'id': 'node_1', 'name': '审批节点1', 'type': 'APPROVER', 'approver': {'type': 'FIXED', 'ids': [ao]}},
                {'id': 'node_2', 'name': '审批节点2', 'type': 'APPROVER', 'approver': {'type': 'FIXED', 'ids': [ao]}},
                {'id': 'node_3', 'name': '审批节点3', 'type': 'APPROVER', 'approver': {'type': 'FIXED', 'ids': [ao]}},
            ],
        })
        if code_r == 0:
            got = resp.get('data', {}).get('approval', {}).get('approval_code') or ac
            out[biz] = got
        elif '重复' in str(resp.get('msg', '')) or code_r in (1060012, 1060013, 1060014):
            out[biz] = ac  # 已存在, 沿用
        else:
            out[biz] = f'ERROR:{resp.get("msg","")}'
    codes = {k: v for k, v in out.items() if not str(v).startswith('ERROR')}
    if codes: cfg_set('feishu_approval_codes', json.dumps(codes, ensure_ascii=False))
    return {'success': True, 'results': out}

# ---- 单据信息 -> 飞书审批表单 ----
def fs_biz_info(biz_type, biz_id):
    c = db()
    r = None
    if biz_type == 'purchase_request':
        r = c.execute("SELECT * FROM purchase_requests WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['req_no'], r['purpose'] or r['req_no'], r['total_estimated'], r['requester'] or '系统', r['created_at'])
    if biz_type == 'purchase_order':
        r = c.execute("SELECT * FROM purchase_orders WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['order_no'], f"{r['item_name']} {r['spec'] or ''}".strip(), r['total_amount'], r['owner'] or '系统', r['created_at'])
    if biz_type == 'contract':
        r = c.execute("SELECT * FROM contracts WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['contract_no'], r['contract_name'] or r['contract_no'], r['amount'], '系统', r['created_at'])
    if biz_type == 'credit':
        r = c.execute("SELECT * FROM credit_notes WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['credit_no'], r['item_name'] or r['credit_no'], r['amount'], '系统', r['created_at'])
    if biz_type == 'payment':
        r = c.execute("SELECT * FROM payment_requests WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['payment_no'], f"{r['payment_type']} {r['supplier'] or ''}".strip(), r['amount'], '系统', r['created_at'])
    if biz_type == 'inquiry_approval':
        r = c.execute("SELECT * FROM inquiries WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['inq_no'], r['title'] or r['inq_no'], 0, r['created_by'] or '', '')
    r = c.execute("SELECT * FROM receivings WHERE id=?", (biz_id,)).fetchone()
    c.close()
    if not r: return None
    return (r['receive_no'], r['item_name'] or r['receive_no'], r['quantity'] or 0, r['inspector'] or '系统', r['created_at'])
    if biz_type == 'inquiry_approval':
        r = c.execute("SELECT * FROM inquiries WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['inq_no'], r['title'] or r['inq_no'], 0, r['created_by'] or '', '')
        r = c.execute("SELECT * FROM receivings WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['receive_no'], r['item_name'] or r['receive_no'], r['quantity'] or 0, r['inspector'] or '系统', r['created_at'])
    if biz_type == 'requisition':
        r = c.execute("SELECT * FROM requisitions WHERE id=?", (biz_id,)).fetchone()
        c.close()
        if not r: return None
        return (r['req_no'], r['item_name'] or r['req_no'], r['quantity'] or 0, r['requester'] or '系统', r['created_at'])
    c.close(); return None

def fs_start_instance(biz_type, biz_id):
    """单据进入待审批后, 同步发起飞书审批实例; 成功返回instance_code, 失败返回None"""
    try:
        if not feishu_enabled(): return None
        ac = fs_approval_code(biz_type)
        if not ac: return None
        c = db()
        if c.execute("SELECT COUNT(*) FROM feishu_instances WHERE biz_type=? AND biz_id=? AND status NOT IN ('error','cancelled')", (biz_type, biz_id)).fetchone()[0] > 0:
            c.close(); return None
        levels = c.execute("SELECT DISTINCT role FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no", (biz_type, biz_id)).fetchall()
        c.close()
        if not levels: return None
        approvers, missing = [], []
        for lv in levels:
            # V5.0+: 严格校验 — 审批人必须是 有效用户(is_active=1) 且已绑定飞书通讯录成员ID
            u = find_approver_for_role(lv['role'], 'feishu')
            if u and u['feishu_open_id']: approvers.append(u['feishu_open_id'])
            else: missing.append(lv['role'])
        if missing:
            log('系统', '飞书审批未发起', f"{biz_type}#{biz_id} 以下角色无有效审批人(未启用或未绑定飞书通讯录): {','.join(missing)}")
            return None
        info = fs_biz_info(biz_type, biz_id)
        if not info: return None
        pre = FS_PRE[biz_type]
        form = [
            {'id': pre+'_no', 'value': str(info[0])},
            {'id': pre+'_name', 'value': str(info[1])},
            {'id': pre+'_amount', 'value': str(info[2])},
            {'id': pre+'_applicant', 'value': str(info[3])},
            {'id': pre+'_date', 'value': str(info[4] or '')[:16]},
        ]
        initiator = admin_open_id()
        ua = find_user_by_name(str(info[3]))
        if ua and ua['feishu_open_id']: initiator = ua['feishu_open_id']
        if not initiator: initiator = approvers[0]
        node_list = [[o] for o in approvers]
        payload = {
            'approval_code': ac, 'open_id': initiator, 'form': form,
            'node_approver_openid_list': node_list,
            'uuid': f'zc-{biz_type}-{biz_id}',
        }
        code_r, resp = fs_post('/approval/v4/instances', payload)
        if code_r != 0 and len(node_list) < FS_NODE_MAX:
            # 节点数与定义不一致时, 补齐到定义节点数(多余节点归最后审批人)重试
            node_list = node_list + [node_list[-1]] * (FS_NODE_MAX - len(node_list))
            payload['node_approver_openid_list'] = node_list
            code_r, resp = fs_post('/approval/v4/instances', payload)
        if code_r == 0:
            inst = resp.get('data', {}).get('instance_code', '')
            c = db()
            c.execute("INSERT INTO feishu_instances(instance_code,biz_type,biz_id,status) VALUES(?,?,?,'pending')", (inst, biz_type, biz_id))
            c.commit(); c.close()
            log('系统', '发起飞书审批', f"{info[0]} 审批人{len(approvers)}级")
            fs_send(approvers[0], f"📋 新的{FS_BIZ[biz_type][0]}待处理\n{info[0]} {info[1]}\n金额 **¥{float(info[2] or 0):,.0f}**\n请前往飞书【审批】应用处理", 'blue')
            return inst
        c = db()
        c.execute("INSERT INTO feishu_instances(instance_code,biz_type,biz_id,status,error) VALUES(?,?,?,'error',?)",
                  (f'ERR-{biz_type}-{biz_id}-{int(time.time())}', biz_type, biz_id, json.dumps(resp, ensure_ascii=False)[:500]))
        c.commit(); c.close()
        log('系统', '飞书审批发起失败', f"{biz_type}#{biz_id}: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return None
    except Exception as e:
        log('系统', '飞书审批发起异常', f"{biz_type}#{biz_id}: {e}")
        return None

# ---- 机器人消息 ----
def fs_send(target, text, color='blue', id_type='open_id'):
    try:
        card = {
            'config': {'wide_screen_mode': True},
            'header': {'template': color, 'title': {'tag': 'plain_text', 'content': '采购系统提醒'}},
            'elements': [
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': text}},
                {'tag': 'hr'},
                {'tag': 'note', 'elements': [{'tag': 'plain_text', 'content': '正成能源智慧采购系统'}]},
            ],
        }
        code_r, resp = fs_post(f'/im/v1/messages?receive_id_type={id_type}', {
            'receive_id': target, 'msg_type': 'interactive', 'content': json.dumps(card, ensure_ascii=False),
        })
        return code_r == 0
    except Exception:
        return False

# ---- 审批结果同步(幂等) ----
def biz_parent_status(biz_type, result):
    m = {
        'purchase_request': ('已通过', '已驳回'), 'purchase_order': ('审批通过', '已驳回'),
        'contract': ('执行中', '已驳回'), 'credit': ('已通过', '已驳回'), 'payment': ('已通过', '已驳回'),
        'receiving': ('已入库', '已驳回'), 'requisition': ('已出库', '已驳回'),
    }
    ok, no = m.get(biz_type, ('已通过', '已驳回'))
    return ok if result == 'ok' else no

def biz_table(biz_type):
    return {'purchase_request': 'purchase_requests', 'purchase_order': 'purchase_orders',
            'contract': 'contracts', 'credit': 'credit_notes', 'payment': 'payment_requests',
            'receiving': 'receivings', 'requisition': 'requisitions'}[biz_type]

# ============================================================
# V11.64: 数据级权限 — 按角色过滤列表数据(前端隐藏菜单+后端过滤数据, 双保险)
# 规则: 管理员/领导全看; 采购员看采购相关; 库管员看库房相关; 财务看单据+财务
# ============================================================
def can_see_all():
    """管理员/领导 可看全部数据"""
    return session.get('user_role') in ('系统管理员', '分管领导', '总经理')

def filter_scope(role):
    """返回角色可看的业务域: full=全部 / buy=采购 / stock=库房 / fin=财务 / own=仅自己的"""
    if role in ('系统管理员', '分管领导', '总经理'):
        return 'full'
    if role == '采购员':
        return 'buy'
    if role == '库管员':
        return 'stock'
    if role == '财务':
        return 'fin'
    return 'own'  # 员工/部门负责人

def can_see_price():
    """价格可见: 管理员/领导/财务/采购员(谈价需要), 库管员/员工/部门负责人不可见"""
    return session.get('user_role') in ('系统管理员', '分管领导', '总经理', '财务', '采购员')

def mask_price(d):
    """价格脱敏: 无权限的角色, 金额字段置 None(前端显示 ***)"""
    for k in ('total_estimated', 'total_amount', 'price', 'amount', 'est_amount', 'tax_amount', 'quote_price', 'estimated_price'):
        if k in d and d[k] is not None:
            d[k] = None
    return d

def _scope_where(alias):
    """按角色返回 SQL WHERE 片段(过滤数据范围):
    own=只自己的(requester_id=当前用户) / 其余返回空(不过滤, 由调用方决定)"""
    role = session.get('user_role')
    if role in ('员工',):
        return f"{alias}requester_id={session.get('user_id', 0)}"
    if role == '部门负责人':
        return f"{alias}requester_id={session.get('user_id', 0)}"  # 部门负责人仅看自己(暂简化, 后续按部门)
    return ''  # 管理员/领导/采购/库管/财务 由各接口决定(业务域不同)

def finish_approvals(biz_type, biz_id, result='ok', approver='飞书', approver_id=0, comment='飞书审批同步'):
    """result: 'ok'=通过, 'reject'=驳回; 同步更新审批节点/父单据/飞书实例状态 (幂等)
    场景覆盖: ①系统内逐级审批(每过一级pending减1, 全过才置父状态)
    ②飞书回调(回调时节点全pending, 一次性全部通过) ③驳回(全部pending置rejected)"""
    c = db()
    pending = c.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending'", (biz_type, biz_id)).fetchone()[0]
    approved = c.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='approved'", (biz_type, biz_id)).fetchone()[0]
    if result == 'ok':
        if pending > 0:
            if approved == 0:
                # 飞书回调场景: 节点全pending但审批已全过 → 一次性全部置approved
                c.execute("UPDATE approval_instances SET status='approved', approver=?, approver_id=?, comment=?, processed_at=? WHERE biz_type=? AND biz_id=? AND status='pending'",
                          (approver, approver_id, comment, now(), biz_type, biz_id))
            else:
                c.close(); return False  # 系统内逐级审批中, 还有待审节点, 不改父状态
        st = biz_parent_status(biz_type, 'ok')
    else:
        c.execute("UPDATE approval_instances SET status='rejected', approver=?, approver_id=?, comment=?, processed_at=? WHERE biz_type=? AND biz_id=? AND status='pending'",
                  (approver, approver_id, comment, now(), biz_type, biz_id))
        st = biz_parent_status(biz_type, 'reject')
    c.execute(f"UPDATE {biz_table(biz_type)} SET status=?, updated_at=? WHERE id=?", (st, now(), biz_id))
    # V11.67: 询价定标审批通过 → 同步询价单状态(定标审批中→已生成订单); 驳回→恢复询价中
    if biz_type == 'purchase_order':
        if result == 'ok' and st in ('已通过', '审批通过'):
            c.execute("UPDATE inquiries SET status='已生成订单', updated_at=? WHERE id IN (SELECT i.id FROM inquiries i JOIN inquiry_suppliers s ON s.inquiry_id=i.id WHERE s.id=(SELECT selected_supplier_id FROM purchase_orders WHERE id=?))", (now(), biz_id))
    # V11.76: 询价审批通过 → 根据选定供应商创建订单并生效    elif biz_type == 'inquiry_approval':        if result == 'ok' and st in ('已通过', '审批通过'):            try:                inst = c.execute("SELECT * FROM dingtalk_instances WHERE biz_type=? AND biz_id=? ORDER BY id DESC LIMIT 1", (biz_type, biz_id)).fetchone()                if inst:                    form_vals = inst.get('form_values', '') or '{}'                    fv = json.loads(form_vals)                    selected_id = None                    for item in (fv if isinstance(fv, list) else []):                        if isinstance(item, dict) and item.get('name') == '选定供应商':                            vals = item.get('value', '[]')                            if isinstance(vals, str):                                try:                                    vals = json.loads(vals)                                except:                                    pass                            if isinstance(vals, list) and len(vals) > 0:                                selected_id = int(vals[0])                            elif isinstance(vals, str):                                selected_id = int(vals)                            break                    if selected_id:                        c.execute("UPDATE inquiry_approvals SET selected_supplier_id=?, status='已批准', updated_at=? WHERE id=?", (selected_id, now(), biz_id))                        iq = c.execute("SELECT * FROM inquiries WHERE id=?", (biz_id,)).fetchone()                        if iq:                            pr = c.execute("SELECT * FROM purchase_requests WHERE id=?", (iq['req_id'],)).fetchone()                            items = c.execute("SELECT * FROM request_items WHERE req_id=?", (iq['req_id'],)).fetchall()                            if pr and items:                                sup = c.execute("SELECT * FROM inquiry_suppliers WHERE id=?", (selected_id,)).fetchone()                                if sup:                                    no = gen_no('CG', 'purchase_orders', 'order_no', c)                                    total = float(sup['quote_price'] or 0)                                    remark = '询价单:%s 供应商:%s 报价¥%.0f' % (iq['inq_no'], sup['supplier_name'], total)                                    c.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",                                        (no, iq['req_id'], iq['title'][:50], '', 1, '个', 0, total, 0, 0, total, sup['supplier_name'], iq['created_by'], '后勤类', iq['created_by'], 1, iq['deadline'] or '', '货到付款', remark, 0, json.dumps([], ensure_ascii=False), '已通过'))                                    oid = c.execute("SELECT id FROM purchase_orders WHERE order_no=?", (no,)).fetchone()[0]                                    for idx, it in enumerate(items):                                        qty = float(it['quantity'] or 1)                                        c.execute("INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,amount,tax_rate,tax_amount,total_amount,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",                                            (oid, it['item_name'], it['spec'] or '', it['unit'] or '个', qty, 0, total, 0, 0, total, ''))                                    c.execute("UPDATE inquiries SET status='已生成订单', selected_supplier_id=?, updated_at=? WHERE id=?", (selected_id, now(), biz_id))                                    c.execute("UPDATE inquiry_approvals SET order_id=?, status='已完成' WHERE id=?", (oid, biz_id))                                    log('系统', '询价审批生效', '%s → 订单%s 已生效' % (iq['inq_no'], no))                c.commit()            except Exception as e:                log('系统', '询价审批处理异常', str(e))
    if result == 'ok':
        if biz_type == 'receiving' and st == '已入库':
            do_receiving_stock(c, biz_id)
        elif biz_type == 'requisition' and st == '已出库':
            do_requisition_stock(c, biz_id)
    # V7.0: 全节点自动推送 — 单据流转到下一级时, 向下一级审批人钉钉推送(待办+通知)
    try:
        if result == 'ok':
            nxt = c.execute("SELECT * FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no LIMIT 1", (biz_type, biz_id)).fetchone()
            if nxt:
                u = None
                if nxt['approver_id']:
                    u = c.execute("SELECT * FROM users WHERE id=?", (nxt['approver_id'],)).fetchone()
                if not u:
                    u = c.execute("SELECT * FROM users WHERE role=? AND is_active=1 AND dingtalk_userid IS NOT NULL AND dingtalk_userid!='' ORDER BY id LIMIT 1", (nxt['role'],)).fetchone()
                if u and u['dingtalk_userid']:
                    no = c.execute(f"SELECT * FROM {biz_table(biz_type)} WHERE id=?", (biz_id,)).fetchone()
                    doc_no = no['req_no'] if 'req_no' in no.keys() else (no['order_no'] if 'order_no' in no.keys() else (no['contract_no'] if 'contract_no' in no.keys() else (no['receive_no'] if 'receive_no' in no.keys() else (no['payment_no'] if 'payment_no' in no.keys() else ''))))
                    c.execute("INSERT INTO dingtalk_push_log(biz_type,biz_id,doc_no,push_type,target_user,target_userid,operator,content) VALUES(?,?,?,?,?,?,?,?)",
                              (biz_type, str(biz_id), doc_no, 'auto', u['name'] or u['username'], u['dingtalk_userid'],
                               approver, f"下一节点审批: {nxt['role']} 待审批"))
                    c.commit()
                    # 异步推送避免阻塞审批
                    def _push(uid, no_, biz_type_, biz_id_):
                        try:
                            dt_send_todo([uid], '📋 新的审批待办', f"{no_} 等待您的审批",
                                         f"节点: {nxt['role']}", biz_type_, biz_id_, push_type='auto', operator=approver)
                        except Exception:
                            pass
                    threading.Thread(target=_push, args=(u['dingtalk_userid'], doc_no, biz_type, biz_id), daemon=True).start()
    except Exception:
        pass
    # V55需求1-2: 先款后货合同生效(执行中)后, 自动生成待入库记录展示在入库板块
    # V11.11: 合同审批通过(执行中)后, 无论交易模式(货到付款/先款后货/自定义)均自动生成待入库记录
    if biz_type == 'contract' and st == '执行中':
        _ct = c.execute("SELECT * FROM contracts WHERE id=?", (biz_id,)).fetchone()
        if _ct and _ct['order_id']:
            _po = c.execute("SELECT * FROM purchase_orders WHERE id=?", (_ct['order_id'],)).fetchone()
            if _po:
                _exist = c.execute("SELECT 1 FROM receivings WHERE order_id=? AND status!='已入库'", (_po['id'],)).fetchone()
                if not _exist:
                    _oi = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (_po['id'],)).fetchall()
                    _name = _oi[0]['item_name'] if _oi else _po['item_name']
                    _qty = sum(float(x['quantity']) for x in _oi) if _oi else _po['quantity']
                    # V11.31: 自动带出部门(申请单→订单→入库单链)
                    _dept = _po['requester'] and '' or ''
                    try:
                        if _po['req_id']:
                            _pr = c.execute("SELECT dept FROM purchase_requests WHERE id=?", (_po['req_id'],)).fetchone()
                            if _pr and _pr['dept']:
                                _dept = _pr['dept']
                    except Exception:
                        pass
                    _rno = gen_no('RK', 'receivings', 'receive_no', c)
                    c.execute("INSERT INTO receivings(receive_no,delivery_id,order_id,item_name,spec,quantity,unit,qualified_qty,status,received_at,remark,dept) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                              (_rno, None, _po['id'], _name, '', _qty, '个', 0, '待入库', now(), '合同生效后自动进入入库板块(整批%d项)' % (len(_oi) if _oi else 1), _dept))
    c.execute("UPDATE feishu_instances SET status='synced', updated_at=? WHERE biz_type=? AND biz_id=? AND status='pending'", (now(), biz_type, biz_id))
    c.execute("UPDATE dingtalk_instances SET status='synced', updated_at=? WHERE biz_type=? AND biz_id=? AND status='pending'", (now(), biz_type, biz_id))
    c.commit(); c.close()
    log(approver, f'{biz_type}审批{"通过" if result=="ok" else "驳回"}', f'{biz_type}#{biz_id} → {st}')

    # V11.70: 审批办结通知发起人(站内信+钉钉工作通知)
    if result == 'ok' and st in ('已通过', '审批通过', '已入库', '已出库', '已签合同'):
        try:
            _d = c.execute(f"SELECT * FROM {biz_table(biz_type)} WHERE id=?", (biz_id,)).fetchone()
            if _d:
                _req = _d.get('requester') or _d.get('created_by') or _d.get('apply_by')
                if _req:
                    from app import find_user_by_name
                    _u = find_user_by_name(_req)
                    if _u and _u.get('dingtalk_userid'):
                        import threading as _th
                        def _notify():
                            try:
                                from app import dt_send_todo
                                _doc_no = _d.get('req_no') or _d.get('order_no') or _d.get('contract_no') or ''
                                dt_send_todo(
                                    [_u['dingtalk_userid']],
                                    f'✅ 审批通过 · {_doc_no}',
                                    f'{biz_type}已审批通过，可继续后续操作',
                                    f'单据: {_doc_no}',
                                    biz_type, str(biz_id),
                                    push_type='done',
                                    operator=approver
                                )
                            except Exception as e:
                                pass
                        _th.Thread(target=_notify, args=(), daemon=True).start()
        except Exception:
            pass
    return True

def fs_sync_result(instance_code, result):
    c = db()
    r = c.execute("SELECT * FROM feishu_instances WHERE instance_code=?", (instance_code,)).fetchone()
    c.close()
    if not r or r['status'] in ('synced', 'error'): return
    finish_approvals(r['biz_type'], r['biz_id'], 'ok' if result == 'approved' else 'reject', '飞书', 0, f'飞书审批{result}')

def fs_mark_cancelled(instance_code):
    c = db()
    c.execute("UPDATE feishu_instances SET status='cancelled', updated_at=? WHERE instance_code=?", (now(), instance_code))
    c.commit(); c.close()

# ---- 回调解密(encrypt_key模式) ----
def fs_decrypt(body):
    key_b64 = cfg_get('feishu_encrypt_key')
    if not key_b64: return None
    key = base64.b64decode(key_b64)
    iv = key[:16]
    raw = base64.b64decode(body.get('encrypt', ''))
    plain = None
    try:
        from Crypto.Cipher import AES
        plain = AES.new(key, AES.MODE_CBC, iv).decrypt(raw)
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            plain = dec.update(raw) + dec.finalize()
        except ImportError:
            raise RuntimeError('回调使用加密模式但缺少解密库: pip install pycryptodome, 或在飞书后台把加密策略改为"不加密"')
    if plain and 0 < plain[-1] <= 16: plain = plain[:-plain[-1]]
    return json.loads(plain.decode('utf-8'))

# ---- 预警提醒(全部走飞书机器人, 系统内不开发提醒) ----
def fs_send_reminders(force=False):
    if not feishu_enabled(): return
    approve_hours = int(cfg_get('feishu_approve_hours', '24') or 24)
    order_days = int(cfg_get('feishu_order_days', '3') or 3)
    report_chat = cfg_get('feishu_report_chat')
    today_s = today()
    c = db()
    def pushed(rule, key):
        if force: return False
        k = f"{today_s}:{key}"
        if c.execute("SELECT 1 FROM reminder_log WHERE rule=? AND key=?", (rule, k)).fetchone(): return True
        c.execute("INSERT INTO reminder_log(rule,key) VALUES(?,?)", (rule, k))
        return False
    def admins_open():
        return [r['feishu_open_id'] for r in c.execute("SELECT feishu_open_id FROM users WHERE role='系统管理员' AND feishu_open_id IS NOT NULL AND feishu_open_id!=''").fetchall()]
    # ① 超时未审批 → 推给对应审批角色
    rows = c.execute("""SELECT ai.* FROM approval_instances ai
        WHERE ai.status='pending' AND ai.created_at <= datetime('now','localtime',?)
        ORDER BY ai.created_at""", (f'-{approve_hours} hours',)).fetchall()
    by_role = {}
    for r in rows: by_role.setdefault(r['role'], []).append(r)
    for role, lst in by_role.items():
        u = find_user_by_role(role)
        if not u or not u['feishu_open_id']: continue
        if not pushed('approve_timeout', role):
            fs_send(u['feishu_open_id'], f"⏰ **{role}** 有 **{len(lst)}** 条审批超过 {approve_hours} 小时未处理:\n" +
                    '\n'.join(f"- {x['biz_type']}#{x['biz_id']} (提交于 {x['created_at']})" for x in lst[:10]), 'orange')
        for x in lst:  # 超过48小时升级给管理员
            try: age_h = int((datetime.datetime.now() - datetime.datetime.strptime(x['created_at'][:19], '%Y-%m-%d %H:%M:%S')).total_seconds() // 3600)
            except Exception: age_h = approve_hours
            if age_h >= 48 and not pushed('approve_escalate', x['id']):
                for ao in admins_open():
                    fs_send(ao, f"🚨 审批升级: {x['biz_type']}#{x['biz_id']} 已超 {age_h} 小时, 审批角色【{role}】, 请介入处理", 'red')
    # ② 待采未下单 → 推给申请人
    rows2 = c.execute("""SELECT pr.* FROM purchase_requests pr
        WHERE pr.status='已通过' AND pr.created_at <= datetime('now','localtime',?)
        AND NOT EXISTS (SELECT 1 FROM purchase_orders po WHERE po.req_id=pr.id)""", (f'-{order_days} days',)).fetchall()
    for r in rows2:
        u = find_user_by_name(r['requester'])
        if not u or not u['feishu_open_id']: continue
        if not pushed('req_no_order', r['id']):
            fs_send(u['feishu_open_id'], f"📝 采购申请 **{r['req_no']}** 已通过审批 {order_days} 天仍未转采购订单\n金额 ¥{r['total_estimated'] or 0:,.0f} · {r['purpose'] or ''}\n请及时下单", 'orange')
    # ③ 逾期未回货(送货单未签收) → 推给订单负责人
    rows3 = c.execute("""SELECT d.*, po.owner FROM deliveries d
        LEFT JOIN purchase_orders po ON d.order_id=po.id
        WHERE d.sign_status='待签收' AND d.delivery_date < date('now')""").fetchall()
    for r in rows3:
        u = find_user_by_name(r['owner'])
        if not u or not u['feishu_open_id']: continue
        if not pushed('delivery_overdue', r['id']):
            fs_send(u['feishu_open_id'], f"🚚 送货单 **{r['delivery_no']}** 计划 {r['delivery_date']} 到货, 至今未签收\n物资: {r['item_name']} x{r['quantity']}{r['unit']}\n请立即联系供应商催货", 'red')
    # ④ 订单超目标日 → 推给负责人
    rows4 = c.execute("""SELECT * FROM purchase_orders WHERE target_date < date('now') AND status NOT IN ('已完成','已关闭','已挂账','已入库')""").fetchall()
    for r in rows4:
        u = find_user_by_name(r['owner'])
        if not u or not u['feishu_open_id']: continue
        if not pushed('order_overdue', r['id']):
            fs_send(u['feishu_open_id'], f"📋 采购订单 **{r['order_no']}** 目标日 {r['target_date']} 已过, 当前状态【{r['status']}】\n物资: {r['item_name']} x{r['quantity']}{r['unit']} · 金额 ¥{r['total_amount'] or 0:,.0f}", 'orange')
    # ⑤ 每周一 09:00 汇总周报 → 报表群 + 管理员
    if datetime.date.today().weekday() == 0 and datetime.datetime.now().strftime('%H') == '09' and not pushed('weekly_report', 'wk'):
        stats = {
            '总额': c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE created_at >= datetime('now','localtime','-7 days')").fetchone()[0],
            '待审批': c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0],
            '进行中订单': c.execute("SELECT COUNT(*) FROM purchase_orders WHERE status NOT IN ('已完成','已关闭','已挂账')").fetchone()[0],
            '库存预警': c.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0],
            '超时未签收': c.execute("SELECT COUNT(*) FROM deliveries WHERE sign_status='待签收' AND delivery_date < date('now')").fetchone()[0],
        }
        text = (f"📊 **本周采购周报**\n- 近7天采购总额: ¥{stats['总额']:,.0f}\n- 当前待审批: {stats['待审批']} 条\n"
                f"- 进行中订单: {stats['进行中订单']} 条\n- 库存预警: {stats['库存预警']} 项\n- 超时未签收: {stats['超时未签收']} 单")
        if report_chat: fs_send(report_chat, text, 'blue', id_type='chat_id')
        for ao in admins_open(): fs_send(ao, text, 'blue')
    c.commit(); c.close()

def dt_poll_loop():
    """钉钉审批结果即时同步线程: 每60秒轮询一次(V11.28: 15s→60s, API消耗降75%)"""
    while True:
        try:
            time.sleep(60)
            if dingtalk_enabled():
                dt_poll_results()
                dt_retry_failed_instances()
                dt_terminate_stale()
        except Exception:
            pass


def dt_terminate_stale():
    """终态终止兜底: 系统侧已审批完(无pending节点)但钉钉实例仍在RUNNING → 终止。
    解决 terminate 偶发 internalError(审批流刚创建未稳定): 15秒轮询持续重试, 直到成功。"""
    try:
        if not dingtalk_enabled(): return
        c = db()
        rows = c.execute("SELECT * FROM dingtalk_instances WHERE status IN ('pending','synced')").fetchall()
        for r in rows:
            try:
                n_pending = c.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending'", (r['biz_type'], r['biz_id'])).fetchone()[0]
            except Exception:
                continue
            if n_pending > 0:
                continue
            inst = dt_query_instance(r['instance_code'])
            if not inst:
                continue
            if str(inst.get('status', '')) in ('RUNNING', 'NEW'):
                u = find_user_by_role('系统管理员')
                uid = u['dingtalk_userid'] if u and u['dingtalk_userid'] else dt_first_bound_userid()
                dt_terminate_instance(r['instance_code'], uid)
        c.close()
    except Exception:
        pass


def scheduler_loop():
    """预警调度: 每60秒扫描一次; 虾(xia_enabled=1)启用后预警全部交给虾独立服务, 系统本体不再推送"""
    while True:
        try:
            time.sleep(60)
            # V55: 系统内预警中心(首页集中展示)始终重建, 不受虾/飞书开关影响
            try:
                if cfg_get('warn_enabled', '1') == '1': build_alerts()
            except Exception:
                pass
            if cfg_get('xia_enabled') == '1': continue  # 虾接管提醒推送
            if feishu_enabled(): fs_send_reminders()
            if dingtalk_enabled(): dt_send_reminders()
        except Exception:
            pass

# ============================================================
# V5.2 ── 钉钉对接: 审批同步 + 工作通知 + 快捷审批 (urllib; 旧式回调需 pycryptodome)
# 审批模板需在钉钉OA审批后台手工创建(见《钉钉对接配置手册.md》), 模板process_code填入配置
# 流程: 单据进入待审批 → 发起钉钉审批实例(快捷审批) → 审批人钉钉处理 → 事件回调同步回系统
# ============================================================
DT_API = 'https://oapi.dingtalk.com'
DT_NEW_API = 'https://api.dingtalk.com'
DT_BIZ = {  # biz_type -> 审批模板名称
    'purchase_request': '采购申请审批',
    'purchase_order':   '采购订单审批',
    'contract':         '合同审批',
    'credit':           '挂账审批',
    'payment':          '付款审批',
    'receiving':        '入库审批',
    'requisition':      '出库审批',
}
DT_FORM = [('单据编号', 'text'), ('内容摘要', 'text'), ('金额(元)', 'text'), ('申请人', 'text'), ('提交时间', 'text')]

_DT_TOKEN = {'t': None, 'exp': 0}
_DT_TOKEN_NEW = {'t': None, 'exp': 0}
_DT_TICKET = {'t': None, 'exp': 0}

def dingtalk_enabled():
    return cfg_get('dingtalk_enabled') == '1'

def dt_token():
    if _DT_TOKEN['t'] and time.time() < _DT_TOKEN['exp'] - 120: return _DT_TOKEN['t']
    ak = cfg_get('dingtalk_app_key'); sk = cfg_get('dingtalk_app_secret')
    if not ak or not sk: raise RuntimeError('钉钉应用未配置: 请先在"钉钉设置"填写 AppKey / AppSecret')
    url = f'{DT_API}/gettoken?appkey={urllib.parse.quote(ak)}&appsecret={urllib.parse.quote(sk)}'
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            d = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        raise RuntimeError(f'钉钉网络错误: {e}')
    if d.get('errcode') != 0: raise RuntimeError(f'获取钉钉access_token失败: {d.get("errmsg", d)}')
    _DT_TOKEN['t'] = d.get('access_token'); _DT_TOKEN['exp'] = time.time() + int(d.get('expires_in', 7200))
    return _DT_TOKEN['t']

def dt_new_token():
    """新版接口 token (api.dingtalk.com/v1.0)"""
    if _DT_TOKEN_NEW['t'] and time.time() < _DT_TOKEN_NEW['exp'] - 120: return _DT_TOKEN_NEW['t']
    ak = cfg_get('dingtalk_app_key'); sk = cfg_get('dingtalk_app_secret')
    if not ak or not sk: raise RuntimeError('钉钉应用未配置: 请先在"钉钉设置"填写 AppKey / AppSecret')
    body = json.dumps({'appKey': ak, 'appSecret': sk}).encode('utf-8')
    req = urllib.request.Request(f'{DT_NEW_API}/v1.0/oauth2/accessToken', data=body,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        raise RuntimeError(f'钉钉新版接口网络错误: {e}')
    t = d.get('accessToken')
    if not t: raise RuntimeError(f'获取钉钉新版accessToken失败: {d}')
    _DT_TOKEN_NEW['t'] = t; _DT_TOKEN_NEW['exp'] = time.time() + int(d.get('expireIn', 7200))
    return _DT_TOKEN_NEW['t']

def dt_new_post(path, payload, timeout=12):
    """新版接口 POST, 返回 (err_code, resp)"""
    url = DT_NEW_API + path
    try:
        tok = dt_new_token()
    except Exception as e:
        return -1, {'msg': str(e)}
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json', 'x-acs-dingtalk-access-token': tok})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return 0, d
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode('utf-8'))
            return 1, d
        except Exception:
            return 1, {'msg': f'HTTP {e.code}'}
    except Exception as e:
        return -1, {'msg': f'网络错误: {e}'}

def dt_post(path, payload, timeout=12):
    """旧版接口 POST (oapi.dingtalk.com), 返回 (errcode, resp); 工作通知/手机号查询/SSO 用"""
    try:
        tok = dt_token()
    except Exception as e:
        return -1, {'msg': str(e)}
    sep = '&' if '?' in path else '?'
    url = DT_API + path + sep + 'access_token=' + urllib.parse.quote(tok)
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode('utf-8'))
        return d.get('errcode', -1), d
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode('utf-8'))
            return d.get('errcode', 1), d
        except Exception:
            return 1, {'msg': f'HTTP {e.code}'}
    except Exception as e:
        return -1, {'msg': f'网络错误: {e}'}

def dt_agent_id():
    try: return int(cfg_get('dingtalk_agent_id', '0') or 0)
    except Exception: return 0

def dt_approval_codes():
    try: return json.loads(cfg_get('dingtalk_approval_codes', '{}'))
    except Exception: return {}

def dt_approval_code(biz_type):
    return dt_approval_codes().get(biz_type, '')

def dt_public_url():
    f = os.path.join(BASE, 'data', 'public_url.txt')
    try:
        u = open(f).read().strip()
        return u if u.startswith('http') else ''
    except Exception:
        return ''

def dt_userid_by_mobile(mobile):
    code, resp = dt_post('/topapi/v2/user/getbymobile', {'mobile': mobile})
    if code != 0: return None, resp.get('msg', '查询失败')
    r = resp.get('result', {}) or {}
    return r.get('userid', ''), r.get('name', '')


def dt_first_bound_userid():
    """返回系统内第一个已绑定钉钉ID的用户(作发起人兜底)"""
    try:
        c = db()
        r = c.execute("SELECT dingtalk_userid FROM users WHERE dingtalk_userid IS NOT NULL AND dingtalk_userid != '' ORDER BY id LIMIT 1").fetchone()
        c.close()
        return r['dingtalk_userid'] if r else ''
    except Exception:
        return ''

def dt_union_id():
    """V11.8: 获取钉钉 unionId(上传附件必填, 缓存到 sys_config.dingtalk_union_id)
    取第一个已绑定 userid → /topapi/v2/user/get 拿 unionid; 失败逐用户尝试"""
    try:
        cached = cfg_get('dingtalk_union_id', '')
        if cached:
            return cached
        c = db()
        users = c.execute("SELECT name, dingtalk_userid FROM users WHERE dingtalk_userid IS NOT NULL AND dingtalk_userid != '' ORDER BY id").fetchall()
        c.close()
        for u in users:
            try:
                code_r, resp = dt_post('/topapi/v2/user/get', {'userid': u['dingtalk_userid']})
                if code_r == 0 and isinstance(resp, dict):
                    uid = (resp.get('result') or {}).get('unionid', '')
                    if uid:
                        cfg_set('dingtalk_union_id', uid)
                        return uid
            except Exception:
                continue
        return ''
    except Exception:
        return ''

def dt_biz_info(biz_type, biz_id):
    return fs_biz_info(biz_type, biz_id)  # 复用单据信息提取

def start_instances(biz_type, biz_id):
    """单据进入待审批后, 向已配置的外部通道(飞书/钉钉)同步发起审批实例"""
    fs_start_instance(biz_type, biz_id)
    dt_start_instance(biz_type, biz_id)

def dt_actioner_key(biz_type):
    """自选审批人节点的 actionerKey (模板相关, 从配置读, 缺失返回 '')"""
    try:
        return json.loads(cfg_get('dingtalk_actioner_keys', '{}')).get(biz_type, '')
    except Exception:
        return ''

def dt_cat_option(budget_name):
    """预算科目名 → 采购申请模板'采购类别'下拉选项(固定资产/行政办公/酒店日耗/工程采购)"""
    n = str(budget_name or '')
    if '固定' in n or '设备' in n or '维修' in n or '配件' in n: return '固定资产'
    if '酒店' in n or '耗材' in n or '客房' in n: return '酒店日耗'
    if '工程' in n or '施工' in n or '建设' in n: return '工程采购'
    return '行政办公'  # 兜底


def dt_date_ts(date_str):
    """'yyyy-MM-dd' → 毫秒时间戳字符串 (DDDateField 要求)"""
    try:
        d = datetime.datetime.strptime(str(date_str).strip()[:10], '%Y-%m-%d')
        return str(int(d.timestamp() * 1000))
    except Exception:
        return str(int(time.time() * 1000))


def dt_build_form(biz_type, biz_id, info):
    """V6.0: 按钉钉审批模板字段组装表单值（单据完整详情结构化展示）。

    采购申请审批模板字段(2026-08-06 schema 实测):
      部门=DepartmentField(必填,部门ID数组如[1]) / 采购类别=DDSelectField(必填,固定选项:
      固定资产/行政办公/酒店日耗/工程采购) / 采购事由=TextField(必填) /
      交付日期=DDDateField(必填,yyyy-MM-dd) / 附件=DDAttachment(可选) / 备注=TextareaField

    V6.0 增强(6.0.docx 需求1):
      - 单据基础信息全展示: 编号/申请人/部门/时间/类型/总金额/数量/紧急等级
      - 各单据类型全部表单字段结构化展示在"备注"(审批人钉钉端无需跳回系统)
    """
    today = datetime.date.today().strftime('%Y-%m-%d')
    if biz_type in ('purchase_request', 'contract', 'purchase_order', 'receiving', 'requisition', 'payment'):
        c = db()
        r = c.execute(f"SELECT * FROM {biz_table(biz_type)} WHERE id=?", (biz_id,)).fetchone()
        if r:
            detail = dt_build_detail(biz_type, r, c)
            if biz_type == 'purchase_request':
                # V11.15: 申请模板控件=部门/采购类别/采购事由/交付日期/备注/附件
                cat = dt_cat_option(r['budget_code'] or r['dept'] or '')
                purpose = str(r['purpose'] or '')[:200]
                target = str(r['target_date'] or today)[:10]
                form = [
                    {'name': '部门', 'value': '[1]'},
                    {'name': '采购类别', 'value': cat},
                    {'name': '采购事由', 'value': purpose},
                    {'name': '交付日期', 'value': target},
                    {'name': '备注', 'value': detail[:1900]},
                ]
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
            elif biz_type == 'contract':
                # V11.15: 合同模板控件=合同编号/对方单位名称/合同总额（元）/图片/备注/附件(全角括号实测)
                _amt = float(r['amount'] or 0)
                form = [
                    {'name': '合同编号', 'value': r['contract_no'] or ''},
                    {'name': '对方单位名称', 'value': r['contract_name'] or ''},
                    {'name': '合同总额（元）', 'value': '%.2f' % _amt},
                    {'name': '图片', 'value': '[]'},
                    {'name': '备注', 'value': detail[:1900]},
                ]
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
            elif biz_type == 'receiving':
                # V11.14: 入库模板控件=入库日期/备注/附件(表格控件"入库明细"格式无法兼容, 明细走备注文本)
                rdetail = dt_build_detail('receiving', r, c)
                form = [
                    {'name': '入库日期', 'value': str(r['received_at'] or today)[:10]},
                    {'name': '备注', 'value': rdetail[:1900]},
                ]
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
            elif biz_type == 'requisition':
                # V11.14: 出库模板控件=出库日期/备注/附件(表格控件"采购明细"格式无法兼容, 明细走备注文本)
                rdetail = dt_build_detail('requisition', r, c)
                form = [
                    {'name': '出库日期', 'value': str(r['issued_at'] or today)[:10]},
                    {'name': '备注', 'value': rdetail[:1900]},
                ]
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
            elif biz_type == 'payment':
                # V11.15: 付款模板控件=单据编号/内容摘要/金额(元)/申请人
                form = [
                    {'name': '单据编号', 'value': r['payment_no'] or ''},
                    {'name': '内容摘要', 'value': detail[:1900]},
                    {'name': '金额(元)', 'value': '%.2f' % float(r['amount'] or 0)},
                    {'name': '申请人', 'value': r['payee_name'] or r['requester'] or '系统'},
                ]
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
            elif biz_type == 'inquiry_approval':
                # V11.76: 询价审批表单=询价详情+供应商报价+选定供应商(单选)
                c2 = db()
                iq = c2.execute("SELECT * FROM inquiries WHERE id=?", (biz_id,)).fetchone()
                if not iq:
                    c2.close(); return []
                # 查询三家供应商报价
                sups = c2.execute("SELECT id, supplier_name, quote_price, quote_remark FROM inquiry_suppliers WHERE inquiry_id=? ORDER BY quote_price ASC", (biz_id,)).fetchall()
                supplier_opts = []
                supplier_details = []
                for si in sups:
                    supplier_opts.append({'value': str(si['id']), 'text': '%s (¥%.0f)' % (si['supplier_name'], si['quote_price'] or 0)})
                    detail = '%s报价¥%.0f' % (si['supplier_name'], si['quote_price'] or 0)
                    if si['quote_remark']:
                        detail += ' 备注:%s' % si['quote_remark']
                    supplier_details.append(detail)
                c2.close()
                form = [
                    {'name': '询价单号', 'value': iq['inq_no'] or ''},
                    {'name': '物资名称', 'value': (iq['title'] or '')[:50]},
                    {'name': '报价详情', 'value': '\\n'.join(supplier_details) if supplier_details else '暂无报价'},
                    {'name': '选定供应商', 'value': '[]'},  # 领导在钉钉上选
                    {'name': '备注', 'value': '请在上方"选定供应商"选择一家供应商后提交审批'},
                ]
                return form
            else:  # purchase_order
                cat = dt_cat_option(r['category'] or r['item_name'] or '')
                purpose = f"订单 {r['order_no']} {r['item_name'] or ''}"[:200]
                target = str(r['target_date'] or today)[:10]
                form = [
                    {'name': '部门', 'value': '["选项一"]'},
                    {'name': '销售方式', 'value': '["选项一"]'},
                    {'name': '订单图片', 'value': '[]'},
                    {'name': '备注', 'value': detail[:1900]},
                ]
                # 附件: 订单Excel凭证 → 钉钉附件
                attach = dt_build_attachment(biz_type, r, c)
                if attach:
                    form.append({'name': '附件', 'value': json.dumps(attach, ensure_ascii=False)})
                c.close()
                return form
    # 其他业务类型: 回退到原有字段组装(按各自模板配置)
    return [
        {'name': '采购名称', 'value': str(info[1])[:60]},
        {'name': '采购数量', 'value': '1'},
        {'name': '金额（元）', 'value': f"{float(info[2] or 0):.2f}"},
        {'name': '用途说明', 'value': f"{info[0]} {info[1]}"[:200]},
        {'name': '日期', 'value': today},
    ]


def dt_build_detail(biz_type, r, c):
    """V6.0: 组装单据完整详情结构化文本(钉钉备注字段, 审批人无需跳回系统)"""
    from collections import OrderedDict
    lines = OrderedDict()
    f = lambda v: str(v) if v is not None else ''
    if biz_type == 'purchase_request':
        lines['单据编号'] = r['req_no']; lines['单据类型'] = '采购申请'
        lines['申请人'] = r['requester']; lines['申请部门'] = r['dept']
        lines['申请时间'] = str(r['created_at'] or '')[:16]
        lines['预算归属'] = r['budget_code'] or '-'
        lines['采购用途'] = r['purpose']
        lines['需求到货'] = str(r['target_date'] or '')[:10]
        lines['预估总金额'] = f"¥{float(r['total_estimated'] or 0):,.2f}"
        lines['紧急等级'] = '🚨加急' if r['urgent'] else '普通'
        # 明细子表
        its = c.execute("SELECT * FROM request_items WHERE req_id=? ORDER BY id", (r['id'],)).fetchall()
        if its:
            lines['商品明细'] = ''
            for i, it in enumerate(its, 1):
                extra = ' '.join(x for x in [it['category'] if 'category' in it.keys() else '', it['brand_param'] if 'brand_param' in it.keys() else ''] if x)
                arr = it['arrival_date'] if 'arrival_date' in it.keys() and it['arrival_date'] else ''
                lines['商品明细'] += f"{i}. {it['item_name']} {it['spec'] or ''} x{it['quantity']}{it['unit'] or ''} 单价¥{float(it['estimated_price'] or 0):.2f} 小计¥{float(it['total_price'] or 0):.2f}" + (f" ({extra})" if extra else '') + (f" 到货:{arr}" if arr else '') + "\n"
        if r['remark']: lines['备注'] = r['remark']
    elif biz_type == 'contract':
        lines['合同编号'] = r['contract_no']; lines['单据类型'] = '采购合同'
        lines['合同名称'] = r['contract_name']; lines['合作供应商'] = r['supplier']
        lines['合同总金额'] = f"¥{float(r['amount'] or 0):,.2f}"
        lines['签订日期'] = str(r['sign_date'] or '')[:10]
        lines['履约周期'] = f"{str(r['start_date'] or '')[:10]} ~ {str(r['end_date'] or '')[:10]}"
        lines['紧急等级'] = '🚨加急' if r['urgent'] else '普通'
        # 关联订单/入库: 履约与质保信息
        if r['order_id']:
            po = c.execute("SELECT * FROM purchase_orders WHERE id=?", (r['order_id'],)).fetchone()
            if po:
                lines['来源订单'] = po['order_no']
                lines['付款方式'] = po['trade_mode'] or '-'
        if r['content']:
            txt = str(r['content'])[:400]
            lines['合同内容摘要'] = txt
        if r['remark']: lines['备注'] = r['remark']
    elif biz_type == 'purchase_order':
        lines['订单编号'] = r['order_no']; lines['单据类型'] = '采购订单'
        lines['供应商'] = r['supplier']; lines['申请人'] = r['requester']
        lines['下单时间'] = str(r['created_at'] or '')[:16]
        lines['交易模式'] = r['trade_mode'] or '货到付款'
        lines['紧急等级'] = '🚨加急' if r['urgent'] else '普通'
        lines['订单金额'] = f"¥{float(r['total_amount'] or 0):,.2f}"
        lines['物资名称'] = r['item_name']; lines['规格型号'] = r['spec'] or ''
        lines['数量'] = f"{r['quantity']}{r['unit'] or ''}"
        lines['单价'] = f"¥{float(r['price'] or 0):.2f}"
        lines['税率'] = f"{r['tax_rate'] or 13}%"
        lines['需求到货'] = str(r['target_date'] or '')[:10]
        if r['remark']: lines['备注'] = r['remark']
    elif biz_type == 'receiving':
        lines['入库单号'] = r['receive_no']; lines['单据类型'] = '入库单'
        # V11.29: 归属部门(钉钉审批也可见)
        lines['归属部门'] = r['dept'] if 'dept' in r.keys() and r['dept'] else '-'
        lines['关联订单'] = r['order_no'] if 'order_no' in r.keys() and r['order_no'] else ('#' + str(r['order_id']) if r['order_id'] else '-')
        lines['供应商'] = r['supplier'] if 'supplier' in r.keys() and r['supplier'] else '-'
        lines['仓库'] = r['warehouse'] or '主库房'
        lines['验收人'] = r['inspector'] or '-'
        lines['提交时间'] = str(r['created_at'] or '')[:16]
        lines['合格数量'] = f"{r['qualified_qty'] or r['quantity'] or 0}{r['unit'] or ''}"
        # 明细(手动入库单 items_json / 关联订单 order_items)
        its = []
        if r['items_json']:
            try: its = json.loads(r['items_json'])
            except Exception: its = []
        if not its and r['order_id']:
            its = [dict(x) for x in c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (r['order_id'],)).fetchall()]
        if its:
            lines['商品明细'] = ''
            for i, it in enumerate(its, 1):
                lines['商品明细'] += f"{i}. {it.get('item_name','')} {it.get('spec','') or ''} x{it.get('quantity',0)}{it.get('unit','') or ''}" + (f" 单价¥{float(it.get('price',0) or 0):.2f}" if it.get('price') is not None else '') + "\n"
        if r['remark']: lines['备注'] = r['remark']
    elif biz_type == 'requisition':
        lines['出库单号'] = r['req_no']; lines['单据类型'] = '出库单'
        lines['领用部门'] = r['dept'] or '-'; lines['领用人'] = r['requester'] or '-'
        # V11.25: 领取人/领取部门(出库追溯)
        lines['领取人'] = r['receiver'] or r['requester'] or '-'
        lines['领取部门'] = r['receive_dept'] or r['dept'] or '-'
        lines['提交时间'] = str(r['created_at'] or '')[:16]
        lines['数量'] = f"{r['quantity'] or 0}{r['unit'] or ''}"
        lines['用途'] = r['purpose'] or '-'
        its = c.execute("SELECT * FROM requisition_items WHERE requisition_id=? ORDER BY id", (r['id'],)).fetchall()
        if its:
            lines['商品明细'] = ''
            for i, it in enumerate(its, 1):
                lines['商品明细'] += f"{i}. {it['item_name']} {it['spec'] or ''} x{it['quantity']}{it['unit'] or ''}\n"
    elif biz_type == 'payment':
        lines['付款单号'] = r['payment_no']; lines['单据类型'] = '付款申请'
        lines['付款事由'] = r['payment_reason'] or '-'
        lines['供应商'] = r['supplier'] or '-'; lines['付款金额'] = f"¥{float(r['amount'] or 0):,.2f}"
        lines['期望付款日期'] = str(r['expect_pay_date'] or '')[:10]
        lines['发票类型'] = r['invoice_type'] or '-'
        lines['是否签合同'] = '是' if r['has_contract'] == '是' else '否'
        lines['收款人'] = f"{r['payee_name'] or '-'} {r['payee_account'] or ''}"
        lines['交易模式'] = r['trade_mode'] or '-'
        lines['紧急等级'] = '🚨加急' if r['urgent'] else '普通'
        if r['remark']: lines['备注'] = r['remark']
    # 组装多行文本: "字段: 值"
    out = []
    for k, v in lines.items():
        if k == '备注' and v:
            out.append(f"【{k}】\n{v}")
        elif v or k in ('商品明细', '入库明细'):
            out.append(f"【{k}】\n{v}".rstrip())
    return "\n".join(out)


def gen_doc_voucher(biz_type, biz_id, kind, title):
    """V11.9b: 单据附件凭证 — 复用系统现有下载接口生成的文件(与页面下载完全一致)
    申请/订单/入库/出库 → 调对应 /download 接口拿xlsx存uploads; 已存在则复用"""
    try:
        no = None
        c = db()
        r = c.execute(f"SELECT * FROM {biz_table(biz_type)} WHERE id=?", (biz_id,)).fetchone()
        if r:
            no = r['order_no'] if kind == 'order' else (r['req_no'] if kind == 'prequest' else (r['receive_no'] if kind == 'receiving' else r['req_no']))
        c.close()
        if not no:
            return None
        fname = f"voucher_{kind}_{no}.xlsx"
        fpath = os.path.join(BASE, 'uploads', fname)
        if os.path.exists(fpath):
            return fname
        # 内部调用现有下载接口(与页面"下载"按钮完全同一份生成逻辑)
        url = {'prequest': f'/api/prequests/{biz_id}/download',
               'order': f'/api/orders/{biz_id}/download',
               'receiving': f'/api/receivings/{biz_id}/download',
               'requisition': f'/api/requisitions/{biz_id}/download'}.get(kind)
        if not url:
            return None
        client = app.test_client()
        client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        resp = client.get(url)
        if resp.status_code != 200 or not resp.data:
            log('系统', '单据凭证生成失败', f'{biz_type}#{biz_id}: 下载接口HTTP {resp.status_code}')
            return None
        os.makedirs(os.path.join(BASE, 'uploads'), exist_ok=True)
        with open(fpath, 'wb') as f:
            f.write(resp.data)
        return fname
    except Exception as e:
        log('系统', '单据凭证生成失败', f'{biz_type}#{biz_id}: {str(e)[:120]}')
        return None


def dt_build_attachment(biz_type, r, c):
    """V11.9: 组装钉钉附件字段值 — 每类单据自动生成标准Excel凭证后上传
    申请/订单/入库/出库 → 自动生成凭证xlsx存uploads; 合同 → 已有docx
    返回 [{path,name,cat}] 本地路径标记, 由 dt_resolve_attachments 上传钉钉"""
    try:
        files = []
        # 合同: 自动生成的合同文件
        if biz_type == 'contract' and r['file_path']:
            files.append({'path': os.path.join(BASE, 'uploads', r['file_path']), 'name': r['file_path'],
                          'cat': '合同类'})
        # V11.9: 申请/订单/入库/出库 → 自动生成Excel凭证(领导审批可见)
        gen_map = {'purchase_request': ('prequest', '采购申请单'),
                   'purchase_order': ('order', '采购订单'),
                   'receiving': ('receiving', '入库验收单'),
                   'requisition': ('requisition', '出库单')}
        if biz_type in gen_map and r['id']:
            try:
                fn = gen_doc_voucher(biz_type, r['id'], gen_map[biz_type][0], gen_map[biz_type][1])
                if fn:
                    files.append({'path': os.path.join(BASE, 'uploads', fn), 'name': fn, 'cat': gen_map[biz_type][1]})
            except Exception as e:
                log('系统', '单据凭证生成失败', f'{biz_type}#{r["id"]}: {str(e)[:120]}')
        # 申请: attachments JSON 数组(纯文件名)
        for key in ('attachments', 'attachment'):
            if key in r.keys() and r[key]:
                try:
                    lst = json.loads(r[key]) if isinstance(r[key], str) else (r[key] or [])
                except Exception:
                    lst = [r[key]]
                for a in lst:
                    if isinstance(a, str) and a.strip():
                        files.append({'path': os.path.join(BASE, 'uploads', a.strip()), 'name': a.strip(), 'cat': '附件'})
        # 只保留真实存在的文件
        exist = [f for f in files if os.path.exists(f['path'])]
        return exist
    except Exception:
        return []


def dt_storage_space_id():
    """V8.5: 获取钉钉企业存储空间(ORG全员空间) — 审批附件存放于此, 组织内成员均可预览下载
    优先级: ①sys_config缓存(有效数字ID) ②审批钉盘空间(workflow域, 无需Storage权限) ③创建ORG空间(需Storage.Space.Write)
    返回 '' 表示不可用"""
    sid = cfg_get('dingtalk_storage_space_id', '')
    if sid and str(sid).isdigit():
        return sid
    if sid:  # 无效占位符(如 SPACE123) → 清除
        cfg_set('dingtalk_storage_space_id', '')
    # ① 审批钉盘空间(无需额外权限, 稳定可用)
    try:
        c, r = dt_new_post('/v1.0/workflow/processInstances/spaces/infos/query',
                           {'userId': dt_first_bound_userid() or '', 'agentId': dt_agent_id()})
        if c == 0 and r.get('result', {}).get('spaceId'):
            sid = str(r['result']['spaceId'])
            cfg_set('dingtalk_storage_space_id', sid)
            return sid
    except Exception as e:
        log('系统', '钉钉审批空间查询异常', str(e)[:120])
    # ② 创建ORG空间(需 Storage.Space.Write 权限)
    try:
        c2, r2 = dt_new_post('/v1.0/storage/spaces', {
            'option': {'name': '采购系统审批附件', 'ownerType': 'ORG', 'quota': 0}})
        if c2 == 0 and r2.get('space', {}).get('id'):
            sid = str(r2['space']['id'])
            cfg_set('dingtalk_storage_space_id', sid)
            return sid
        log('系统', '钉钉存储空间创建失败', json.dumps(r2, ensure_ascii=False)[:200])
    except Exception as e:
        log('系统', '钉钉存储空间创建异常', str(e)[:150])
    return ''


def dt_upload_file_to_dingtalk(path, filename):
    """V8.5: 本地文件上传钉钉企业存储(v1.0 storage: GetFileUploadInfo→签名URL直传→CommitFile)
    返回 {spaceId, fileName, fileSize, fileType, fileId} — OA审批附件组件必需结构。失败返回 None"""
    try:
        sid = dt_storage_space_id()
        if not sid:
            return None
        fname = os.path.basename(filename)
        ext = os.path.splitext(fname)[1].lower().lstrip('.')
        ftype = 'image' if ext in ('jpg', 'jpeg', 'png', 'gif', 'bmp') else 'file'
        with open(path, 'rb') as f:
            data = f.read()
        fsize = len(data)
        try:
            import hashlib
            md5 = hashlib.md5(data).hexdigest()
        except Exception:
            md5 = ''
        # 1. 获取上传信息(签名URL + uploadKey) — V11.8: URL必须带 ?unionId=
        _uid = dt_union_id()
        if not _uid:
            log('系统', '钉钉附件上传失败', f'{fname}: 未获取到unionId')
            return None
        c, r = dt_new_post(f'/v1.0/storage/spaces/{sid}/files/uploadInfos/query?unionId={_uid}', {
            'multipart': False,
            'protocol': 'HEADER_SIGNATURE',
            'option': {'preCheckParam': {'name': fname, 'size': fsize, 'md5': md5}}})
        if c != 0:
            # V10.4 自动重试: 空间授权过期(no privilege/permissionDenied) → 清缓存重新获取空间+unionId → 重试一次
            _msg = json.dumps(r, ensure_ascii=False)[:300] if r else ''
            if 'privilege' in _msg or 'permissionDenied' in _msg:
                log('系统', '钉钉空间授权过期', f'{fname}: {_msg[:80]} → 清缓存重取空间重试')
                cfg_set('dingtalk_storage_space_id', '')
                cfg_set('dingtalk_union_id', '')
                sid2 = dt_storage_space_id()
                _uid2 = dt_union_id()
                if sid2 and _uid2:
                    c, r = dt_new_post(f'/v1.0/storage/spaces/{sid2}/files/uploadInfos/query?unionId={_uid2}', {
                        'multipart': False,
                        'protocol': 'HEADER_SIGNATURE',
                        'option': {'preCheckParam': {'name': fname, 'size': fsize, 'md5': md5}}})
            if c != 0:
                log('系统', '钉钉附件上传失败', f'{fname}: 获取上传信息失败 {json.dumps(r, ensure_ascii=False)[:200]}')
                return None
        upload_key = r.get('uploadKey', '')
        hsi = r.get('headerSignatureInfo') or {}
        urls = hsi.get('resourceUrls') or []
        headers = hsi.get('headers') or {}
        if not upload_key or not urls:
            log('系统', '钉钉附件上传失败', f'{fname}: 无签名URL/uploadKey')
            return None
        # 2. 上传文件二进制到签名URL(直传 OSS) — V11.8: 必须用 http.client 手动PUT
        #    (urllib 带 data 会自动加 Content-Type, 破坏 OSS 签名 → 403 SignatureDoesNotMatch)
        try:
            import http.client
            from urllib.parse import urlparse
            _u = urlparse(urls[0])
            _conn = http.client.HTTPSConnection(_u.hostname, timeout=60)
            _conn.putrequest('PUT', _u.path + ('?' + _u.query if _u.query else ''))
            for _k, _v in (headers or {}).items():
                _conn.putheader(_k, _v)
            _conn.putheader('Content-Length', str(len(data)))
            _conn.endheaders()
            _conn.send(data)
            _resp = _conn.getresponse()
            _body = _resp.read()
            _conn.close()
            if _resp.status != 200:
                log('系统', '钉钉附件上传失败', f'{fname}: OSS直传HTTP {_resp.status} {_body[:120]}')
                return None
        except Exception as e:
            log('系统', '钉钉附件上传异常', f'{fname}: {str(e)[:120]}')
            return None
        # 3. 提交文件(dentry) → 获得 fileId — V11.8: URL带 ?unionId=
        c2, r2 = dt_new_post(f'/v1.0/storage/spaces/{sid}/files/commit?unionId={_uid}', {
            'name': fname, 'parentId': '0', 'uploadKey': upload_key})
        if c2 != 0:
            log('系统', '钉钉附件上传失败', f'{fname}: 提交失败 {json.dumps(r2, ensure_ascii=False)[:200]}')
            return None
        dentry = r2.get('dentry') or {}
        fid = dentry.get('id') or dentry.get('uuid') or ''
        if not fid:
            log('系统', '钉钉附件上传失败', f'{fname}: 提交成功但无fileId')
            return None
        return {'spaceId': sid, 'fileName': fname, 'fileSize': fsize, 'fileType': ftype, 'fileId': fid}
    except Exception as e:
        log('系统', '钉钉附件上传异常', str(e)[:150])
        return None


def dt_resolve_attachments(form, biz_type, biz_id):
    """V6.0: 表单中附件字段(本地路径标记) → 上传钉钉存储 → 替换为钉钉附件结构
    返回上传成功的附件数"""
    n = 0
    for fld in form:
        if fld.get('name') != '附件':
            continue
        try:
            local = json.loads(fld['value']) if isinstance(fld['value'], str) else fld['value']
        except Exception:
            continue
        if not isinstance(local, list):
            continue
        up = []
        for it in local:
            p = it.get('path', '')
            if not p or not os.path.exists(p):
                continue
            d = dt_upload_file_to_dingtalk(p, it.get('name', os.path.basename(p)))
            if d:
                up.append(d); n += 1
        if up:
            fld['value'] = json.dumps(up, ensure_ascii=False)
        else:
            # 无附件可上传 → 移除附件字段(避免空附件报错)
            form.remove(fld)
        break
    return n


def dt_start_instance(biz_type, biz_id):
    """单据进入待审批后, 同步发起钉钉审批实例(新版接口); 成功返回instance_id, 失败返回None"""
    try:
        if not dingtalk_enabled(): return None
        code = dt_approval_code(biz_type)
        if not code: return None
        ak = dt_actioner_key(biz_type)
        c = db()
        if c.execute("SELECT COUNT(*) FROM dingtalk_instances WHERE biz_type=? AND biz_id=? AND status NOT IN ('error','cancelled')", (biz_type, biz_id)).fetchone()[0] > 0:
            c.close(); return None
        levels = c.execute("SELECT DISTINCT role, approver FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no", (biz_type, biz_id)).fetchall()
        c.close()
        if not levels: return None
        approvers, missing = [], []
        for lv in levels:
            # V5.0+: 优先用审批节点配置的具体审批人(其钉钉ID); 未配置则按角色严格解析
            u = None
            if lv['approver']:
                cc = db()
                u = cc.execute("SELECT * FROM users WHERE name=? AND is_active=1 AND dingtalk_userid IS NOT NULL AND dingtalk_userid!=''", (lv['approver'],)).fetchone()
                if u is None:
                    # 配置的名字可能是用户名
                    u = cc.execute("SELECT * FROM users WHERE username=? AND is_active=1 AND dingtalk_userid IS NOT NULL AND dingtalk_userid!=''", (lv['approver'],)).fetchone()
                cc.close()
            if u is None:
                u = find_approver_for_role(lv['role'], 'dingtalk')
            if u and u['dingtalk_userid']: approvers.append(u['dingtalk_userid'])
            else: missing.append(f"{lv['role']}{(':'+lv['approver']) if lv['approver'] else ''}")
        if missing:
            # 需求(2026-08-06): 模板里选的审批人必须是钉钉通讯录成员, 系统对应角色必须是有效用户
            # 任一角色缺失 → 发起失败并明确记录, 不再用 admin 静默顶替
            log('系统', '钉钉审批未发起', f"{biz_type}#{biz_id} 以下节点无有效审批人(未启用或未绑定钉钉通讯录): {','.join(missing)}")
            return None
        info = dt_biz_info(biz_type, biz_id)
        if not info: return None
        originator = ''
        ua = find_user_by_name(str(info[3]))
        if ua and ua['dingtalk_userid']: originator = ua['dingtalk_userid']
        if not originator:
            # 兜底: 第一个已绑定钉钉ID的审批人, 再不行用系统内任意已绑定用户
            originator = approvers[0] if approvers else dt_first_bound_userid()
        if not originator:
            log('系统', '钉钉审批未发起', f"{biz_type}#{biz_id} 无已绑定钉钉ID的用户")
            return None
        form = dt_build_form(biz_type, biz_id, info)
        # V6.0: 系统附件 → 上传钉钉存储 → 挂载审批附件(钉钉端在线预览/下载)
        try:
            n_attach = dt_resolve_attachments(form, biz_type, biz_id)
            if n_attach:
                log('系统', '钉钉审批附件', f"{info[0]} 挂载{n_attach}个附件")
        except Exception:
            pass
        payload = {
            'originatorUserId': originator, 'processCode': code, 'deptId': 1,
            'formComponentValues': form,
        }
        if ak:
            # 模板含"自选审批人"节点时指定审批人; 否则走模板默认审批流
            payload['targetSelectActioners'] = [{'actionerKey': ak, 'actionerUserIds': approvers}]
        # 2026-08-06: 新API(v1.0/workflow/processInstances)对该模板报
        # processInstanceStartFailed, 旧API(/topapi/processinstance/create)实测成功 → 创建走旧API
        try:
            _create_payload = {
                'process_code': code,
                'originator_user_id': originator,
                'dept_id': 1,
                'form_component_values': form,
                'agent_id': dt_agent_id(),
            }
            # V8.0: 模板审批流为"发起人自选"时, 旧API用 approvers 指定审批人
            # (模板固定审批人时传了也不生效, 需在钉钉后台把审批节点改为自选/按角色)
            # V8.2: 只要有审批人就传 approvers(自选节点即生效, 无需 actionerKey)
            if approvers:
                _create_payload['approvers'] = approvers
            code_r, resp = dt_post('/topapi/processinstance/create', _create_payload)
            if code_r == 0:
                iid = resp.get('process_instance_id', '') or resp.get('process_instance', {}).get('instance_id', '')
                c = db()
                c.execute("INSERT INTO dingtalk_instances(instance_code,biz_type,biz_id,status) VALUES(?,?,?,'pending')", (iid, biz_type, biz_id))
                c.commit(); c.close()
                log('系统', '发起钉钉审批', f"{info[0]} 审批人{len(approvers)}级")
                dt_send_todo(approvers, f"新的{DT_BIZ[biz_type]}待处理", f"{info[0]} {info[1]}", f"金额 ¥{float(info[2] or 0):,.0f}", biz_type, biz_id)
                return iid
        except Exception as e:
            log('系统', '钉钉审批发起异常', f"{biz_type}#{biz_id}: {e}")
            return None
        c = db()
        c.execute("INSERT INTO dingtalk_instances(instance_code,biz_type,biz_id,status,error) VALUES(?,?,?,'error',?)",
                  (f'ERR-{biz_type}-{biz_id}-{int(time.time())}', biz_type, biz_id, json.dumps(resp, ensure_ascii=False)[:500]))
        c.commit(); c.close()
        log('系统', '钉钉审批发起失败', f"{biz_type}#{biz_id}: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return None
    except Exception as e:
        log('系统', '钉钉审批发起异常', f"{biz_type}#{biz_id}: {e}")
        return None

# ---- 工作通知(action_card, "去处理"直达系统审批页; 审批实例本身钉钉会另行通知) ----
def dt_send_todo(userids, title, text, extra='', biz_type='', biz_id=0, push_type='auto', operator='系统'):
    """V7.0: 钉钉工作通知推送(双触达: 工作台待办+工作通知) + 推送记录留存
    push_type: auto=自动节点推送 / urgent=手动加急提醒 / overdue=超期提醒 / alert=业务提醒 / test=测试
    V8.3: 审批类提醒(auto/urgent)改用钉钉OA审批原生通知(实例创建后钉钉自动通知审批人),
    不再经工作通知机器人(jiao助手)推送; 开关 sys_config.dingtalk_oa_notify_only=1 时生效"""
    try:
        if push_type in ('auto', 'urgent') and cfg_get('dingtalk_oa_notify_only', '0') == '1':
            return False
        agent = dt_agent_id()
        if not agent: return False
        userids = [u for u in userids if u]
        if not userids: return False
        url = ''
        pu = dt_public_url()
        if pu:
            url = pu.rstrip('/') + '/#approvals'
        msg = {'msgtype': 'action_card', 'action_card': {
            'title': title,
            'markdown': text + ('\n' + extra if extra else ''),
            'btn_orientation': '1',
            'btn_json_list': [{'title': '去处理', 'action_url': url}] if url else [],
        }}
        code_r, resp = dt_post('/topapi/message/corpconversation/asyncsend_v2', {
            'agent_id': agent, 'userid_list': ','.join(userids), 'msg': msg,
        })
        if code_r == 0:
            # 推送记录留存(7.0优化1: 每次推送留痕可查)
            try:
                c = db()
                names = []
                for uid in userids:
                    u = c.execute("SELECT name FROM users WHERE dingtalk_userid=?", (uid,)).fetchone()
                    names.append(u['name'] if u else uid)
                c.execute("INSERT INTO dingtalk_push_log(biz_type,biz_id,doc_no,push_type,target_user,target_userid,operator,content) VALUES(?,?,?,?,?,?,?,?)",
                          (biz_type or '', str(biz_id), '', push_type, '、'.join(names), ','.join(userids),
                           operator, f"{title} | {text}"[:200]))
                c.commit(); c.close()
            except Exception:
                pass
        return code_r == 0
    except Exception:
        return False


def dt_urgent_remind(biz_type, biz_id, operator):
    """V7.0优化1-2: 手动加急提醒 — 向当前待审批节点负责人钉钉二次推送
    限频: 同一单据 10 分钟内不可重复(可后台配置 urgent_limit_min)
    返回 (success, message)"""
    try:
        if not dingtalk_enabled():
            return False, '钉钉未启用'
        # 限频检查(默认1分钟 — V8.0: 原10分钟太慢, 加急提醒应可快速连续触发)
        limit_min = int(cfg_get('urgent_limit_min', '1') or 1)
        c = db()
        last = c.execute("SELECT MAX(created_at) FROM dingtalk_push_log WHERE biz_type=? AND biz_id=? AND push_type='urgent'", (biz_type, str(biz_id))).fetchone()[0]
        if last:
            try:
                from datetime import datetime as _dt
                last_dt = _dt.strptime(last, '%Y-%m-%d %H:%M:%S')
                if (_dt.now() - last_dt).total_seconds() < limit_min * 60:
                    c.close()
                    return False, f'{limit_min}分钟内已加急提醒过，请稍后再试'
            except Exception:
                pass
        # 找当前待审批节点负责人
        cur = c.execute("SELECT * FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no LIMIT 1", (biz_type, biz_id)).fetchone()
        if not cur:
            c.close(); return False, '该单据无待审批节点'
        u = None
        if cur['approver_id']:
            u = c.execute("SELECT * FROM users WHERE id=?", (cur['approver_id'],)).fetchone()
        if not u:
            u = c.execute("SELECT * FROM users WHERE role=? AND is_active=1 AND dingtalk_userid IS NOT NULL AND dingtalk_userid!='' ORDER BY id LIMIT 1", (cur['role'],)).fetchone()
        c.close()
        if not u or not u['dingtalk_userid']:
            return False, '当前审批人未绑定钉钉'
        # V8.3: 审批提醒统一走OA审批原生通知时, 手动加急不再经工作通知机器人推送
        if cfg_get('dingtalk_oa_notify_only', '0') == '1':
            return True, '审批提醒已统一走钉钉OA审批通知（钉钉会自动提醒当前审批人）'
        doc_no = dt_biz_info(biz_type, biz_id)
        doc_no = doc_no[0] if doc_no else f'{biz_type}#{biz_id}'
        title = '⏰ 人工加急提醒，请尽快审批'
        text = f"{doc_no} 审批等待中，发起人已加急催办，请尽快处理"
        ok = dt_send_todo([u['dingtalk_userid']], title, text,
                          f"单据: {doc_no}", biz_type, biz_id, push_type='urgent', operator=operator)
        return ok, ('加急提醒已发送给 ' + (u['name'] or u['username'])) if ok else '加急提醒发送失败'
    except Exception as e:
        return False, f'加急提醒异常: {str(e)[:80]}'

# ---- 审批结果同步(幂等) ----
def dt_sync_result(instance_id, result, comment=''):
    """V6.0: 钉钉审批结果回写系统(幂等)
    - result: agree/reject
    - comment: 钉钉审批意见(驳回原因等) → 回写单据留痕
    审批状态双向同步: 钉钉同意→系统单据通过; 钉钉驳回→系统单据驳回+原因留痕"""
    c = db()
    r = c.execute("SELECT * FROM dingtalk_instances WHERE instance_code=?", (instance_id,)).fetchone()
    c.close()
    if not r or r['status'] in ('synced', 'error'): return
    if comment:
        # 审批意见留痕: 写入审批实例 comment + 单据 rejected_reason
        c2 = db()
        c2.execute("UPDATE approval_instances SET comment=? WHERE biz_type=? AND biz_id=? AND status='pending'",
                   (str(comment)[:200], r['biz_type'], r['biz_id']))
        try:
            c2.execute(f"UPDATE {biz_table(r['biz_type'])} SET rejected_reason=? WHERE id=? AND rejected_reason IS NOT NULL",
                       (str(comment)[:200], r['biz_id']))
        except Exception:
            pass
        c2.commit(); c2.close()
        log('钉钉', '审批意见回写', f"{r['biz_type']}#{r['biz_id']}: {str(comment)[:100]}")
    finish_approvals(r['biz_type'], r['biz_id'], 'ok' if result == 'agree' else 'reject', '钉钉', 0, comment or f'钉钉审批{result}')
    # V11.28: 审批结果即时通知申请人(钉钉工作通知, 一次审批一次调用, 消耗可忽略)
    try:
        if dingtalk_enabled():
            _b = biz_table(r['biz_type'])
            _c = db()
            try:
                _row = _c.execute(f"SELECT * FROM {_b} WHERE id=?", (r['biz_id'],)).fetchone()
            except Exception:
                _row = None
            _c.close()
            if _row is not None:
                _doc_no = ''
                for _k in ('req_no', 'order_no', 'contract_no', 'receive_no', 'payment_no'):
                    if _k in _row.keys() and _row[_k]:
                        _doc_no = str(_row[_k]); break
                _requester = None
                for _k in ('requester', 'created_by', 'apply_by'):
                    if _k in _row.keys() and _row[_k]:
                        _requester = str(_row[_k]); break
                if _requester:
                    _u = db()
                    try:
                        _usr = _u.execute("SELECT * FROM users WHERE name=? LIMIT 1", (_requester,)).fetchone()
                    except Exception:
                        _usr = None
                    _u.close()
                    if _usr and _usr['dingtalk_userid']:
                        _verdict = '已通过 ✅' if result == 'agree' else '未通过 ❌'
                        dt_send_todo([_usr['dingtalk_userid']], f'审批结果通知：{_doc_no}',
                                     f"您提交的{_doc_no} 经钉钉审批 **{_verdict}**",
                                     biz_type=r['biz_type'], biz_id=r['biz_id'], push_type='auto',
                                     operator='系统')
    except Exception:
        pass


# ---- 审批结果轮询兜底(不依赖事件回调; corpId 缺失时也能同步结果) ----
def dt_query_instance(instance_id):
    """查询钉钉审批实例状态; 返回 dict 或 None"""
    try:
        code_r, resp = dt_post('/topapi/processinstance/get', {'process_instance_id': instance_id})
        if code_r != 0:
            return None
        return resp.get('process_instance') or resp.get('result') or resp
    except Exception:
        return None


def dt_poll_results():
    """遍历 dingtalk_instances 中 pending 状态实例, 查询钉钉侧结果并回写系统
    V11.28: 只查最近7天内活跃的pending实例(老实例不查, API消耗大降)"""
    try:
        c = db()
        rows = c.execute("""SELECT * FROM dingtalk_instances WHERE status='pending'
            AND updated_at >= datetime('now','localtime','-7 days')""").fetchall()
        c.close()
        n = 0
        for r in rows:
            try:
                inst = dt_query_instance(r['instance_code'])
                if not inst:
                    continue
                st = str(inst.get('status', ''))
                if st in ('APPROVED', 'COMPLETED'):
                    # COMPLETED(旧API) 需结合 result 判断 agree/reject
                    _res = str(inst.get('result', 'agree') or 'agree').lower()
                    dt_sync_result(r['instance_code'], 'agree' if _res in ('agree', 'agree_ok') else 'reject'); n += 1
                elif st in ('REJECTED',):
                    dt_sync_result(r['instance_code'], 'reject'); n += 1
                elif st in ('TERMINATED', 'CANCELED'):
                    dt_sync_result(r['instance_code'], 'refuse'); n += 1
            except Exception:
                continue
        return n
    except Exception:
        return 0


def dt_retry_failed_instances():
    """error 状态的钉钉实例自动重试(表单错误等修复后无需人工操作); 已过3分钟的才重试"""
    try:
        c = db()
        rows = c.execute("""SELECT * FROM dingtalk_instances WHERE status='error'
            AND created_at <= datetime('now','localtime','-3 minutes') LIMIT 5""").fetchall()
        c.close()
        for r in rows:
            try:
                dt_start_instance(r['biz_type'], r['biz_id'])
            except Exception:
                continue
    except Exception:
        pass


def dt_terminate_instance(instance_code, userid):
    """终止钉钉审批实例(系统侧已终态时调用, 避免钉钉侧继续挂起); 偶发 internalError 自动重试"""
    for attempt in range(3):
        try:
            code_r, resp = dt_new_post('/v1.0/workflow/processInstances/terminate', {
                'processInstanceId': instance_code,
                'operatingUserId': userid,
            })
            if code_r == 0:
                c = db()
                c.execute("UPDATE dingtalk_instances SET status='terminated', updated_at=? WHERE instance_code=? AND status IN ('pending','synced')", (now(), instance_code))
                c.commit(); c.close()
                log('系统', '终止钉钉审批', instance_code)
                return True
            if attempt < 2:
                time.sleep(2)
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
            else:
                log('系统', '终止钉钉审批异常', f"{instance_code}: {e}")
    log('系统', '终止钉钉审批失败', f"{instance_code}: {json.dumps(resp, ensure_ascii=False)[:200]}")
    return False


def dt_sync_now(biz_type, biz_id):
    """系统内审批动作后立即同步钉钉: ①查询该单据钉钉实例最新状态回写系统;
    ②若系统侧已终态(无pending节点)且钉钉实例仍在运行 → 终止钉钉实例"""
    try:
        if not dingtalk_enabled(): return
        c = db()
        # finish_approvals 已把本地实例置 synced, 但钉钉侧实例可能仍在 RUNNING →
        # 必须查 pending+synced 两态, 才能真正终止挂起的钉钉实例
        rows = c.execute("SELECT * FROM dingtalk_instances WHERE biz_type=? AND biz_id=? AND status IN ('pending','synced')", (biz_type, biz_id)).fetchall()
        n_pending = c.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending'", (biz_type, biz_id)).fetchone()[0]
        c.close()
        for r in rows:
            inst = dt_query_instance(r['instance_code'])
            if not inst:
                continue
            st = str(inst.get('status', ''))
            if st == 'APPROVED':
                dt_sync_result(r['instance_code'], 'agree')
            elif st in ('TERMINATED', 'CANCELED'):
                dt_sync_result(r['instance_code'], 'refuse')
            elif n_pending == 0 and st in ('RUNNING', 'NEW'):
                # 系统侧已审批完但钉钉实例还在跑 → 终止, 避免双通道不一致
                u = find_user_by_role('系统管理员')
                uid = u['dingtalk_userid'] if u and u['dingtalk_userid'] else dt_first_bound_userid()
                dt_terminate_instance(r['instance_code'], uid)
    except Exception:
        pass

# ---- 旧式回调加解密(AES-CBC; 需 pycryptodome; 新式事件订阅为明文JSON无需解密) ----
def _dt_aes_key():
    k = cfg_get('dingtalk_aes_key', '').strip()
    if not k: return None
    try:
        return base64.b64decode(k + '=' * ((4 - len(k) % 4) % 4))
    except Exception:
        return None

def dt_decrypt_old(encrypt):
    """旧式回调解密: 返回 dict(JSON) 或 str(纯文本如 success)"""
    from Crypto.Cipher import AES  # pycryptodome
    key = _dt_aes_key()
    if not key or not cfg_get('dingtalk_callback_token'):
        raise RuntimeError('钉钉回调加解密未配置(dingtalk_callback_token / dingtalk_aes_key)')
    raw = base64.b64decode(encrypt)
    cipher = AES.new(key, AES.MODE_CBC, key[:16])
    plain = cipher.decrypt(raw)
    if plain and 0 < plain[-1] <= 32: plain = plain[:-plain[-1]]
    msg_len = int.from_bytes(plain[16:20], 'big')
    text = plain[20:20 + msg_len].decode('utf-8')
    try:
        return json.loads(text)
    except Exception:
        return text

def dt_encrypt_old(msg):
    from Crypto.Cipher import AES  # pycryptodome
    key = _dt_aes_key()
    corp = cfg_get('dingtalk_corp_id', '')
    if not key or not corp: raise RuntimeError('钉钉回调加解密未配置')
    data = msg.encode('utf-8')
    # 钉钉旧式加密尾巴必须是 corp_id(不是 token), 否则钉钉解密校验失败
    payload = os.urandom(16) + len(data).to_bytes(4, 'big') + data + corp.encode('utf-8')
    pad = 32 - len(payload) % 32
    payload += bytes([pad]) * pad
    cipher = AES.new(key, AES.MODE_CBC, key[:16])
    return base64.b64encode(cipher.encrypt(payload)).decode('utf-8')

def dt_sign(timestamp, nonce, encrypt):
    token = cfg_get('dingtalk_callback_token', '')
    return hashlib.sha1(''.join(sorted([token, timestamp, nonce, encrypt])).encode('utf-8')).hexdigest()

# ---- 预警提醒(钉钉工作通知; 去重窗口8小时; 审批超时阈值默认8小时) ----
def dt_send_reminders(force=False):
    if not dingtalk_enabled(): return
    approve_hours = int(cfg_get('dingtalk_approve_hours', '8') or 8)
    order_days = int(cfg_get('dingtalk_order_days', '3') or 3)
    c = db()
    c.execute("DELETE FROM reminder_log WHERE pushed_at <= datetime('now','localtime','-8 hours')")
    today_s = today()
    def pushed(rule, key):
        if force: return False
        k = f"{today_s}:{key}"
        if c.execute("SELECT 1 FROM reminder_log WHERE rule=? AND key=?", (rule, k)).fetchone(): return True
        c.execute("INSERT INTO reminder_log(rule,key) VALUES(?,?)", (rule, k))
        return False
    def admins_dt():
        return [r['dingtalk_userid'] for r in c.execute("SELECT dingtalk_userid FROM users WHERE role='系统管理员' AND dingtalk_userid IS NOT NULL AND dingtalk_userid!=''").fetchall()]
    def role_userid(role):
        u = find_user_by_role(role)
        return u['dingtalk_userid'] if u and u['dingtalk_userid'] else ''
    # ① 审批超时未处理(默认>8小时) → 对应审批角色; >48小时升级管理员
    rows = c.execute("""SELECT ai.* FROM approval_instances ai
        WHERE ai.status='pending' AND ai.created_at <= datetime('now','localtime',?)
        ORDER BY ai.created_at""", (f'-{approve_hours} hours',)).fetchall()
    by_role = {}
    for r in rows: by_role.setdefault(r['role'], []).append(r)
    for role, lst in by_role.items():
        uid = role_userid(role)
        if not uid: continue
        if not pushed('dt_approve_timeout', role):
            dt_send_todo([uid], f"⏰ {role} 有 {len(lst)} 条审批超过 {approve_hours} 小时未处理",
                         '\n'.join(f"- {x['biz_type']}#{x['biz_id']} (提交于 {x['created_at']})" for x in lst[:10]))
        for x in lst:
            try: age_h = int((datetime.datetime.now() - datetime.datetime.strptime(x['created_at'][:19], '%Y-%m-%d %H:%M:%S')).total_seconds() // 3600)
            except Exception: age_h = approve_hours
            if age_h >= 48 and not pushed('dt_approve_escalate', x['id']):
                for ao in admins_dt():
                    dt_send_todo([ao], f"🚨 审批升级: {x['biz_type']}#{x['biz_id']} 已超 {age_h} 小时", f"审批角色【{role}】, 请介入处理")
    # ② 待采未下单(已通过N天未转订单) → 申请人 (按人合并消息, 减少API调用)
    rows2 = c.execute("""SELECT pr.* FROM purchase_requests pr
        WHERE pr.status='已通过' AND pr.created_at <= datetime('now','localtime',?)
        AND NOT EXISTS (SELECT 1 FROM purchase_orders po WHERE po.req_id=pr.id)""", (f'-{order_days} days',)).fetchall()
    by_req = {}
    for r in rows2:
        u = find_user_by_name(r['requester'])
        if not u or not u['dingtalk_userid']: continue
        by_req.setdefault(u['dingtalk_userid'], []).append(r)
    for uid, lst in by_req.items():
        if not pushed('dt_req_no_order', uid):
            lines = [f"- {r['req_no']} ¥{r['total_estimated'] or 0:,.0f} · {r['purpose'] or ''}" for r in lst[:10]]
            dt_send_todo([uid], f"📝 {len(lst)} 条采购申请已通过 {order_days} 天仍未转订单", '\n'.join(lines), push_type='alert')
    # ③ 逾期未回货(送货单未签收) → 订单负责人 (按人合并)
    rows3 = c.execute("""SELECT d.*, po.owner FROM deliveries d
        LEFT JOIN purchase_orders po ON d.order_id=po.id
        WHERE d.sign_status='待签收' AND d.delivery_date < date('now')""").fetchall()
    by_owner = {}
    for r in rows3:
        u = find_user_by_name(r['owner'])
        if not u or not u['dingtalk_userid']: continue
        by_owner.setdefault(u['dingtalk_userid'], []).append(r)
    for uid, lst in by_owner.items():
        if not pushed('dt_delivery_overdue', uid):
            lines = [f"- {r['delivery_no']} {r['item_name']} x{r['quantity']}{r['unit']} (计划 {r['delivery_date']})" for r in lst[:10]]
            dt_send_todo([uid], f"🚚 {len(lst)} 张送货单逾期未签收", '\n'.join(lines), push_type='alert')
    # ④ 订单超目标日 → 负责人 (按人合并)
    rows4 = c.execute("""SELECT * FROM purchase_orders WHERE target_date < date('now') AND status NOT IN ('已完成','已关闭','已挂账','已入库')""").fetchall()
    by_owner4 = {}
    for r in rows4:
        u = find_user_by_name(r['owner'])
        if not u or not u['dingtalk_userid']: continue
        by_owner4.setdefault(u['dingtalk_userid'], []).append(r)
    for uid, lst in by_owner4.items():
        if not pushed('dt_order_overdue', uid):
            lines = [f"- {r['order_no']} 目标日 {r['target_date']} 状态【{r['status']}】 ¥{r['total_amount'] or 0:,.0f}" for r in lst[:10]]
            dt_send_todo([uid], f"📋 {len(lst)} 条采购订单超目标日未完成", '\n'.join(lines), push_type='alert')
    # ⑤ 库存预警(≤安全库存) → 系统管理员 (合并成一条)
    rows5 = c.execute("""SELECT i.* FROM inventory i WHERE i.quantity<=i.safe_stock AND i.safe_stock>0 ORDER BY i.item_name""").fetchall()
    if rows5 and not pushed('dt_inventory_alert', 'all'):
        lines = [f"- {r['item_name']} 当前 {r['quantity']:g}{r['unit']}, 安全库存 {r['safe_stock']:g}{r['unit']} (仓库: {r['warehouse']})" for r in rows5[:15]]
        for ao in admins_dt():
            dt_send_todo([ao], f"⚠️ {len(rows5)} 项库存低于安全库存", '\n'.join(lines), push_type='alert')
    # ⑥ 每周一 09:00 周报 → 管理员
    if datetime.date.today().weekday() == 0 and datetime.datetime.now().strftime('%H') == '09' and not pushed('dt_weekly_report', 'wk'):
        stats = {
            '采购总额': c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE created_at >= datetime('now','localtime','-7 days')").fetchone()[0],
            '待审批': c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0],
            '进行中订单': c.execute("SELECT COUNT(*) FROM purchase_orders WHERE status NOT IN ('已完成','已关闭','已挂账')").fetchone()[0],
            '库存预警': c.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0],
            '超时未签收': c.execute("SELECT COUNT(*) FROM deliveries WHERE sign_status='待签收' AND delivery_date < date('now')").fetchone()[0],
        }
        text = '📊 本周采购周报\n' + '\n'.join(f'- {k}: {v:,.0f}' for k, v in stats.items())
        for ao in admins_dt():
            dt_send_todo([ao], '📊 本周采购周报', text, push_type='alert')
    c.commit(); c.close()

@app.route('/api/dingtalk/callback', methods=['GET', 'POST'])
def api_dingtalk_callback():
    """钉钉事件订阅回调: 新式明文JSON / 旧式AES加密; 审批结果同步回系统"""
    if request.method == 'GET':
        enc_param = request.args.get('encrypt', '')
        if not enc_param:
            # 新式事件订阅注册校验: 钉钉 GET 回调地址期望纯文本 success
            return 'success'
        ts = request.args.get('timestamp', ''); nonce = request.args.get('nonce', '')
        try:
            body = dt_decrypt_old(enc_param)
            inner = body.get('Random', '') if isinstance(body, dict) else ''
            enc = dt_encrypt_old(inner or 'success')
            return jsonify({'msg_signature': dt_sign(ts, nonce, enc), 'timeStamp': ts, 'nonce': nonce, 'encrypt': enc})
        except Exception as e:
            log('系统', '钉钉回调验证失败', str(e))
            return jsonify({'msg_signature': '', 'timeStamp': ts, 'nonce': nonce, 'encrypt': ''})
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}
    try:
        if body.get('encrypt'):
            body = dt_decrypt_old(body['encrypt'])
    except Exception as e:
        log('系统', '钉钉回调解密失败', str(e))
        return 'success'  # 钉钉要求纯文本success; 返回它避免重试轰炸
    if not isinstance(body, dict):
        return 'success'
    etype = body.get('eventType', '') or body.get('EventType', '')
    data = body.get('data', body) or {}
    if str(etype) in ('check_url', 'check_url_encrypt') or 'check_url' in str(etype):
        # 旧式加密回调校验: 钉钉 POST 加密 check_url, 必须返回加密的 success JSON(尾巴=corp_id)
        ts = request.args.get('timestamp', ''); nonce = request.args.get('nonce', '')
        try:
            enc = dt_encrypt_old('success')
            return jsonify({'msg_signature': dt_sign(ts, nonce, enc), 'timeStamp': ts, 'nonce': nonce, 'encrypt': enc})
        except Exception:
            return 'success'
    if 'bpms' in str(etype):
        iid = data.get('processInstanceId', '')
        result = data.get('result', '')
        # V6.0: 审批意见/驳回原因 → 回写系统留痕
        comment = str(data.get('remark', '') or '').strip()
        if not comment:
            records = data.get('approvalRecords') or []
            for rec in records:
                if rec.get('status') == 'REFUSE' and rec.get('remark'):
                    comment = rec['remark']
                    break
        if iid and result in ('agree', 'refuse'):
            dt_sync_result(iid, result, comment)
    return 'success'  # 钉钉事件回调统一要求纯文本success

@app.route('/api/dingtalk/config', methods=['GET', 'POST'])
@login_required
def api_dingtalk_config():
    if request.method == 'POST':
        if not can_manage_config(): return jsonify({'error': '仅系统管理员可配置'}), 403
        d = request.json or {}
        for k in ('dingtalk_app_key','dingtalk_app_secret','dingtalk_agent_id','dingtalk_corp_id',
                  'dingtalk_callback_token','dingtalk_aes_key','dingtalk_approve_hours','dingtalk_order_days'):
            if k in d: cfg_set(k, d[k])
        if 'dingtalk_enabled' in d: cfg_set('dingtalk_enabled', '1' if d['dingtalk_enabled'] else '0')
        codes = d.get('dingtalk_approval_codes') or {}
        if isinstance(codes, dict) and codes:
            cfg_set('dingtalk_approval_codes', json.dumps(codes, ensure_ascii=False))
        changed = [k for k in ('dingtalk_app_key','dingtalk_app_secret','dingtalk_agent_id','dingtalk_corp_id',
                  'dingtalk_callback_token','dingtalk_aes_key','dingtalk_approve_hours','dingtalk_order_days') if k in d]
        if 'dingtalk_enabled' in d: changed.append('dingtalk_enabled')
        if isinstance(codes, dict) and codes: changed.append('dingtalk_approval_codes')
        log(session.get('user_name',''), '修改钉钉配置', '变更项: %s' % (','.join(changed) or '无'))
        return jsonify({'success': True})
    c = db()
    rows = c.execute("SELECT key,value FROM sys_config WHERE key LIKE 'dingtalk_%'").fetchall()
    c.close()
    cfg = {r['key']: r['value'] for r in rows}
    cfg['dingtalk_approval_codes'] = dt_approval_codes()
    cfg['public_url'] = dt_public_url()
    return jsonify(cfg)

@app.route('/api/dingtalk/test', methods=['POST'])
@login_required
def api_dingtalk_test():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    try:
        tk = dt_token()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    target = (request.json or {}).get('userid', '')
    if target:
        ok = dt_send_todo([target], '✅ 采购系统钉钉对接测试成功', '这是一条测试工作通知', push_type='test')
        return jsonify({'success': ok, 'token': tk[:12] + '...', 'msg': '测试通知已发送' if ok else '发送失败, 请检查AgentId/权限'})
    return jsonify({'success': True, 'token': tk[:12] + '...'})

@app.route('/api/dingtalk/register-callback', methods=['POST'])
@login_required
def api_dingtalk_register_callback():
    """一键注册钉钉事件订阅(旧式call_back接口, 兼容稳定; 需应用开通事件订阅能力)"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    url = dt_public_url()
    if not url: return jsonify({'success': False, 'error': '未获取到公网地址(检查隧道与 data/public_url.txt)'})
    cb = url.rstrip('/') + '/api/dingtalk/callback'
    # 旧式回调: aes_key 必须恰好43位(字母数字), token 随机串
    if not cfg_get('dingtalk_aes_key'):
        cfg_set('dingtalk_aes_key', ''.join(secrets.choice('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') for _ in range(43)))
    if not cfg_get('dingtalk_callback_token'):
        cfg_set('dingtalk_callback_token', secrets.token_hex(8))
    payload = {'call_back_tag': ['bpms_instance_change', 'bpms_task_change'],
               'token': cfg_get('dingtalk_callback_token'), 'aes_key': cfg_get('dingtalk_aes_key'), 'url': cb}
    body = json.dumps(payload).encode('utf-8')
    try:
        req = urllib.request.Request(DT_API + '/call_back/register_call_back?access_token=' + dt_token(), data=body,
            headers={'Content-Type': 'application/json; charset=utf-8'})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode('utf-8'))
        if resp.get('errcode') == 0:
            return jsonify({'success': True, 'callbackId': resp.get('callbackId', ''), 'url': cb})
        # 71009=验证请求未通过(公网隧道/解密问题); 其余错误原样返回
        return jsonify({'success': False, 'error': resp.get('errmsg', '注册失败'), 'url': cb})
    except Exception as e:
        err = str(e)
        rd = getattr(e, 'read', None)
        if rd:
            try: err = rd().decode('utf-8', 'ignore')[:300]
            except Exception: pass
        return jsonify({'success': False, 'error': err, 'url': cb})

@app.route('/api/dingtalk/bind', methods=['POST'])
@login_required
def api_dingtalk_bind():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    d = request.json or {}
    c = db()
    c.execute("UPDATE users SET dingtalk_userid=? WHERE id=?", (str(d.get('userid', '')).strip(), int(d.get('user_id', 0))))
    c.commit(); c.close()
    log(session.get('user_name',''), '绑定钉钉', '用户#%s 绑定 userid=%s' % (d.get('user_id'), d.get('userid')))
    return jsonify({'success': True})

@app.route('/api/config-users')
@login_required
def api_config_users():
    """配置管理授权名单: 系统管理员可查看/添加/移除被授权用户(用自己账号登录即可配置系统/钉钉/飞书)"""
    if not can_manage_config():
        return jsonify({'error': '无权限'}), 403
    extra = [x.strip() for x in cfg_get('config_users', '').split(',') if x.strip()]
    conn = db()
    us = conn.execute("SELECT username, name, role, is_active FROM users ORDER BY id").fetchall()
    conn.close()
    return jsonify({'authorized': extra, 'users': [dict_row(u) for u in us],
                    'can_edit': session.get('user_role') == '系统管理员'})

@app.route('/api/config-users', methods=['POST'])
@login_required
def api_config_users_save():
    """保存授权名单: 仅系统管理员可改(防止被授权人自己扩权)"""
    if session.get('user_role') != '系统管理员':
        return jsonify({'error': '仅系统管理员可调整授权名单'}), 403
    d = request.json or {}
    names = d.get('usernames') or []
    names = [str(x).strip() for x in names if str(x).strip()]
    conn = db()
    # 校验用户名都存在
    for n in names:
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (n,)).fetchone():
            conn.close(); return jsonify({'error': '账号不存在: %s' % n}), 400
    conn.execute("INSERT INTO sys_config(key,value) VALUES('config_users',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (','.join(names),))
    conn.commit(); conn.close()
    log(session['user_name'], '调整配置授权', '授权名单: %s' % (','.join(names) or '空'))
    return jsonify({'success': True, 'authorized': names})

@app.route('/api/dingtalk/unbind', methods=['POST'])
@login_required
def api_dingtalk_unbind():
    """55.docx需求5: 钉钉账号解除绑定"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    d = request.json or {}
    c = db()
    c.execute("UPDATE users SET dingtalk_userid='' WHERE id=?", (int(d.get('user_id', 0)),))
    c.commit(); c.close()
    log(session['user_name'], '解绑钉钉', '用户#%s' % d.get('user_id'))
    return jsonify({'success': True})

@app.route('/api/dingtalk/lookup', methods=['POST'])
@login_required
def api_dingtalk_lookup():
    """按手机号查钉钉userid(需通讯录权限: 手机号获取userId)"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    mobile = str((request.json or {}).get('mobile', '')).strip()
    if not mobile: return jsonify({'success': False, 'error': '请输入手机号'})
    userid, name = dt_userid_by_mobile(mobile)
    if userid: return jsonify({'success': True, 'userid': userid, 'name': name})
    return jsonify({'success': False, 'error': '未找到该手机号对应的钉钉用户(需钉钉通讯录权限)'})

# ---- V7.0: 手动加急提醒(发起人/管理员可点, 限频, 留痕) ----
@app.route('/api/dingtalk/urgent-remind', methods=['POST'])
@login_required
def api_urgent_remind():
    """手动向当前待审批负责人钉钉加急提醒; 权限: 单据发起人/系统管理员; 10分钟内限一次"""
    d = request.json or {}
    biz_type = str(d.get('biz_type', '')); biz_id = int(d.get('biz_id', 0) or 0)
    if not biz_type or not biz_id:
        return jsonify({'error': '参数缺失'}), 400
    # 权限: 系统管理员 或 单据发起人
    me = db().execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    if not (me and me['role'] == '系统管理员'):
        conn = db()
        try:
            r = conn.execute(f"SELECT requester, requester_id FROM {biz_table(biz_type)} WHERE id=?", (biz_id,)).fetchone()
        except Exception:
            r = None
        conn.close()
        is_owner = r and (r['requester'] == session['user_name'] or r['requester_id'] == session['user_id'])
        if not is_owner:
            return jsonify({'error': '仅单据发起人或系统管理员可加急提醒'}), 403
    ok, msg = dt_urgent_remind(biz_type, biz_id, session['user_name'])
    if ok:
        log(session['user_name'], '钉钉加急提醒', f"{biz_type}#{biz_id} {msg}")
        return jsonify({'success': True, 'message': msg})
    return jsonify({'error': msg}), 400

# ---- V7.0: 超期未审批单据清单(首页预警板块, 支持一键加急) ----
@app.route('/api/approvals/overdue')
@login_required
def api_overdue_approvals():
    """超期未审批: 待审批单据停留超过可配置小时数(默认24h), 返回当前节点+审批人+停留时长"""
    hours = float(cfg_get('approve_hours', '24') or 24)
    conn = db()
    rows = conn.execute("""
        SELECT ai.*,
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.req_no FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.order_no FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.contract_no FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='payment' THEN (SELECT pp.payment_no FROM payment_requests pp WHERE pp.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.receive_no FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.req_no FROM requisitions rq WHERE rq.id=ai.biz_id)
            END as doc_no
        FROM approval_instances ai
        WHERE ai.status='pending' AND ai.created_at <= datetime('now','localtime', ?)
        ORDER BY ai.created_at ASC""", (f'-{int(hours)} hours',)).fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        # 停留时长
        try:
            from datetime import datetime as _dt
            created = _dt.strptime(str(d['created_at'])[:19], '%Y-%m-%d %H:%M:%S')
            d['wait_hours'] = round((_dt.now() - created).total_seconds() / 3600, 1)
        except Exception:
            d['wait_hours'] = None
        out.append(d)
    conn.close()
    return jsonify(out)

# ---- V7.0: 钉钉推送记录查询(留痕) ----
@app.route('/api/dingtalk/push-logs')
@login_required
def api_dingtalk_push_logs():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    conn = db()
    rows = conn.execute("SELECT * FROM dingtalk_push_log ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/dingtalk/status', methods=['GET'])
@login_required
def api_dingtalk_status():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    c = db()
    bound = c.execute("SELECT COUNT(*) FROM users WHERE dingtalk_userid IS NOT NULL AND dingtalk_userid!=''").fetchone()[0]
    insts = c.execute("SELECT COUNT(*) FROM dingtalk_instances").fetchone()[0]
    pend = c.execute("SELECT COUNT(*) FROM dingtalk_instances WHERE status='pending'").fetchone()[0]
    c.close()
    return jsonify({
        'enabled': dingtalk_enabled(), 'config_ok': bool(cfg_get('dingtalk_app_key') and cfg_get('dingtalk_app_secret')),
        'token_ok': bool(_DT_TOKEN['t']), 'bound_users': bound, 'instances': insts, 'pending_sync': pend,
        'codes': dt_approval_codes(), 'agent_ok': dt_agent_id() > 0, 'public_url': dt_public_url(),
    })

@app.route('/api/dingtalk/remind-now', methods=['POST'])
@login_required
def api_dingtalk_remind_now():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    dt_send_reminders(force=True)
    return jsonify({'success': True})

@app.route('/api/dingtalk/attachment-check', methods=['GET'])
@login_required
def api_dingtalk_attachment_check():
    """V9.1: 钉钉附件链路自检 — 逐段诊断: token/空间/上传权限/文件写入
    用于权限开通后的快速验证"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    out = {'steps': []}
    # 1. token
    try:
        tok = dt_token()
        out['steps'].append({'name': 'access_token', 'ok': bool(tok), 'detail': f"len={len(tok)}" if tok else '获取失败'})
    except Exception as e:
        out['steps'].append({'name': 'access_token', 'ok': False, 'detail': str(e)[:100]})
    # 2. 存储空间ID
    sid = dt_storage_space_id()
    out['steps'].append({'name': '存储空间', 'ok': bool(sid), 'detail': f"spaceId={sid}" if sid else '不可用(需开通Storage权限)'})
    # 3. 上传权限(获取上传信息) — V11.8: 带 unionId
    if sid:
        import hashlib
        _uid = dt_union_id()
        test_data = b'dingtalk attachment check'
        md5 = hashlib.md5(test_data).hexdigest()
        c, r = dt_new_post(f'/v1.0/storage/spaces/{sid}/files/uploadInfos/query?unionId={_uid}', {
            'multipart': False, 'protocol': 'HEADER_SIGNATURE',
            'option': {'preCheckParam': {'name': '_check.txt', 'size': len(test_data), 'md5': md5}}})
        if c == 0:
            out['steps'].append({'name': '上传权限', 'ok': True, 'detail': 'Storage.UploadInfo.Read 已开通 ✅'})
            # 4. 实际上传+提交(完整闭环)
            try:
                d = dt_upload_file_to_dingtalk_simple(test_data, '_check.txt')
                out['steps'].append({'name': '文件上传', 'ok': bool(d), 'detail': json.dumps(d, ensure_ascii=False)[:200] if d else '失败'})
            except Exception as e:
                out['steps'].append({'name': '文件上传', 'ok': False, 'detail': str(e)[:120]})
        else:
            msg = (r or {}).get('message', '') if isinstance(r, dict) else ''
            out['steps'].append({'name': '上传权限', 'ok': False, 'detail': msg[:180] or json.dumps(r, ensure_ascii=False)[:180]})
    out['ok'] = all(s['ok'] for s in out['steps'])
    return jsonify(out)

def dt_upload_file_to_dingtalk_simple(data, filename):
    """简化上传: bytes → 钉盘, 返回 {spaceId,fileName,fileSize,fileType,fileId} — V11.8: 带unionId"""
    import hashlib, mimetypes
    sid = dt_storage_space_id()
    if not sid: return None
    _uid = dt_union_id()
    if not _uid: return None
    fname = os.path.basename(filename)
    ext = os.path.splitext(fname)[1].lower().lstrip('.')
    ftype = 'image' if ext in ('jpg', 'jpeg', 'png', 'gif', 'bmp') else 'file'
    md5 = hashlib.md5(data).hexdigest()
    c, r = dt_new_post(f'/v1.0/storage/spaces/{sid}/files/uploadInfos/query?unionId={_uid}', {
        'multipart': False, 'protocol': 'HEADER_SIGNATURE',
        'option': {'preCheckParam': {'name': fname, 'size': len(data), 'md5': md5}}})
    if c != 0:
        # V10.4 自动重试: 空间授权过期 → 清缓存重取 → 重试一次
        _msg = json.dumps(r, ensure_ascii=False)[:300] if r else ''
        if 'privilege' in _msg or 'permissionDenied' in _msg:
            cfg_set('dingtalk_storage_space_id', '')
            cfg_set('dingtalk_union_id', '')
            sid2 = dt_storage_space_id()
            _uid2 = dt_union_id()
            if sid2 and _uid2:
                c, r = dt_new_post(f'/v1.0/storage/spaces/{sid2}/files/uploadInfos/query?unionId={_uid2}', {
                    'multipart': False, 'protocol': 'HEADER_SIGNATURE',
                    'option': {'preCheckParam': {'name': fname, 'size': len(data), 'md5': md5}}})
        if c != 0: return None
    upload_key = r.get('uploadKey', '')
    hsi = r.get('headerSignatureInfo') or {}
    urls = hsi.get('resourceUrls') or []
    headers = hsi.get('headers') or {}
    if not upload_key or not urls: return None
    try:
        import http.client
        from urllib.parse import urlparse
        _u = urlparse(urls[0])
        _conn = http.client.HTTPSConnection(_u.hostname, timeout=60)
        _conn.putrequest('PUT', _u.path + ('?' + _u.query if _u.query else ''))
        for _k, _v in (headers or {}).items():
            _conn.putheader(_k, _v)
        _conn.putheader('Content-Length', str(len(data)))
        _conn.endheaders()
        _conn.send(data)
        _resp = _conn.getresponse()
        _body = _resp.read()
        _conn.close()
        if _resp.status != 200:
            return None
    except Exception:
        return None
    c2, r2 = dt_new_post(f'/v1.0/storage/spaces/{sid}/files/commit?unionId={_uid}', {
        'name': fname, 'parentId': '0', 'uploadKey': upload_key})
    if c2 != 0: return None
    dentry = r2.get('dentry') or {}
    fid = dentry.get('id') or dentry.get('uuid') or ''
    if not fid: return None
    return {'spaceId': sid, 'fileName': fname, 'fileSize': len(data), 'fileType': ftype, 'fileId': fid}

@app.route('/api/dingtalk/sync-instances', methods=['POST'])
@login_required
def api_dingtalk_sync_instances():
    """把系统内所有待审批但未在钉钉发起的单据, 补发到钉钉"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    c = db()
    rows = c.execute("SELECT DISTINCT biz_type, biz_id FROM approval_instances WHERE status='pending'").fetchall()
    c.close()
    n = 0
    for r in rows:
        if dt_start_instance(r['biz_type'], r['biz_id']): n += 1
    return jsonify({'success': True, 'pushed': n, 'total': len(rows)})

@app.route('/api/dingtalk/sync-results', methods=['POST'])
@login_required
def api_dingtalk_sync_results():
    """手动触发: 查询钉钉审批结果并回写系统(轮询兜底, 不依赖事件回调/corpId)"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    n = dt_poll_results()
    return jsonify({'success': True, 'synced': n})

@app.route('/api/dingtalk/instances', methods=['GET'])
@login_required
def api_dingtalk_instances():
    c = db()
    rows = c.execute("SELECT * FROM dingtalk_instances ORDER BY id DESC LIMIT 30").fetchall()
    c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/dingtalk/sso', methods=['POST'])
def api_dingtalk_sso():
    """钉钉H5免登: 前端dd.config后取authCode, 换userid并匹配系统用户登录(需已在②中绑定)"""
    d = request.json or {}
    code = str(d.get('authCode', '')).strip()
    if not code: return jsonify({'error': '缺少authCode'}), 400
    code_r, resp = dt_post('/topapi/v2/user/getuserinfo', {'code': code})
    if code_r != 0: return jsonify({'error': resp.get('msg', '免登失败')}), 400
    userid = (resp.get('result', {}) or {}).get('userid', '')
    if not userid: return jsonify({'error': '未获取到钉钉userid'}), 400
    conn = db()
    u = conn.execute("SELECT * FROM users WHERE dingtalk_userid=? AND is_active=1", (userid,)).fetchone()
    conn.close()
    if not u:
        return jsonify({'error': '该钉钉账号未绑定系统用户, 请联系管理员在「钉钉设置 → 用户钉钉绑定」中绑定'}), 403
    session['user_id'] = u['id']; session['username'] = u['username']; session['user_name'] = u['name']; session['user_role'] = u['role']
    session['dept_id'] = u['dept_id']; session['dept_name'] = ''
    log(u['name'], '钉钉免登登录', f'userid={userid}')
    return jsonify({'success': True, 'name': u['name'], 'role': u['role']})

@app.route('/api/dingtalk/jsapi-ticket')
def api_dingtalk_jsapi_ticket():
    """钉钉JSAPI签名参数(dd.config), 钉钉内打开页面时使用"""
    if not (_DT_TICKET['t'] and time.time() < _DT_TICKET['exp'] - 120):
        try:
            url = f"{DT_API}/get_jsapi_ticket?access_token={dt_token()}"
            with urllib.request.urlopen(url, timeout=12) as r:
                d = json.loads(r.read().decode('utf-8'))
            if d.get('errcode') != 0: return jsonify({'error': d.get('errmsg', '获取ticket失败')}), 400
            _DT_TICKET['t'] = d.get('ticket'); _DT_TICKET['exp'] = time.time() + int(d.get('expires_in', 7200))
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    nonce = secrets.token_hex(8); ts = str(int(time.time()))
    jsurl = request.args.get('url', request.url_root.rstrip('/') + '/')
    sig = hashlib.sha1(f"jsapi_ticket={_DT_TICKET['t']}&noncestr={nonce}&timestamp={ts}&url={jsurl}".encode('utf-8')).hexdigest()
    return jsonify({'agentId': cfg_get('dingtalk_agent_id', ''), 'corpId': cfg_get('dingtalk_corp_id', ''),
                    'timeStamp': ts, 'nonceStr': nonce, 'signature': sig, 'ticket_ok': True})

@app.route('/api/approval-flow')
@login_required
def api_approval_flow():
    conn = db(); rows = conn.execute("SELECT * FROM approval_flow_config ORDER BY biz_type,level_no").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/approval-flow', methods=['POST'])
@login_required
def api_save_approval_flow():
    """V5.0: 审批流设置 — 管理员按 单据类型×金额区间 配置审批环节(角色)
    保存后新单据按新配置生成审批链; 审批人=角色对应 users 表有效用户, 钉钉/飞书自动同步"""
    if not can_manage_config():
        return jsonify({'error': '仅系统管理员可设置审批流'}), 403
    d = request.json or {}
    biz_type = str(d.get('biz_type', '')).strip()
    levels = d.get('levels') or []
    if biz_type not in ('purchase_request', 'purchase_order', 'contract', 'credit', 'payment', 'receiving', 'requisition'):
        return jsonify({'error': '未知单据类型'}), 400
    valid_roles = ('部门负责人', '财务', '分管领导', '总经理')
    parsed = []
    for lv in levels:
        role = str(lv.get('role', '')).strip()
        approver = str(lv.get('approver', '') or '').strip()  # 具体审批人用户名, 留空=按角色
        try:
            mn = float(lv.get('min_amount', 0) or 0)
            mx = float(lv.get('max_amount', 9999999) or 9999999)
        except Exception:
            return jsonify({'error': '金额区间必须为数字'}), 400
        if role not in valid_roles:
            return jsonify({'error': f'审批角色必须是: {"、".join(valid_roles)}'}), 400
        if mn < 0 or mx < mn:
            return jsonify({'error': '金额区间无效(最小不能小于0, 且最小≤最大)'}), 400
        if approver:
            # 校验具体审批人存在且有效
            u = conn_check_user(approver)
            if not u:
                return jsonify({'error': f'审批人「{approver}」不存在或未启用'}), 400
        parsed.append({'role': role, 'approver': approver, 'min_amount': mn, 'max_amount': mx})
    if not parsed:
        return jsonify({'error': '至少配置一级审批'}), 400
    conn = db()
    # 区间连续性校验: 排序后 每级 min 必须 ≤ 下一级 max(允许重叠取并集, 简化为: 第1级从0开始)
    sorted_lv = sorted(parsed, key=lambda x: x['min_amount'])
    if sorted_lv[0]['min_amount'] != 0:
        return jsonify({'error': '第一级审批金额下限必须为 0'}), 400
    conn.execute("DELETE FROM approval_flow_config WHERE biz_type=?", (biz_type,))
    for i, lv in enumerate(sorted_lv, 1):
        label = f"{i}级-{lv['role']}" + (f"-{lv['approver']}" if lv['approver'] else "")
        conn.execute("INSERT INTO approval_flow_config(biz_type,level_no,role,min_amount,max_amount,label,approver) VALUES(?,?,?,?,?,?,?)",
                     (biz_type, i, lv['role'], lv['min_amount'], lv['max_amount'], label, lv['approver']))
    conn.commit(); conn.close()
    log(session['user_name'], '修改审批流', f'{biz_type} → {len(sorted_lv)}级: ' + ' > '.join(f"{l['role']}({l['approver'] or '按角色'})[{l['min_amount']:.0f}-{l['max_amount']:.0f}]" for l in sorted_lv))
    return jsonify({'success': True, 'message': f'审批流已更新：{biz_type} {len(sorted_lv)} 级'})

# ---- V5.0: 审批人配置检查(角色→有效用户→钉钉/飞书通讯录绑定状态) ----
@app.route('/api/approval-config/check')
@login_required
def api_approval_config_check():
    """逐单据类型+金额分级展示审批链, 每级角色对应审批人是否有效/是否绑定通讯录
    让管理员一眼看出哪个角色缺人/未绑定, 满足'模板选的审批人必须是钉钉通讯录成员'的配置要求"""
    conn = db()
    flows = conn.execute("SELECT * FROM approval_flow_config ORDER BY biz_type,level_no").fetchall()
    users = conn.execute("SELECT id,username,name,role,is_active,dingtalk_userid,feishu_open_id FROM users ORDER BY role,id").fetchall()
    conn.close()
    user_map = {}
    for u in users:
        d = dict_row(u)
        user_map.setdefault(d['role'], []).append(d)
    # 各级角色去重后的完整清单(用于检查缺人)
    roles_used = sorted(set(r['role'] for r in flows))
    out_flows = []
    for f in flows:
        d = dict_row(f)
        role_users = user_map.get(d['role'], [])
        active = [u for u in role_users if u['is_active'] == 1]
        bound_dt = [u for u in active if u.get('dingtalk_userid')]
        bound_fs = [u for u in active if u.get('feishu_open_id')]
        # 配置的具体审批人: 解析用户名 → 显示姓名+绑定状态
        cfg_approver = ''
        cfg_approver_ok_dt = False
        if d.get('approver'):
            cfg_approver = d['approver']
            for u in role_users:
                if (u['username'] == d['approver'] or (u['name'] or '') == d['approver']):
                    cfg_approver = u['name'] or u['username']
                    cfg_approver_ok_dt = bool(u['is_active'] == 1 and u.get('dingtalk_userid'))
                    break
        d['approver'] = cfg_approver
        d['approver_bound_dt'] = cfg_approver_ok_dt
        d['role_users'] = role_users
        d['active_users'] = [u['name'] for u in active]
        d['dingtalk_bound'] = [u['name'] for u in bound_dt]
        d['feishu_bound'] = [u['name'] for u in bound_fs]
        # 状态: 配置了具体审批人→看该人; 否则看角色下是否有绑钉钉的有效用户
        d['config_ok_dt'] = cfg_approver_ok_dt if cfg_approver else bool(bound_dt)
        d['config_ok_fs'] = bool(bound_fs)
        out_flows.append(d)
    roles_status = []
    for role in roles_used:
        ru = user_map.get(role, [])
        roles_status.append({
            'role': role,
            'total': len(ru),
            'active': sum(1 for u in ru if u['is_active'] == 1),
            'dingtalk_bound': sum(1 for u in ru if u['is_active'] == 1 and u.get('dingtalk_userid')),
            'feishu_bound': sum(1 for u in ru if u['is_active'] == 1 and u.get('feishu_open_id')),
            'users': ru,
        })
    return jsonify({'flows': out_flows, 'roles': roles_status})

@app.route('/api/approvals/pending')
@login_required
def api_approvals_pending():
    conn = db()
    role = session['user_role']
    rows = conn.execute("""
        SELECT ai.*, 
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.req_no FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.order_no FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.contract_no FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='credit' THEN (SELECT cn.credit_no FROM credit_notes cn WHERE cn.id=ai.biz_id)
                 WHEN ai.biz_type='payment' THEN (SELECT pp.payment_no FROM payment_requests pp WHERE pp.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.receive_no FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.req_no FROM requisitions rq WHERE rq.id=ai.biz_id) END as biz_no,
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.purpose FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.item_name FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.contract_name FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='credit' THEN (SELECT cn.item_name FROM credit_notes cn WHERE cn.id=ai.biz_id)
                 WHEN ai.biz_type='payment' THEN (SELECT pp.payment_reason FROM payment_requests pp WHERE pp.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.item_name FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.item_name FROM requisitions rq WHERE rq.id=ai.biz_id) END as biz_name,
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.total_estimated FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.amount FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.quantity FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.quantity FROM requisitions rq WHERE rq.id=ai.biz_id) END as biz_amount
        FROM approval_instances ai WHERE ai.status='pending'
        AND (ai.role=? OR ai.role='部门负责人' AND ? IN ('部门负责人','系统管理员'))
        ORDER BY ai.id DESC LIMIT 50
    """, (role, role)).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/approvals/rejected')
@login_required
def api_approvals_rejected():
    """55.docx需求4: 审批未通过数据独立板块(按业务类别分组汇总)"""
    conn = db()
    rows = conn.execute("""SELECT ai.biz_type,
        CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.req_no FROM purchase_requests pr WHERE pr.id=ai.biz_id)
             WHEN ai.biz_type='purchase_order' THEN (SELECT po.order_no FROM purchase_orders po WHERE po.id=ai.biz_id)
             WHEN ai.biz_type='contract' THEN (SELECT ct.contract_no FROM contracts ct WHERE ct.id=ai.biz_id)
             WHEN ai.biz_type='credit' THEN (SELECT cn.credit_no FROM credit_notes cn WHERE cn.id=ai.biz_id)
             WHEN ai.biz_type='payment' THEN (SELECT pp.payment_no FROM payment_requests pp WHERE pp.id=ai.biz_id)
             WHEN ai.biz_type='receiving' THEN (SELECT rv.receive_no FROM receivings rv WHERE rv.id=ai.biz_id)
             WHEN ai.biz_type='requisition' THEN (SELECT rq.req_no FROM requisitions rq WHERE rq.id=ai.biz_id) END as biz_no,
             MAX(ai.comment) last_comment, COUNT(*) cnt, MAX(ai.processed_at) processed_at
        FROM approval_instances ai WHERE ai.status='rejected'
        GROUP BY ai.biz_type, ai.biz_id ORDER BY ai.biz_type, cnt DESC""").fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/approvals/all-pending')
@login_required
def api_all_pending():
    conn = db()
    rows = conn.execute("""
        SELECT ai.*, 
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.req_no FROM purchase_requests pr WHERE pr.id=ai.biz_id) 
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.order_no FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.contract_no FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='credit' THEN (SELECT cn.credit_no FROM credit_notes cn WHERE cn.id=ai.biz_id)
                 WHEN ai.biz_type='payment' THEN (SELECT pr.payment_no FROM payment_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.receive_no FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.req_no FROM requisitions rq WHERE rq.id=ai.biz_id)
            END as biz_no,
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.purpose FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.item_name FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='inquiry_approval' THEN (SELECT iq.title FROM inquiries iq WHERE iq.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.contract_name FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='credit' THEN (SELECT cn.item_name FROM credit_notes cn WHERE cn.id=ai.biz_id)
                 WHEN ai.biz_type='receiving' THEN (SELECT rv.item_name FROM receivings rv WHERE rv.id=ai.biz_id)
                 WHEN ai.biz_type='requisition' THEN (SELECT rq.item_name FROM requisitions rq WHERE rq.id=ai.biz_id)
            END as biz_name,
            CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.total_estimated FROM purchase_requests pr WHERE pr.id=ai.biz_id)
                 WHEN ai.biz_type='purchase_order' THEN (SELECT po.total_amount FROM purchase_orders po WHERE po.id=ai.biz_id)
                 WHEN ai.biz_type='contract' THEN (SELECT ct.amount FROM contracts ct WHERE ct.id=ai.biz_id)
                 WHEN ai.biz_type='credit' THEN (SELECT cn.amount FROM credit_notes cn WHERE cn.id=ai.biz_id)
                 WHEN ai.biz_type='payment' THEN (SELECT pr.amount FROM payment_requests pr WHERE pr.id=ai.biz_id)
            END as biz_amount
        FROM approval_instances ai WHERE ai.status='pending'
        ORDER BY ai.id DESC LIMIT 50
    """).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/approvals/<biz_type>/<int:biz_id>/list')
@login_required
def api_approval_list(biz_type, biz_id):
    conn = db(); rows = conn.execute("SELECT * FROM approval_instances WHERE biz_type=? AND biz_id=? ORDER BY level_no", (biz_type,biz_id)).fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/approvals/<biz_type>/<int:biz_id>/approve', methods=['POST'])
@login_required
def api_approve_action(biz_type, biz_id):
    d = request.json
    # V7.0: 审批操作权限锁死 — 仅当前节点指定审批人/系统管理员可操作
    conn = db()
    cur = conn.execute("SELECT * FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='pending' ORDER BY level_no LIMIT 1", (biz_type, biz_id)).fetchone()
    if cur:
        me = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
        is_admin = me and me['role'] == '系统管理员'
        node_approver_id = cur['approver_id'] if cur['approver_id'] else None
        if not node_approver_id:
            u = conn.execute("SELECT * FROM users WHERE role=? AND is_active=1 ORDER BY id LIMIT 1", (cur['role'],)).fetchone()
            node_approver_id = u['id'] if u else None
        if not is_admin and (not me or me['id'] != node_approver_id):
            conn.close()
            return jsonify({'error': '仅当前审批节点指定审批人可操作'}), 403
    conn.close()
    if d.get('action') == 'rejected':
        # 驳回: 全部待审节点驳回 + 父单据置为已驳回 (同步飞书实例)
        finish_approvals(biz_type, biz_id, 'reject', session['user_name'], session['user_id'], d.get('comment',''))
        dt_sync_now(biz_type, biz_id)  # 立即同步钉钉: 终止挂起的审批实例
        return jsonify({'success':True})
    sig = d.get('signature', '')
    r = do_approve(biz_type, biz_id, session['user_name'], session['user_id'], 'approved', d.get('comment',''), signature=sig)
    if not r['success']: return jsonify(r), 400
    finish_approvals(biz_type, biz_id, 'ok', session['user_name'], session['user_id'], d.get('comment',''))
    dt_sync_now(biz_type, biz_id)  # 立即同步钉钉: 查询最新状态/终态时终止实例
    return jsonify({'success':True})

# ============================================================
# V11.27 ── 预录签名(签名版): 领导手写一次, 审批通过的单据自动盖章
# ============================================================
@app.route('/api/signature/save', methods=['POST'])
@login_required
def api_signature_save():
    """保存预录签名(dataURL PNG) → uploads/signatures/ → sys_config 记文件名"""
    d = request.json
    data_url = (d.get('dataUrl') or '')
    name = (d.get('name') or '').strip()[:20] or session['user_name']
    if not data_url.startswith('data:image/png'):
        return jsonify({'error': '签名格式错误'}), 400
    import base64
    try:
        raw = base64.b64decode(data_url.split(',')[1])
    except Exception:
        return jsonify({'error': '签名数据解码失败'}), 400
    os.makedirs(os.path.join(BASE, 'uploads', 'signatures'), exist_ok=True)
    fn = f'sig_{name}_{datetime.datetime.now().strftime("%Y%m%d%H%M%S")}.png'
    with open(os.path.join(BASE, 'uploads', 'signatures', fn), 'wb') as f:
        f.write(raw)
    conn = db()
    conn.execute("INSERT OR REPLACE INTO sys_config(key,value) VALUES('leader_signature',?)", (fn,))
    conn.execute("INSERT OR REPLACE INTO sys_config(key,value) VALUES('leader_signature_name',?)", (name,))
    conn.commit(); conn.close()
    log(session['user_name'], '保存预录签名', name)
    return jsonify({'success': True, 'file': fn})

@app.route('/api/signature/info')
@login_required
def api_signature_info():
    """返回预录签名状态(有无/名字/图片URL)"""
    conn = db()
    fn = conn.execute("SELECT value FROM sys_config WHERE key='leader_signature'").fetchone()
    nm = conn.execute("SELECT value FROM sys_config WHERE key='leader_signature_name'").fetchone()
    conn.close()
    f = fn[0] if fn else ''
    return jsonify({'has': bool(f), 'name': (nm[0] if nm else '') or '',
                    'url': '/uploads/signatures/' + f if f else ''})

@app.route('/api/signature/delete', methods=['POST'])
@login_required
def api_signature_delete():
    """删除预录签名"""
    conn = db()
    fn = conn.execute("SELECT value FROM sys_config WHERE key='leader_signature'").fetchone()
    if fn and fn[0]:
        try:
            p = os.path.join(BASE, 'uploads', 'signatures', fn[0])
            if os.path.exists(p): os.remove(p)
        except Exception: pass
    conn.execute("DELETE FROM sys_config WHERE key IN ('leader_signature','leader_signature_name')")
    conn.commit(); conn.close()
    log(session['user_name'], '删除预录签名', '')
    return jsonify({'success': True})

def get_leader_sign():
    """返回 (签名文件名, 签名人名字); 无则 ('','')"""
    conn = db()
    fn = conn.execute("SELECT value FROM sys_config WHERE key='leader_signature'").fetchone()
    nm = conn.execute("SELECT value FROM sys_config WHERE key='leader_signature_name'").fetchone()
    conn.close()
    return ((fn[0] if fn else ''), (nm[0] if nm else '') or '')

def stamp_leader_sign(ws, sign_row, biz_type='', biz_id=0):
    """V11.27: 单据Excel盖章 — 若单据已审批通过且配置了预录签名, 在签字区盖签名图+说明
    ws: openpyxl worksheet; sign_row: 签字区行号; 签名图插入到签字文本右侧空白区"""
    try:
        sign_fn, sign_name = get_leader_sign()
        if not sign_fn:
            return
        # 判断单据是否已审批通过(有 approved 节点)
        conn = db()
        ok = conn.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type=? AND biz_id=? AND status='approved'",
                          (biz_type, biz_id)).fetchone()[0] > 0
        conn.close()
        if not ok:
            return
        import openpyxl
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Font as XLFont
        img_path = os.path.join(BASE, 'uploads', 'signatures', sign_fn)
        if not os.path.exists(img_path):
            return
        img = XLImage(img_path)
        # V11.71: 签名尺寸(150x50), 紧凑放在H7(部门负责人右侧)
        try:
            from PIL import Image as PILImage
            pil_img = PILImage.open(img_path)
            orig_w, orig_h = pil_img.size
            max_w, max_h = 150, 50
            scale = min(max_w / orig_w, max_h / orig_h, 1.0)
            img.width = int(orig_w * scale)
            img.height = int(orig_h * scale)
        except Exception:
            img.width = 150; img.height = 50
        # 放在H7(部门负责人文字右侧)
        anchor = ws.cell(row=7, column=8).coordinate
        img.anchor = anchor
        ws.add_image(img)
        # 签名说明小字 (自动找签字区下方第一个非合并单元格)
        try:
            from openpyxl.cell.cell import MergedCell
            nr = sign_row + 1
            note = None
            while nr < sign_row + 20:
                cc = ws.cell(row=nr, column=6)
                if not isinstance(cc, MergedCell):
                    note = cc; break
                nr += 1
            if note is not None:
                note.value = '本单据经系统电子审批，签名由系统自动生成'
                note.font = XLFont(name='宋体', size=7, color='999999')
        except Exception:
            pass
    except Exception as e:
        print('stamp_leader_sign err:', e)

# ============================================================
# ── PURCHASE REQUESTS (多行申请) ──
# ============================================================
@app.route('/api/prequests')
@login_required
def api_prequests():
    # V11.64: 数据权限 — 员工/部门负责人只看自己的; 采购员/财务/领导/库管员看各自域
    role = session.get('user_role')
    scope = filter_scope(role)
    conn = db()
    if scope == 'own':
        rows = conn.execute("SELECT * FROM purchase_requests WHERE requester_id=? ORDER BY id DESC LIMIT 100", (session.get('user_id', 0),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM purchase_requests ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        # V11.64: 库管员/员工/部门负责人 价格脱敏
        if scope in ('stock', 'own') and not can_see_price():
            d = mask_price(d)
        d['req_type'] = r['req_type'] if 'req_type' in r.keys() and r['req_type'] else '物资采购'
        # V11.49: 采购进度状态(红=未联系厂家/黄=已下单在途/绿=已到货)
        d['progress'] = 'none'
        if d.get('status') == '已通过':
            po = conn.execute("SELECT status FROM purchase_orders WHERE req_id=? ORDER BY id DESC LIMIT 1", (r['id'],)).fetchone()
            if po and po['status'] == '已入库':
                d['progress'] = 'arrived'      # 绿: 已入库
            elif po:
                d['progress'] = 'shipped'      # 黄: 已下单/在途
            else:
                d['progress'] = 'contact'      # 红: 未联系厂家
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/prequests/next_no')
@login_required
def api_prequest_next_no():
    # V11.45: 单号前缀随部门
    dept = request.args.get('dept') or ''
    return jsonify({'req_no': gen_req_no(dept)})

@app.route('/api/prequests/<int:rid>')
@login_required
def api_prequest(rid):
    conn = db()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (rid,)).fetchone()
    items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (rid,)).fetchall()
    approvals = conn.execute("SELECT * FROM approval_instances WHERE biz_type='purchase_request' AND biz_id=? ORDER BY level_no", (rid,)).fetchall()
    conn.close()
    return jsonify({'request':dict_row(pr),'items':[dict_row(i) for i in items],'approvals':[dict_row(a) for a in approvals]})

@app.route('/api/prequests', methods=['POST'])
@login_required
def api_create_prequest():
    d = request.json
    conn = db()
    items = d.get('items', [])
    total = sum(float(i.get('quantity',1)) * float(i.get('estimated_price',0)) for i in items)
    apply_date = d.get('apply_date') or datetime.date.today().strftime('%Y-%m-%d')
    # 并发安全: 单号冲突(UNIQUE)时重新生成重试(最多5次)
    no = ''
    for _try in range(5):
        no = gen_req_no(d.get('dept', ''), conn)
        try:
            conn.execute("""INSERT INTO purchase_requests(req_no,dept,requester,requester_id,budget_code,purpose,target_date,total_estimated,remark,attachments,urgent,apply_date,req_type)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (no, d.get('dept',''), session['user_name'], session['user_id'], d.get('budget_code',''),
                 d.get('purpose',''), d.get('target_date'), total, d.get('remark',''),
                 json.dumps(d.get('attachments') or [], ensure_ascii=False), 1 if d.get('urgent') else 0, apply_date,
                 d.get('req_type') or '物资采购'))
            break
        except sqlite3.IntegrityError:
            continue
    else:
        conn.close(); return jsonify({'error': '单号生成冲突，请重试'}), 500
    prid = conn.execute("SELECT id FROM purchase_requests WHERE req_no=?", (no,)).fetchone()[0]
    for it in items:
        tp = float(it.get('quantity',1)) * float(it.get('estimated_price',0))
        conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,estimated_price,total_price,remark,category,brand_param,arrival_date,attach) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (prid, it.get('item_name',''), it.get('spec',''), it.get('unit','个'), float(it.get('quantity',1)),
                      float(it.get('estimated_price',0)), tp, it.get('remark',''),
                      it.get('category',''), it.get('brand_param',''), it.get('arrival_date',''),
                      it.get('attach','') or ''))
    conn.commit()
    create_approvals('purchase_request', prid, total)
    start_instances('purchase_request', prid)   # 飞书/钉钉同步发起审批(未配置则跳过)
    conn.close()
    log(session['user_name'], '创建采购申请', f'{no} 共{len(items)}项 ¥{total:.0f}')
    return jsonify({'success':True, 'req_no':no, 'id':prid})

@app.route('/api/inventory/<int:iid>/replenish', methods=['POST'])
@login_required
def api_inventory_replenish(iid):
    """V11.33: 库存预警一键补货 — 低于安全库存的物资自动生成采购申请(补到安全线)"""
    conn = db()
    inv = conn.execute("SELECT * FROM inventory WHERE id=?", (iid,)).fetchone()
    if not inv:
        conn.close(); return jsonify({'error': '库存物资不存在'}), 404
    qty = float(inv['quantity'] or 0); safe = float(inv['safe_stock'] or 0)
    if safe <= 0 or qty >= safe:
        conn.close(); return jsonify({'error': '该物资未设置安全库存或未低于安全线'}), 400
    buy_qty = safe - qty  # 补到安全线
    price = float(inv['price'] or 0)
    est = buy_qty * price
    no = ''
    for _try in range(5):
        # V11.45: 补货申请归属采购部(CG前缀)
        no = gen_req_no('采购与供应链部', conn)
        try:
            conn.execute("""INSERT INTO purchase_requests(req_no,dept,requester,requester_id,budget_code,purpose,target_date,total_estimated,remark,urgent,apply_date)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (no, '采购与供应链部', session['user_name'], session['user_id'], '',
                 f'库存补货: {inv["item_name"]}', datetime.date.today().strftime('%Y-%m-%d'), est,
                 f'自动补货(库存不足): 当前{inv["quantity"]:g}{inv["unit"]}, 安全线{safe:g}{inv["unit"]}, 补货{buy_qty:g}{inv["unit"]}', 0,
                 datetime.date.today().strftime('%Y-%m-%d')))
            break
        except sqlite3.IntegrityError:
            continue
    else:
        conn.close(); return jsonify({'error': '单号生成冲突，请重试'}), 500
    prid = conn.execute("SELECT id FROM purchase_requests WHERE req_no=?", (no,)).fetchone()[0]
    conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,estimated_price,total_price,remark,category,brand_param,arrival_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (prid, inv['item_name'], inv['spec'] or '', inv['unit'] or '个', buy_qty, price, est,
                  '库存自动补货', inv['cat_code'] or '', '', ''))
    conn.commit()
    create_approvals('purchase_request', prid, est)
    try: start_instances('purchase_request', prid)
    except Exception: pass
    conn.close()
    log(session['user_name'], '库存一键补货', f'{no} {inv["item_name"]} x{buy_qty:g} 自动补货')
    return jsonify({'success': True, 'req_no': no, 'id': prid, 'qty': buy_qty})

@app.route('/api/prequests/<int:rid>/reject', methods=['POST'])
@login_required
def api_reject_prequest(rid):
    d = request.json or {}
    conn = db()
    conn.execute("UPDATE purchase_requests SET status='已驳回', rejected_reason=?, updated_at=? WHERE id=?", (d.get('reason',''), now(), rid))
    conn.execute("UPDATE approval_instances SET status='rejected' WHERE biz_type='purchase_request' AND biz_id=? AND status='pending'", (rid,))
    conn.commit(); conn.close()
    log(session['user_name'], '驳回采购申请', f'申请#{rid}: {d.get("reason","")}')
    return jsonify({'success':True})

@app.route('/api/prequests/<int:rid>/resubmit', methods=['POST'])
@login_required
def api_resubmit_prequest(rid):
    """V8.0: 采购申请编辑/重提 — 任意状态可修改保存
    - 已驳回/草稿: 保存后重新进入审批流(待审批)
    - 待审批/已通过: 保存后回到待审批重新走流程(撤回原审批)
    - 已通过且已被订单引用: 禁止编辑"""
    d = request.json
    conn = db()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (rid,)).fetchone()
    if not pr:
        conn.close(); return jsonify({'error': '申请不存在'}), 404
    if pr['status'] == '已通过':
        cnt = conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (rid,)).fetchone()[0]
        if cnt > 0:
            conn.close(); return jsonify({'error': '该申请已被订单引用，不可修改（可先删除订单）'}), 400
    if pr['status'] == '已作废':
        conn.close(); return jsonify({'error': '已作废申请不可修改'}), 400
    items = d.get('items') or []
    if not items: items = [dict(i) for i in conn.execute("SELECT * FROM request_items WHERE req_id=?", (rid,)).fetchall()]
    total = sum(float(i.get('quantity',1)) * float(i.get('estimated_price',0)) for i in items)
    conn.execute("UPDATE purchase_requests SET purpose=?, dept=?, budget_code=?, target_date=?, remark=?, total_estimated=?, status='待审批', rejected_reason='', updated_at=? WHERE id=?",
                 (d.get('purpose', pr['purpose']), d.get('dept', pr['dept']), d.get('budget_code', pr['budget_code']),
                  d.get('target_date', pr['target_date']), d.get('remark', pr['remark']), total, now(), rid))
    conn.execute("DELETE FROM request_items WHERE req_id=?", (rid,))
    for it in items:
        tp = float(it.get('quantity',1)) * float(it.get('estimated_price',0))
        conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,estimated_price,total_price,remark,category,brand_param,arrival_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, it.get('item_name',''), it.get('spec',''), it.get('unit','个'), float(it.get('quantity',1)),
                      float(it.get('estimated_price',0)), tp, it.get('remark',''),
                      it.get('category',''), it.get('brand_param',''), it.get('arrival_date','')))
    conn.execute("DELETE FROM approval_instances WHERE biz_type='purchase_request' AND biz_id=?", (rid,))
    conn.execute("DELETE FROM dingtalk_instances WHERE biz_type='purchase_request' AND biz_id=?", (rid,))
    conn.commit()
    create_approvals('purchase_request', rid, total)
    start_instances('purchase_request', rid)
    conn.close()
    log(session['user_name'], '修改采购申请', f'申请#{rid} 重新进入审批')
    return jsonify({'success':True})

# ============================================================
# ── ORDERS ──
# ============================================================
@app.route('/api/orders')
@login_required
def api_orders():
    conn = db(); rows = conn.execute("SELECT * FROM purchase_orders ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        cnt = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM order_items WHERE order_id=?", (r['id'],)).fetchone()
        d['item_count'] = cnt[0] or 1
        d['total_qty'] = cnt[1] or r['quantity']
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/orders/<int:oid>')
@login_required
def api_order(oid):
    conn = db()
    o = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
    approvals = conn.execute("SELECT * FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=? ORDER BY level_no", (oid,)).fetchall()
    pcs = conn.execute("SELECT * FROM price_comparisons WHERE order_id=?", (oid,)).fetchall()
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()
    conn.close()
    return jsonify({'order':dict_row(o),'items':[dict_row(i) for i in items],'approvals':[dict_row(a) for a in approvals],'comparisons':[dict_row(p) for p in pcs]})

@app.route('/api/order_items/<int:iid>/status', methods=['POST'])
@login_required
def api_update_order_item_status(iid):
    """V11.78: 更新订单物品状态"""
    d = request.json
    new_status = d.get('status', '未联系')
    conn = db()
    item = conn.execute("SELECT * FROM order_items WHERE id=?", (iid,)).fetchone()
    if not item:
        conn.close()
        return jsonify({'error': '物品不存在'}), 404
    conn.execute("UPDATE order_items SET status=?, updated_at=? WHERE id=?", (new_status, now(), iid))
    conn.commit()
    conn.close()
    log(session['user_name'], '订单物品状态', '订单物品#%d: %s → %s' % (iid, item['status'] or '未联系', new_status))
    return jsonify({'success': True})

@app.route('/api/orders', methods=['POST'])
@login_required
def api_create_order():
    """同一批下单商品为一张订单: 订单头(汇总) + order_items 明细行; 一次审批"""
    d = request.json
    conn = db()
    tm = (d.get('trade_mode') or '货到付款').strip() or '货到付款'
    # V11.3: 交易模式支持自定义, 不局限于 货到付款/先款后货
    items = d.get('items') or []
    if not items and d.get('item_name'):
        items = [{'item_name': d.get('item_name'), 'spec': d.get('spec'), 'unit': d.get('unit','个'),
                  'quantity': d.get('quantity',1), 'price': d.get('price',0), 'tax_rate': d.get('tax_rate',13)}]
    if not items:
        conn.close(); return jsonify({'error': '请至少添加一个商品'}), 400
    # 防重复下单: 从申请导入时(req_id已传), 校验该申请存在且未生成过订单
    req_id = d.get('req_id')
    if req_id:
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (req_id,)).fetchone()
        if not pr:
            conn.close(); return jsonify({'error': '来源申请不存在'}), 400
        if pr['status'] != '已通过':
            conn.close(); return jsonify({'error': f'来源申请当前状态({pr["status"]})不可下单'}), 400
        if conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (req_id,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该申请已下单，请勿重复下单'}), 400
    no = gen_no('CG', 'purchase_orders', 'order_no', conn)
    rows = []
    total_qty = 0.0; grand_amt = 0.0; grand_tax = 0.0; grand_total = 0.0
    for it in items:
        qty = float(it.get('quantity',1) or 1)
        price = float(it.get('price',0) or 0)
        tr = float(it.get('tax_rate', d.get('tax_rate',13)) or 13)
        amt = qty*price; tax = amt*tr/100
        total_qty += qty; grand_amt += amt; grand_tax += tax; grand_total += amt+tax
        rows.append((it.get('item_name',''), it.get('spec','') or '', it.get('unit','个') or '个', qty, price, amt, tr, tax, amt+tax))
    first = rows[0]
    conn.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,
        supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (no, d.get('req_id'), first[0], first[1], total_qty, first[2], first[4], grand_amt, first[6], grand_tax, grand_total,
         d.get('supplier',''), d.get('requester',''), d.get('category','后勤类'), session['user_name'], session['user_id'],
         d.get('target_date'), tm, d.get('remark',''), 1 if d.get('urgent') else 0,
         json.dumps(d.get('attachments') or [], ensure_ascii=False)))
    oid = conn.execute("SELECT id FROM purchase_orders WHERE order_no=?", (no,)).fetchone()[0]
    for r in rows:
        conn.execute("INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,amount,tax_rate,tax_amount,total_amount,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (oid, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], ''))
    rno = None
    if tm == '货到付款':
        # 整批商品一张入库单, 待【确认验收入库】批量转入正式库存
        # V11.31: 自动带出部门(申请单链)
        _dept = ''
        try:
            if d.get('req_id'):
                _pr = conn.execute("SELECT dept FROM purchase_requests WHERE id=?", (d.get('req_id'),)).fetchone()
                if _pr and _pr['dept']:
                    _dept = _pr['dept']
        except Exception:
            pass
        rno = gen_no('RK', 'receivings', 'receive_no', conn)
        conn.execute("INSERT INTO receivings(receive_no,delivery_id,order_id,item_name,spec,quantity,unit,qualified_qty,status,received_at,remark,dept) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (rno, None, oid, first[0], first[1], total_qty, first[2], 0, '待入库', now(), '货到付款: 下单后自动进入入库板块(整批%d项)' % len(rows), _dept))
    conn.commit()
    create_approvals('purchase_order', oid, grand_total)   # 一张订单一次审批
    start_instances('purchase_order', oid)
    conn.close()
    log(session['user_name'], '创建采购订单', '%s 共%d项商品 ¥%.0f' % (no, len(rows), grand_total))
    return jsonify({'success':True, 'order_no': no, 'id': oid, 'receive_no': rno,
                    'total_qty': total_qty, 'total_amount': grand_total, 'item_count': len(rows)})

@app.route('/api/orders/<int:oid>/submit', methods=['POST'])
@login_required
def api_order_submit(oid):
    """V11.3: 草稿订单提交审批 — 确认商家信息无误后, 创建审批实例并向上审批"""
    conn = db()
    o = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
    if not o:
        conn.close(); return jsonify({'error': '订单不存在'}), 404
    if o['status'] != '草稿':
        conn.close(); return jsonify({'error': '仅草稿状态的订单可提交审批'}), 400
    if conn.execute("SELECT COUNT(*) FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=? AND status='pending'", (oid,)).fetchone()[0] > 0:
        conn.close(); return jsonify({'error': '该订单已在审批中'}), 400
    conn.execute("UPDATE purchase_orders SET status='待审批', updated_at=? WHERE id=?", (now(), oid))
    conn.commit()
    amount = float(o['total_amount'] or 0)
    create_approvals('purchase_order', oid, amount)
    try:
        start_instances('purchase_order', oid)
    except Exception as e:
        print('order submit start_instances err:', e)
    conn.close()
    log(session['user_name'], '订单提交审批', '%s 由草稿提交审批 ¥%.0f' % (o['order_no'], amount))
    return jsonify({'success': True, 'order_no': o['order_no'], 'status': '待审批',
                    'message': '订单已提交审批，审批通过后自动进入后续流程'})

# ============================================================
# ── NOTIFICATIONS ──
# ============================================================
@app.route('/api/notifications')
@login_required
def api_notifications():
    conn = db()
    rows = conn.execute("SELECT * FROM notifications WHERE user_id=? AND is_read=0 ORDER BY id DESC LIMIT 20", (session['user_id'],)).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def api_read_notifications():
    conn = db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    conn.commit(); conn.close()
    return jsonify({'success':True})

# ============================================================
# ── STATS ──
# ============================================================
@app.route('/api/reset-db', methods=['POST'])
def api_reset_db():
    conn = db()
    conn.executescript("""
        PRAGMA writable_schema=ON;
        DELETE FROM sqlite_master WHERE type='table';
        PRAGMA writable_schema=OFF;
        VACUUM;
    """)
    conn.close()
    init_db()
    return jsonify({'success':True,'message':'数据库已重置'})

@app.route('/api/stats')
@login_required
def api_stats():
    conn = db()
    stats = {
        'pending_approvals': conn.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0],
        'active_requests': conn.execute("SELECT COUNT(*) FROM purchase_requests WHERE status='待审批'").fetchone()[0],
        'active_orders': conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE status NOT IN ('已完成','已关闭','已挂账')").fetchone()[0],
        'stock_warnings': conn.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0],
        'total_purchase': conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders").fetchone()[0],
        'overdue_items': conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE target_date<date('now') AND status NOT IN ('已完成','已关闭')").fetchone()[0],
    }
    conn.close()
    return jsonify(stats)

# ============================================================
# V55 ── 首页工作台聚合接口: 快捷导航/审批专区/预警中心/数据看板
# ============================================================
@app.route('/api/dashboard')
@login_required
def api_dashboard():
    role = session['user_role']; uid = session['user_id']
    can_price = role in ('系统管理员', '财务', '分管领导', '总经理')
    def pv(v):  # 财务数据脱敏: 无权限返回None
        return round(float(v or 0), 2) if can_price else None
    c = db()
    m = datetime.date.today().strftime('%Y-%m')
    # ── ① 指标卡 ──
    month_purchase = c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE created_at LIKE ?", (m + '%',)).fetchone()[0]
    pending_in = c.execute("SELECT COUNT(*) FROM receivings WHERE status='待入库'").fetchone()[0]
    pending_appr = c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0]
    urgent_cnt = 0
    for t in ('purchase_requests', 'purchase_orders', 'contracts', 'payment_requests'):
        urgent_cnt += c.execute("SELECT COUNT(*) FROM %s WHERE urgent=1 AND status IN ('待审批','审批通过','已通过','执行中')" % t).fetchone()[0]
    overdue_pay_amt = c.execute("SELECT COALESCE(SUM(amount),0) FROM payment_requests WHERE status IN ('待审批','已通过') AND expect_pay_date!='' AND expect_pay_date<date('now')").fetchone()[0]
    stock_low_cnt = c.execute("SELECT COUNT(*) FROM inventory WHERE safe_stock>0 AND quantity<=safe_stock").fetchone()[0]
    # 55.docx看板补全: 待付款总额/订单完成率/库存总量/超储数量/重点供应商占比
    pending_pay_amt = c.execute("SELECT COALESCE(SUM(amount),0) FROM payment_requests WHERE status IN ('待审批','已通过')").fetchone()[0]
    done_cnt = c.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('已完成','已入库','已核销')").fetchone()[0]
    total_cnt = c.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0]
    stock_total_qty = c.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory").fetchone()[0]
    stock_over_cnt = c.execute("SELECT COUNT(*) FROM inventory WHERE max_stock>0 AND quantity>=max_stock").fetchone()[0]
    top_sups = c.execute("SELECT supplier, SUM(total_amount) amt FROM purchase_orders WHERE supplier!='' GROUP BY supplier ORDER BY amt DESC LIMIT 5").fetchall()
    kpi = {
        'month_purchase': pv(month_purchase), 'pending_in': pending_in,
        'pending_appr': pending_appr, 'urgent_cnt': urgent_cnt,
        'overdue_pay': pv(overdue_pay_amt), 'stock_low': stock_low_cnt,
        'pending_pay': pv(pending_pay_amt),
        'order_complete_rate': round(done_cnt*100.0/total_cnt, 1) if total_cnt else 0,
        'stock_total': stock_total_qty, 'stock_over': stock_over_cnt,
        'top_suppliers': [{'name': r['supplier'], 'amount': pv(r['amt'])} for r in top_sups],
        'can_price': can_price,
    }
    # ── ② 审批消息专区 ──
    def biz_no_expr():
        return "CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.req_no FROM purchase_requests pr WHERE pr.id=ai.biz_id) WHEN ai.biz_type='purchase_order' THEN (SELECT po.order_no FROM purchase_orders po WHERE po.id=ai.biz_id) WHEN ai.biz_type='contract' THEN (SELECT ct.contract_no FROM contracts ct WHERE ct.id=ai.biz_id) WHEN ai.biz_type='credit' THEN (SELECT cn.credit_no FROM credit_notes cn WHERE cn.id=ai.biz_id) WHEN ai.biz_type='payment' THEN (SELECT pp.payment_no FROM payment_requests pp WHERE pp.id=ai.biz_id) WHEN ai.biz_type='receiving' THEN (SELECT rv.receive_no FROM receivings rv WHERE rv.id=ai.biz_id) WHEN ai.biz_type='requisition' THEN (SELECT rq.req_no FROM requisitions rq WHERE rq.id=ai.biz_id) END"
    def biz_name_expr():
        return "CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.purpose FROM purchase_requests pr WHERE pr.id=ai.biz_id) WHEN ai.biz_type='purchase_order' THEN (SELECT po.item_name FROM purchase_orders po WHERE po.id=ai.biz_id) WHEN ai.biz_type='contract' THEN (SELECT ct.contract_name FROM contracts ct WHERE ct.id=ai.biz_id) WHEN ai.biz_type='credit' THEN (SELECT cn.item_name FROM credit_notes cn WHERE cn.id=ai.biz_id) WHEN ai.biz_type='payment' THEN (SELECT pp.payment_reason FROM payment_requests pp WHERE pp.id=ai.biz_id) WHEN ai.biz_type='receiving' THEN (SELECT rv.item_name FROM receivings rv WHERE rv.id=ai.biz_id) WHEN ai.biz_type='requisition' THEN (SELECT rq.item_name FROM requisitions rq WHERE rq.id=ai.biz_id) END"
    def urgent_expr():
        return "(CASE WHEN ai.biz_type='purchase_request' THEN (SELECT pr.urgent FROM purchase_requests pr WHERE pr.id=ai.biz_id) WHEN ai.biz_type='purchase_order' THEN (SELECT po.urgent FROM purchase_orders po WHERE po.id=ai.biz_id) WHEN ai.biz_type='contract' THEN (SELECT ct.urgent FROM contracts ct WHERE ct.id=ai.biz_id) WHEN ai.biz_type='payment' THEN (SELECT pp.urgent FROM payment_requests pp WHERE pp.id=ai.biz_id) ELSE 0 END)"
    my_pending = c.execute("""SELECT ai.*, %s as biz_no, %s as biz_name, %s as urgent
        FROM approval_instances ai WHERE ai.status='pending'
        AND (ai.role=? OR (ai.role='部门负责人' AND ? IN ('部门负责人','系统管理员')))
        ORDER BY %s DESC, ai.id DESC LIMIT 30""" % (biz_no_expr(), biz_name_expr(), urgent_expr(), urgent_expr()),
        (role, role)).fetchall()
    my_urgent = [dict_row(x) for x in my_pending if x['urgent']]
    i_started = c.execute("""SELECT * FROM (
            SELECT '申请' bt, req_no no, purpose name, status, created_at FROM purchase_requests WHERE requester_id=?
            UNION ALL SELECT '订单', order_no, item_name, status, created_at FROM purchase_orders WHERE owner_id=?
            UNION ALL SELECT '合同', contract_no, contract_name, status, created_at FROM contracts WHERE remark LIKE ?
            UNION ALL SELECT '付款', payment_no, payment_reason, status, created_at FROM payment_requests WHERE supplier!=''
        ) ORDER BY created_at DESC LIMIT 8""", (uid, uid, '%%%s%%' % session['user_name'])).fetchall()
    i_done = c.execute("""SELECT biz_type, biz_id, role, status, comment, processed_at FROM approval_instances
        WHERE approver_id=? AND status IN ('approved','rejected') ORDER BY processed_at DESC LIMIT 8""", (uid,)).fetchall()
    rejected = c.execute("""SELECT biz_type, biz_id, COUNT(*) cnt, MAX(comment) last_comment FROM approval_instances
        WHERE status='rejected' GROUP BY biz_type, biz_id ORDER BY cnt DESC LIMIT 8""").fetchall()
    # ── ③ 预警消息中心 ──
    alerts = c.execute("SELECT * FROM alert_items WHERE status='pending' ORDER BY CASE level WHEN 'red' THEN 0 WHEN 'orange' THEN 1 ELSE 2 END, id DESC LIMIT 30").fetchall()
    # V11.60: 询价超3天未完成 → 动态加入预警(避免询价卡死没人管)
    try:
        inq_stale = c.execute("""SELECT id, inq_no, title, created_at FROM inquiries
            WHERE status IN ('询价中') AND created_at < datetime('now','localtime','-3 days')
            ORDER BY created_at""").fetchall()
        for q in inq_stale:
            days = max(1, int((datetime.datetime.now() - datetime.datetime.strptime(q['created_at'][:19], '%Y-%m-%d %H:%M:%S')).total_seconds() / 86400))
            alerts.insert(0, {
                'id': 0, 'alert_type': '询价超时', 'level': 'orange',
                'title': f"询价单 {q['inq_no']}",
                'content': f"已发起{days}天未完成, 请及时跟进商家报价/选中",
                'biz_type': 'inquiry', 'biz_id': q['id'], 'created_at': q['created_at'],
                'status': 'pending', 'link': f"sw('inquiries')",
            })
    except Exception:
        pass
    # ── ④ 数据看板 ──
    trend = []
    for i in range(5, -1, -1):
        d0 = datetime.date.today().replace(day=1) - datetime.timedelta(days=31 * i)
        mm = d0.strftime('%Y-%m')
        s = c.execute("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE created_at LIKE ?", (mm + '%',)).fetchone()[0]
        trend.append({'month': mm, 'amount': round(s or 0, 2)})
    cat_ratio = c.execute("""SELECT po.category, COALESCE(SUM(po.total_amount),0) amt FROM purchase_orders po
        WHERE po.total_amount>0 GROUP BY po.category ORDER BY amt DESC LIMIT 8""").fetchall()
    in_total = c.execute("SELECT COUNT(*) FROM receivings WHERE status='已入库'").fetchone()[0]
    in_ontime = 0
    if in_total:
        try: c.execute("ALTER TABLE receivings ADD COLUMN completed_at TEXT")
        except Exception: pass
        in_ontime = c.execute("""SELECT COUNT(*) FROM receivings r JOIN purchase_orders po ON r.order_id=po.id
            WHERE r.status='已入库' AND po.target_date!='' AND (r.completed_at IS NULL OR r.completed_at<=po.target_date)""").fetchone()[0]
    ontime_rate = round(in_ontime * 100.0 / in_total, 1) if in_total else None
    avg_h = c.execute("""SELECT AVG((julianday(processed_at)-julianday(created_at))*24) FROM approval_instances
        WHERE status IN ('approved','rejected') AND processed_at IS NOT NULL""").fetchone()[0]
    c.close()
    return jsonify({
        'kpi': kpi,
        'my_pending': [dict_row(x) for x in my_pending],
        'my_urgent': my_urgent,
        'i_started': [dict_row(x) for x in i_started],
        'i_done': [dict_row(x) for x in i_done],
        'rejected': [dict_row(x) for x in rejected],
        'alerts': [dict_row(x) for x in alerts],
        'trend': trend,
        'cat_ratio': [dict_row(x) for x in cat_ratio],
        'ontime_rate': ontime_rate, 'approve_avg_hours': round(float(avg_h), 1) if avg_h else None,
    })

# ============================================================
# ── COMMON APIs (reused from v3) ──
# ============================================================
@app.route('/api/categories')
@login_required
def api_categories():
    conn = db(); rows = conn.execute("SELECT * FROM categories").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/items')
@login_required
def api_items():
    conn = db(); rows = conn.execute("SELECT i.*,c.name as cat_name FROM items i LEFT JOIN categories c ON i.cat_code=c.code").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/suppliers')
@login_required
def api_suppliers():
    conn = db(); rows = conn.execute("SELECT * FROM suppliers").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/suppliers', methods=['POST'])
@admin_required
def api_create_supplier():
    """需求44-基础档案: 供应商完整档案(含开户行/账号, 供合同自动填充乙方信息)"""
    d = request.json
    c = db()
    if c.execute("SELECT 1 FROM suppliers WHERE name=?", (d.get('name', ''),)).fetchone():
        c.close(); return jsonify({'error': '供应商已存在'}), 400
    c.execute("""INSERT INTO suppliers(name,contact,phone,category,level,bank,account,tax_id,invoice_type,rating,status)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (d.get('name', ''), d.get('contact', ''), d.get('phone', ''), d.get('category', ''), d.get('level', '一般供应商'),
         d.get('bank', ''), d.get('account', ''), d.get('tax_id', ''), d.get('invoice_type', '增值税专用发票'),
         d.get('rating', 4.0), d.get('status', '正常')))
    c.commit(); c.close()
    log(session['user_name'], '新增供应商', d.get('name', ''))
    return jsonify({'success': True})

@app.route('/api/suppliers/<int:sid>', methods=['POST'])
@admin_required
def api_update_supplier(sid):
    d = request.json
    c = db()
    c.execute("""UPDATE suppliers SET name=?,contact=?,phone=?,category=?,level=?,bank=?,account=?,tax_id=?,invoice_type=?,rating=?,status=? WHERE id=?""",
        (d.get('name', ''), d.get('contact', ''), d.get('phone', ''), d.get('category', ''), d.get('level', '一般供应商'),
         d.get('bank', ''), d.get('account', ''), d.get('tax_id', ''), d.get('invoice_type', '增值税专用发票'),
         d.get('rating', 4.0), d.get('status', '正常'), sid))
    c.commit(); c.close()
    log(session['user_name'], '编辑供应商', f'#{sid}')
    return jsonify({'success': True})

# ============================================================
# ── 三方询价 (V11.0) ──
# 业务流: 采购申请审批通过 → 发起三方询价(最多3家) → 商家免登录报价(/inq/<token>)
#         → 比价选中一家 → 自动生成采购订单(进订单审批)
# ============================================================
@app.route('/api/inquiries')
@login_required
def api_inquiries():
    """询价单列表(含家数/已报价数/最低报价/审批状态)"""
    conn = db()
    rows = conn.execute("""
        SELECT i.*, pr.req_no, pr.purpose, pr.dept,
            (SELECT COUNT(*) FROM inquiry_suppliers s WHERE s.inquiry_id=i.id) AS sup_count,
            (SELECT COUNT(*) FROM inquiry_suppliers s WHERE s.inquiry_id=i.id AND s.quote_price>0) AS quoted_count,
            (SELECT MIN(s.quote_price) FROM inquiry_suppliers s WHERE s.inquiry_id=i.id AND s.quote_price>0) AS min_price,
            (SELECT status FROM inquiry_approvals WHERE inquiry_id=i.id ORDER BY id DESC LIMIT 1) AS approval_status
        FROM inquiries i LEFT JOIN purchase_requests pr ON i.req_id=pr.id
        ORDER BY i.id DESC LIMIT 100
    """).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/inquiries/next_no')
@login_required
def api_inquiries_next_no():
    conn = db()
    no = gen_no('XJ', 'inquiries', 'inq_no', conn)
    conn.close()
    return jsonify({'inq_no': no})

@app.route('/api/inquiries/eligible')
@login_required
def api_inquiries_eligible():
    """可询价申请列表: 已通过 且 未询价过 且 未下单"""
    conn = db()
    rows = conn.execute("""
        SELECT pr.*,
            (SELECT COUNT(*) FROM request_items ri WHERE ri.req_id=pr.id) AS item_count,
            (SELECT SUM(COALESCE(ri.total_price,0)) FROM request_items ri WHERE ri.req_id=pr.id) AS item_total
        FROM purchase_requests pr
        WHERE pr.status='已通过'
          AND NOT EXISTS (SELECT 1 FROM inquiries i WHERE i.req_id=pr.id AND i.status!='已取消')
          AND NOT EXISTS (SELECT 1 FROM purchase_orders po WHERE po.req_id=pr.id)
        ORDER BY pr.id DESC LIMIT 50
    """).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/inquiries', methods=['POST'])
@login_required
def api_create_inquiry():
    """发起三方询价: 选中已通过申请 + 最多3家供应商(每家生成免登录报价链接)"""
    d = request.json
    req_id = d.get('req_id')
    suppliers = d.get('suppliers') or []
    if not req_id:
        return jsonify({'error': '请选择要询价的申请'}), 400
    if not suppliers or len(suppliers) > 3:
        return jsonify({'error': '请添加2-3家询价供应商'}), 400
    # V11.77: 允许2-3家询价，少于2家不允许
    valid_names = [x for x in suppliers if (x.get('name') or '').strip()]
    if len(valid_names) < 2:
        return jsonify({'error': '询价供应商需选择2家及以上，方可发起询价'}), 400
    conn = db()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (req_id,)).fetchone()
    if not pr:
        conn.close(); return jsonify({'error': '申请不存在'}), 400
    if pr['status'] != '已通过':
        conn.close(); return jsonify({'error': '申请当前状态(%s)不可询价' % pr['status']}), 400
    if conn.execute("SELECT COUNT(*) FROM inquiries WHERE req_id=? AND status!='已取消'", (req_id,)).fetchone()[0] > 0:
        conn.close(); return jsonify({'error': '该申请已发起过询价'}), 400
    if conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (req_id,)).fetchone()[0] > 0:
        conn.close(); return jsonify({'error': '该申请已下单，无需询价'}), 400
    no = gen_no('XJ', 'inquiries', 'inq_no', conn)
    title = (pr['purpose'] or '')[:80]
    # V11.24: 报价截止时间 — 前端传 deadline(YYYY-MM-DD), 不传默认7天后
    import datetime as _dt
    try:
        dl = (d.get('deadline') or '').strip()
        _dl = _dt.datetime.strptime(dl, '%Y-%m-%d') if dl else (_dt.date.today() + _dt.timedelta(days=7))
        deadline = _dl.strftime('%Y-%m-%d')
    except Exception:
        deadline = (_dt.date.today() + _dt.timedelta(days=7)).strftime('%Y-%m-%d')
    conn.execute("INSERT INTO inquiries(inq_no,req_id,title,purpose,status,deadline,created_by) VALUES(?,?,?,?,?,?,?)",
                 (no, req_id, title, pr['purpose'], '询价中', deadline, session['user_name']))
    iid = conn.execute("SELECT id FROM inquiries WHERE inq_no=?", (no,)).fetchone()[0]
    for s in suppliers[:3]:
        name = (s.get('name') or '').strip()
        if not name:
            continue
        token = uuid.uuid4().hex
        conn.execute("INSERT INTO inquiry_suppliers(inquiry_id,supplier_name,contact,phone,token) VALUES(?,?,?,?,?)",
                     (iid, name, s.get('contact', ''), s.get('phone', ''), token))
    conn.commit(); conn.close()
    log(session['user_name'], '发起三方询价', '%s 申请#%s %d家' % (no, req_id, len([x for x in suppliers if (x.get('name') or '').strip()])))
    return jsonify({'success': True, 'inq_no': no, 'id': iid})

@app.route('/api/inquiries/<int:iid>')
@login_required
def api_inquiry_detail(iid):
    """询价单详情: 申请信息 + 物品明细 + 供应商报价对比 + 品牌分析"""
    conn = db()
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not i:
        conn.close(); return jsonify({'error': '询价单不存在'}), 404
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone()
    items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (i['req_id'],)).fetchall()
    sups = conn.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=? ORDER BY (quote_price=0), quote_price", (iid,)).fetchall()
    conn.close()
    out = dict_row(i)
    out['request'] = dict_row(pr)
    out['items'] = [dict_row(r) for r in items]
    # 添加品牌分析
    supplier_list = []
    for s in sups:
        sd = dict_row(s)
        brand_info = search_brand_info(sd.get('supplier_name', ''), '')
        sd['brand_analysis'] = brand_info
        supplier_list.append(sd)
    out['suppliers'] = supplier_list
    return jsonify(out)

@app.route('/inq/<token>')
def inquiry_vendor_page(token):
    """商家免登录报价页(无需登录, 链接发供应商)"""
    def _today_str():
        import datetime as _d
        return _d.date.today().strftime('%Y-%m-%d')
    conn = db()
    s = conn.execute("SELECT * FROM inquiry_suppliers WHERE token=?", (token,)).fetchone()
    if not s:
        conn.close()
        return '<h3 style="font-family:sans-serif;text-align:center;margin-top:80px;color:#999">❓ 报价链接无效或已失效</h3>'
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (s['inquiry_id'],)).fetchone()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone() if i else None
    items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (i['req_id'],)).fetchall() if i else []
    conn.close()
    item_rows = ''.join(
        '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            esc_html(it['item_name']), esc_html(it['spec'] or ''),
            str(it['quantity']) + esc_html(it['unit'] or '个'), '¥%.0f' % (it['total_price'] or 0))
        for it in items)
    if s['quote_price'] and s['quote_price'] > 0:
        body = ('<div style="max-width:520px;margin:60px auto;background:#fff;border-radius:12px;padding:32px;'
                'box-shadow:0 4px 24px rgba(0,0,0,.08);font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif">'
                '<h2 style="margin:0 0 4px;color:#1f6feb">✅ 报价已提交</h2>'
                '<p style="color:#666;margin:0 0 20px">感谢 %s 参与本次询价，报价 ¥%.0f 已收到，等待采购方比价结果。</p>'
                '<a href="%s" style="color:#1f6feb;font-size:13px">← 返回查看/修改报价</a></div>') % (
                    esc_html(s['supplier_name']), s['quote_price'], request.url.replace('http://', 'https://') if request.url.startswith('http://') else request.url)
    else:
        # V11.24: 截止时间状态
        _deadline = (i['deadline'] or '') if i else ''
        _dl_txt = ''
        _dl_hint = ''
        if _deadline:
            _dl_txt = '<div style="background:%s;border-radius:8px;padding:10px 14px;font-size:13px;margin-bottom:12px;border:1px solid %s"><b>⏰ 报价截止：%s</b>%s</div>' % (
                '#fff3cd' if _deadline >= _today_str() else '#f8d7da',
                '#ffeeba' if _deadline >= _today_str() else '#f5c6cb',
                esc_html(_deadline),
                '' if _deadline >= _today_str() else '<br><span style="color:#c0392b">⚠️ 该询价已截止，无法继续报价</span>')
        else:
            _dl_hint = ''
        # V11.43: 行明细报价 — 每行 单价+总价(自动算)+交付日期+质保时间+备注, 自由填写
        _rows_html = []
        for idx, it in enumerate(items):
            _rows_html.append(
                '<tr>'
                '<td style="padding:6px 8px;text-align:left;border-bottom:1px solid #eef">%s</td>'
                '<td style="padding:6px 8px;text-align:left;border-bottom:1px solid #eef;color:#888;font-size:12px">%s</td>'
                '<td style="padding:6px 8px;text-align:left;border-bottom:1px solid #eef;white-space:nowrap">%s%s</td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef"><input type="number" min="0" step="0.01" placeholder="单价" '
                'oninput="calc()" data-q="%s" id="up%d" style="width:64px;padding:5px 6px;border:1px solid #d0d7e2;border-radius:6px;font-size:13px;text-align:right"></td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef;text-align:right;font-weight:600;color:#2e7d32;white-space:nowrap">¥<span id="ut%d">0.00</span></td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef"><input placeholder="如7天" id="dl%d" style="width:52px;padding:5px 6px;border:1px solid #d0d7e2;border-radius:6px;font-size:12px"></td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef"><input placeholder="如3个月" id="wr%d" style="width:56px;padding:5px 6px;border:1px solid #d0d7e2;border-radius:6px;font-size:12px"></td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef"><input placeholder="品牌" id="br%d" style="width:80px;padding:5px 6px;border:1px solid #d0d7e2;border-radius:6px;font-size:12px"></td>'
                '<td style="padding:6px 8px;border-bottom:1px solid #eef"><input placeholder="备注" id="rm%d" style="width:64px;padding:5px 6px;border:1px solid #d0d7e2;border-radius:6px;font-size:12px"></td>'
                '</tr>' % (
                    esc_html(it['item_name']), esc_html(it['spec'] or ''),
                    str(it['quantity']) + esc_html(it['unit'] or '个'),
                    '<span style="color:#bbb;font-size:11px">(参考¥%.0f)</span>' % ((it['total_price'] or 0) / it['quantity'] if it['quantity'] else 0),
                    str(it['quantity']), idx, idx, idx, idx, idx, idx))
        _item_rows = ''.join(_rows_html)
        body = ('<div style="max-width:860px;margin:40px auto;background:#fff;border-radius:12px;padding:28px;'
                'box-shadow:0 4px 24px rgba(0,0,0,.08);font-family:-apple-system,Segoe UI,Microsoft YaHei,sans-serif">'
                '<h2 style="margin:0 0 4px;color:#1f6feb">📋 采购询价单</h2>'
                '<p style="color:#888;font-size:13px;margin:0 0 14px">尊敬的 %s，请逐项填写单价，总价自动计算；交付日期/质保时间按实际填写</p>%s'
                '<div style="background:#f5f8ff;border-radius:8px;padding:12px 16px;font-size:13px;margin-bottom:14px">'
                '<b>%s</b><br><span style="color:#888">询价编号：%s</span></div>'
                '<div style="overflow-x:auto"><table style="width:100%%;border-collapse:collapse;font-size:13px;margin-bottom:10px;min-width:700px">'
                '<tr style="background:#f5f8ff"><th style="padding:6px 8px;text-align:left">物资名称</th>'
                '<th style="padding:6px 8px;text-align:left">规格</th><th style="padding:6px 8px;text-align:left">数量</th>'
                '<th style="padding:6px 8px;text-align:left">单价(元)</th><th style="padding:6px 8px;text-align:left">总价(含税含运)</th>'
                '<th style="padding:6px 8px;text-align:left">交付日期</th><th style="padding:6px 8px;text-align:left">质保时间</th>'
                '<th style="padding:6px 8px;text-align:left">品牌</th><th style="padding:6px 8px;text-align:left">备注</th></tr>%s</table></div>'
                '<div style="background:#f0faf0;border-radius:8px;padding:10px 14px;font-size:14px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center">'
                '<span style="color:#2e7d32"><b>报价合计：¥<span id="total">0.00</span></b></span>'
                '<span style="font-size:12px;color:#888">物品较多时，可<a href="javascript:void(0)" onclick="quickFill()" style="color:#1f6feb">💰 填一个总价自动分摊</a></span></div>'
                '<button onclick="sub()" style="width:100%%;padding:12px;background:#1f6feb;color:#fff;border:none;border-radius:8px;font-size:15px;cursor:pointer">提交报价</button>'
                '<div id="msg" style="margin-top:10px;font-size:13px;color:#27ae60;text-align:center"></div>'
                '<script>'
                'function calc(){let t=0;document.querySelectorAll("[id^=up]").forEach((e,i)=>{const q=parseFloat(e.getAttribute("data-q"))||1;const p=parseFloat(e.value)||0;'
                'const st=p*q;t+=st;const u=document.getElementById("ut"+i);if(u)u.textContent=st.toFixed(2)});'
                'document.getElementById("total").textContent=t.toFixed(2)}'
                'function quickFill(){const v=prompt("请输入报价总金额(元):");if(!v||isNaN(v))return;const n=document.querySelectorAll("[id^=up]").length;'
                'const per=parseFloat(v)/n;document.querySelectorAll("[id^=up]").forEach(e=>{e.value=per.toFixed(2)});calc();'
                'alert("已按平均分摊到每行，可再逐行微调")}'
                'async function sub(){const rows=document.querySelectorAll("[id^=up]");const details=[];let ok=false;'
                'rows.forEach((e,i)=>{const p=parseFloat(e.value)||0;if(p>0)ok=true;details.push({unit_price:p,qty:parseFloat(e.getAttribute("data-q"))||1,'
                'delivery:document.getElementById("dl"+i).value||"",warranty:document.getElementById("wr"+i).value||"",'
                'brand:document.getElementById("br"+i).value||"",remark:document.getElementById("rm"+i).value||""})});'
                'if(!ok){alert("请至少填写一项单价");return}'
                'const total=parseFloat(document.getElementById("total").textContent);'
                'const r=await fetch("%s",{method:"POST",headers:{"Content-Type":"application/json"},'
                'body:JSON.stringify({quote_price:total,details,quote_delivery:"",quote_warranty:""})});'
                'const j=await r.json();if(j.success){document.getElementById("msg").textContent="✅ 报价提交成功";setTimeout(()=>location.reload(),800)}'
                'else{alert(j.error||"提交失败")}}</script></div>') % (
                    esc_html(s['supplier_name']), _dl_txt, esc_html(pr['purpose'] if pr else ''), esc_html(i['inq_no']),
                    _item_rows, '/api/inquiry/vendor/%s/quote' % token)
    return body

@app.route('/api/inquiry/vendor/<token>/quote', methods=['POST'])
def inquiry_vendor_quote(token):
    """商家提交报价(免登录)"""
    d = request.json
    details = d.get('details') or []
    # V11.41: 行明细报价时以明细合计为准, 总价可传0; 无明细时走旧逻辑(总价必填)
    price = float(d.get('quote_price') or 0)
    if not details and price <= 0:
        return jsonify({'error': '请填写有效报价金额'}), 400
    conn = db()
    s = conn.execute("SELECT * FROM inquiry_suppliers WHERE token=?", (token,)).fetchone()
    if not s:
        conn.close(); return jsonify({'error': '报价链接无效'}), 404
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (s['inquiry_id'],)).fetchone()
    if not i or i['status'] != '询价中':
        conn.close(); return jsonify({'error': '该询价已结束，无法报价'}), 400
    # V11.24: 报价截止时间检查 — 超过截止日期拒绝报价
    if i['deadline']:
        import datetime as _dt
        _today = _dt.date.today().strftime('%Y-%m-%d')
        if _today > str(i['deadline']):
            conn.close(); return jsonify({'error': '该询价已于 %s 截止，无法继续报价' % i['deadline']}), 400
    # V11.41: 行明细报价(每行单价+备注), 合计=Σ单价×数量; 兼容旧版总价提交
    _final_price = price
    if details:
        _sum = sum(float(x.get('unit_price') or 0) * float(x.get('qty') or 1) for x in details)
        if _sum > 0:
            _final_price = _sum
    conn.execute("UPDATE inquiry_suppliers SET quote_price=?, quote_remark=?, quote_details=?, quote_delivery=?, quote_warranty=?, quote_brand=?, quote_time=? WHERE id=?",
                 (_final_price, (d.get('quote_remark') or '')[:200],
                  json.dumps(details, ensure_ascii=False) if details else '',
                  (d.get('quote_delivery') or '')[:20], (d.get('quote_warranty') or '')[:20],
                  (details[0].get('brand') if details and isinstance(details[0], dict) else '')[:50], now(), s['id']))
    # V11.75: 三家报价完成 → 自动创建采购订单 + 发起钉钉定标审批
    try:
        total_count = conn.execute("SELECT COUNT(*) FROM inquiry_suppliers WHERE inquiry_id=?", (s['inquiry_id'],)).fetchone()[0]
        quoted_count = conn.execute("SELECT COUNT(*) FROM inquiry_suppliers WHERE inquiry_id=? AND quote_price>0", (s['inquiry_id'],)).fetchone()[0]
        if quoted_count >= total_count and total_count > 0:
            i = conn.execute("SELECT * FROM inquiries WHERE id=?", (s['inquiry_id'],)).fetchone()
            if i and i['status'] == '询价中':
                # 自动创建采购订单草稿
                pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone()
                items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (i['req_id'],)).fetchall()
                if pr and items:
                    no = gen_no('CG', 'purchase_orders', 'order_no', conn)
                    # 取最低报价
                    cheapest = conn.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=? AND quote_price>0 ORDER BY quote_price ASC LIMIT 1", (i['id'],)).fetchone()
                    total = float(cheapest['quote_price'] or 0) if cheapest else 0
                    remark = '询价单:%s 商家已报价完成，最低报价¥%.0f(%s)，请领导定标' % (i['inq_no'], total, cheapest['supplier_name'] if cheapest else '待定')
                    conn.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,
                        supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments,status,inquiry_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (no, i['req_id'], i['title'][:50], '', 1, '个', 0, total, 0, 0, total,
                         cheapest['supplier_name'] if cheapest else '待定', i['created_by'], '后勤类', i['created_by'], 1, i['deadline'] or '', '货到付款',
                         remark, 0, json.dumps([], ensure_ascii=False), '草稿', i['id']))
                    oid = conn.execute("SELECT id FROM purchase_orders WHERE order_no=?", (no,)).fetchone()[0]
                    conn.execute("UPDATE inquiries SET status='定标审批中', updated_at=? WHERE id=?", (now(), i['id']))
                    conn.commit()
                    # V11.76: 发起钉钉询价审批(用新模板,领导在钉钉选供应商)
                    try:
                        c2 = db()
                        c2.execute("INSERT INTO inquiry_approvals(inquiry_id, created_at) VALUES(?, ?)", (i['id'], now()))
                        aid = c2.execute("SELECT last_insert_rowid()").fetchone()[0]
                        # 创建approval_instances记录供dt_start_instance使用
                        c2.execute("INSERT INTO approval_instances(biz_type, biz_id, level_no, role, approver, status) VALUES(?, ?, 1, '分管领导', 'xingguo', 'pending')", ('inquiry_approval', aid))
                        c2.commit(); c2.close()
                        # 发起钉钉审批
                        try:
                            dt_start_instance('inquiry_approval', aid)
                            print('[V11.76] 发起询价审批成功: %s, aid=%d' % (i['inq_no'], aid))
                        except Exception as e2:
                            print('[V11.76] 发起询价审批失败: %s' % e2)
                    except Exception as e2:
                        print('[V11.76] 询价审批异常: %s' % e2)
    except Exception as e:
        print('[V11.75] 自动定标异常: %s' % e)
    conn.commit(); conn.close()
    return jsonify({'success': True, 'quote_price': _final_price})

@app.route('/api/inquiries/<int:iid>/submit', methods=['POST'])
@login_required
def api_inquiry_submit(iid):
    """V11.93: 手动提交询价审批（所有供应商报价后）"""
    conn = db()
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not i:
        conn.close()
        return jsonify({'error': '询价单不存在'}), 404
    if i['status'] != '询价中':
        conn.close()
        return jsonify({'error': '该询价已结束'}), 400
    # 检查是否所有供应商都报价
    sups = conn.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=?", (iid,)).fetchall()
    if not sups:
        conn.close()
        return jsonify({'error': '无供应商报价'}), 400
    quoted = [s for s in sups if s['quote_price'] and s['quote_price'] > 0]
    if len(quoted) < 2:
        conn.close()
        return jsonify({'error': f'需至少2家报价，当前{len(quoted)}家'}), 400
    # 创建采购订单草稿
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone()
    items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (i['req_id'],)).fetchall()
    if not pr or not items:
        conn.close()
        return jsonify({'error': '来源申请或明细缺失'}), 400
    # 取最低报价
    cheapest = conn.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=? AND quote_price>0 ORDER BY quote_price ASC LIMIT 1", (iid,)).fetchone()
    total = float(cheapest['quote_price'] or 0) if cheapest else 0
    remark = '询价单:%s 商家已报价完成，最低报价¥%.0f(%s)，请领导定标' % (i['inq_no'], total, cheapest['supplier_name'] if cheapest else '待定')
    conn.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,
        supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments,status,inquiry_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (gen_no('CG', 'purchase_orders', 'order_no', conn), i['req_id'], i['title'][:50], '', 1, '个', 0, total, 0, 0, total,
         cheapest['supplier_name'] if cheapest else '待定', i['created_by'], '后勤类', i['created_by'], 1, i['deadline'] or '', '货到付款',
         remark, 0, json.dumps([], ensure_ascii=False), '草稿', i['id']))
    # 创建询价审批记录
    conn.execute("INSERT INTO inquiry_approvals(inquiry_id, status, created_at) VALUES(?, '审批中', ?)", (iid, now()))
    conn.execute("UPDATE inquiries SET status='定标审批中', updated_at=? WHERE id=?", (now(), iid))
    conn.commit()
    conn.close()
    log(session['user_name'], '提交询价审批', '%s 已提交定标审批' % i['inq_no'])
    return jsonify({'success': True, 'order_no': gen_no('CG', 'purchase_orders', 'order_no', db())})

@app.route('/api/inquiries/<int:iid>/select', methods=['POST'])
@login_required
def api_inquiry_select(iid):
    """定标审批: 领导选定供应商 → 生成采购订单草稿 → 提交审批"""
    d = request.json
    sid = d.get('supplier_id')
    conn = db()
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not i:
        conn.close(); return jsonify({'error': '询价单不存在'}), 404
    if i['status'] != '询价中':
        conn.close(); return jsonify({'error': '该询价已结束'}), 400
    # V11.24: 截止后不允许再选中下单(已截止的询价只能比价查看)
    if i['deadline']:
        import datetime as _dt
        if _dt.date.today().strftime('%Y-%m-%d') > str(i['deadline']):
            conn.close(); return jsonify({'error': '该询价已于 %s 截止，如需下单请重新发起询价' % i['deadline']}), 400
    s = conn.execute("SELECT * FROM inquiry_suppliers WHERE id=? AND inquiry_id=?", (sid, iid)).fetchone()
    if not s:
        conn.close(); return jsonify({'error': '供应商不在该询价单中'}), 400
    if not s['quote_price'] or s['quote_price'] <= 0:
        conn.close(); return jsonify({'error': '该供应商尚未报价'}), 400
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone()
    items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (i['req_id'],)).fetchall()
    if not pr or not items:
        conn.close(); return jsonify({'error': '来源申请或明细缺失'}), 400
    # 生成采购订单: 供应商=选中家, 金额=报价总价按明细参考金额比例分摊
    no = gen_no('CG', 'purchase_orders', 'order_no', conn)
    total = float(s['quote_price'])
    base_sum = sum(float(it['total_price'] or 0) for it in items)
    rows = []
    grand_amt = 0.0
    for idx, it in enumerate(items):
        qty = float(it['quantity'] or 1)
        if base_sum > 0 and idx < len(items) - 1:
            amt = total * (float(it['total_price'] or 0) / base_sum)
        else:
            amt = total - grand_amt  # 最后一行吃余数, 保证合计=报价
        amt = round(amt, 2)
        price = round(amt / qty, 2) if qty else 0
        grand_amt += amt
        rows.append((it['item_name'], it['spec'] or '', it['unit'] or '个', qty, price, amt))
    first = rows[0]
    # 商家详细信息自动填入订单
    contact = (s['contact'] or '').strip()
    phone = (s['phone'] or '').strip()
    quote_remark = (s['quote_remark'] or '').strip()
    quote_delivery = (s['quote_delivery'] or '').strip()
    quote_warranty = (s['quote_warranty'] or '').strip()
    detail_parts = ['三方询价选中: %s 报价¥%.2f' % (s['supplier_name'], total)]
    if contact:
        detail_parts.append('联系人: %s' % contact)
    if phone:
        detail_parts.append('电话: %s' % phone)
    if quote_delivery:
        detail_parts.append('交付日期: %s' % quote_delivery)
    if quote_warranty:
        detail_parts.append('质保时间: %s' % quote_warranty)
    if quote_remark:
        detail_parts.append('备注: %s' % quote_remark)
    detail_parts.append('询价单号: %s' % i['inq_no'])
    remark = '; '.join(detail_parts)
    tm = '货到付款'
    if quote_remark:
        for kw in ('货到付款', '先款后货', '月结', '预付', '现款', '承兑'):
            if kw in quote_remark:
                tm = kw
                break
    # 定标审批: 领导选定后, 订单草稿 + 提交定标审批(必须领导审批通过才能下单)
    settle_type = d.get('settle_type') or '现结'
    conn.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,
        supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (no, i['req_id'], first[0], first[1], sum(r[3] for r in rows), first[2], first[4], grand_amt, 0, 0, total,
         s['supplier_name'], pr['requester'] or '', '后勤类', session['user_name'], session['user_id'],
         pr['target_date'] or '', tm, remark, 0,
         json.dumps([], ensure_ascii=False), '草稿'))
    oid = conn.execute("SELECT id FROM purchase_orders WHERE order_no=?", (no,)).fetchone()[0]
    for r in rows:
        conn.execute("INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,amount,tax_rate,tax_amount,total_amount,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (oid, r[0], r[1], r[2], r[3], r[4], r[5], 0, 0, r[5], ''))
    conn.execute("UPDATE inquiry_suppliers SET is_selected=1 WHERE id=?", (sid,))
    # V11.73: 定标审批通过后直接生效(领导已选定供应商,无需再次审批)
    conn.execute("UPDATE purchase_orders SET status='已通过', settle_type=?, updated_at=? WHERE id=?", (settle_type, now(), oid))
    conn.execute("UPDATE inquiries SET status='已生成订单', selected_supplier_id=?, updated_at=? WHERE id=?", (sid, now(), iid))
    conn.commit()
    log(session['user_name'], '询价定标', '%s → 订单%s(供应商:%s ¥%.0f,已生效)' % (i['inq_no'], no, s['supplier_name'], total))
    return jsonify({'success': True, 'order_no': no, 'id': oid, 'total_amount': total, 'status': '已通过',
                    'message': '✅ 定标通过，订单已生效'})

@app.route('/api/inquiries/<int:iid>/export')
@login_required
def api_inquiry_export(iid):
    """V11.1 询价单导出Excel: 基础信息+物料明细+全量供应商报价+比价统计+选中高亮+决策备注"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

    conn = db()
    i = conn.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not i:
        conn.close(); return jsonify({'error': '询价单不存在'}), 404
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (i['req_id'],)).fetchone()
    items = conn.execute("SELECT * FROM request_items WHERE req_id=? ORDER BY id", (i['req_id'],)).fetchall()
    sups = [dict(s) for s in conn.execute("SELECT * FROM inquiry_suppliers WHERE inquiry_id=? ORDER BY id", (iid,)).fetchall()]
    conn.close()

    wb = Workbook(); ws = wb.active; ws.title = '询价单'
    thin = Side(style='thin', color='B0B0B0')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    title_font = Font(name='微软雅黑', size=14, bold=True)
    head_font = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
    head_fill = PatternFill('solid', fgColor='2F5597')
    label_font = Font(name='微软雅黑', size=10, bold=True)
    base_font = Font(name='微软雅黑', size=10)
    note_font = Font(name='微软雅黑', size=10, italic=True, color='666666')
    sel_fill = PatternFill('solid', fgColor='FFF2CC')       # 选中行浅黄
    min_font = Font(name='微软雅黑', size=10, bold=True, color='C00000')  # 最低价标红加粗
    wrap = Alignment(vertical='center', wrap_text=True)

    ws.merge_cells('A1:H1')
    ws['A1'] = '采 购 询 价 单'
    ws['A1'].font = title_font; ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30

    info = [
        ('询价单号', i['inq_no'] or '', '询价主题', (i['title'] or i['purpose'] or '')[:60]),
        ('申请单号', pr['req_no'] if pr else '', '采购事由', (pr['purpose'] if pr else '') or (i['purpose'] or '')),
        ('发起部门', (pr['dept'] if pr else '') or '', '发起人', i['created_by'] or ''),
        ('询价发起时间', i['created_at'] or '', '询价状态', i['status'] or ''),
        ('物料/服务项数', str(len(items)) + ' 项', '供应商家数', str(len(sups)) + ' 家'),
    ]
    row = 3
    for left_k, left_v, right_k, right_v in info:
        ws.cell(row, 1, left_k).font = label_font
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=3)
        c = ws.cell(row, 2, left_v); c.font = base_font; c.alignment = wrap
        ws.cell(row, 4, right_k).font = label_font
        ws.merge_cells(start_row=row, start_column=5, end_row=row, end_column=10)
        c = ws.cell(row, 5, right_v); c.font = base_font; c.alignment = wrap
        for col in range(1, 11):
            ws.cell(row, col).border = border
        row += 1

    row += 2
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=10)
    ws.cell(row, 1, '二、供应商报价比价表（逐项对比）').font = head_font
    for col in range(1, 11):
        ws.cell(row, col).fill = head_fill; ws.cell(row, col).border = border
    row += 1
    # V11.52: 逐行三家对比 — 每家3列(单价/总价/备注), 备注独立列跟商家走
    quoted = [s for s in sups if s['quote_price'] and s['quote_price'] > 0]
    # 解析每家行明细(商家按申请明细顺序报价)
    sup_details = []
    for s in sups:
        try:
            sup_details.append(json.loads(s['quote_details']) if s['quote_details'] else None)
        except Exception:
            sup_details.append(None)
    n_sup = len(sups)
    # 表头: 序号/物料/数量/规格 | 每家4列(单价/总价/品牌/备注) | 
    sup_head = ['序号', '物料名称', '数量', '规格型号']
    for s in sups:
        sup_head += [f"{s['supplier_name']} 单价", f"{s['supplier_name']} 总价", f"{s['supplier_name']} 品牌", f"{s['supplier_name']} 备注"]
    col_count = 4 + n_sup * 4
    for ci, h in enumerate(sup_head, 1):
        c = ws.cell(row, ci, h); c.font = head_font; c.fill = head_fill; c.border = border
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[row].height = 30
    row += 1
    # 每物料一行
    total_per_sup = [0.0] * n_sup
    min_font_s = Font(name='微软雅黑', size=10, bold=True, color='C00000')
    for idx, it in enumerate(items, 1):
        qty = float(it['quantity'] or 0)
        vals = [idx, it['item_name'] or '', ('%g' % qty) + (it['unit'] or '个'), it['spec'] or '']
        # 每家: 单价/总价/备注(行明细优先, 无则空)
        row_prices = []  # (unit_price, total)
        for si, s in enumerate(sups):
            # V11.77: 自动搜索品牌信息并附到备注
            brand_info = search_brand_info(s['supplier_name'], '')
            if brand_info:
                s_brand_remark = '%s 品牌分析: 优点-%s | 缺点-%s' % (s['supplier_name'], brand_info.get('优点',''), brand_info.get('缺点',''))
            else:
                s_brand_remark = ''
            det = sup_details[si]
            unit_p = None; total_p = None; remark = ''
            if det and idx - 1 < len(det):
                d_i = det[idx - 1]
                if d_i.get('unit_price') is not None:
                    unit_p = float(d_i.get('unit_price') or 0)
                    total_p = unit_p * qty
                remark = d_i.get('remark') or ''
            # 获取品牌
            brand = ''
            if det and idx - 1 < len(det):
                brand = det[idx - 1].get('brand') or ''
            if unit_p is not None:
                vals += [round(unit_p, 2), round(total_p, 2), brand, remark]
                total_per_sup[si] += total_p
            else:
                vals += ['', '', brand, remark]
            row_prices.append((unit_p, total_p))
        # 最低单价标红
        unit_prices = [p[0] for p in row_prices if p[0] is not None]
        min_unit = min(unit_prices) if unit_prices else None
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row, ci, v); c.border = border
            c.alignment = Alignment(horizontal='center' if ci <= 4 or (ci - 5) % 4 != 0 else 'left', vertical='center', wrap_text=True)
            # 单价列: 最低标红加粗（更明显）
            if unit_prices and min_unit is not None:
                k = (ci - 5) // 4
                if 0 <= k < n_sup and (ci - 5) % 4 == 0:
                    if row_prices[k][0] is not None and abs(row_prices[k][0] - min_unit) < 0.001:
                        c.font = Font(name='微软雅黑', size=11, bold=True, color='C00000')
                        c.fill = PatternFill('solid', fgColor='FFF2CC')
                        continue
            c.font = base_font
        ws.row_dimensions[row].height = 26
        row += 1
    # 合计行
    ws.cell(row, 1, '').border = border
    ws.cell(row, 2, '合计').font = label_font; ws.cell(row, 2).border = border
    for cc in (3, 4):
        ws.cell(row, cc).border = border
    total_min = None
    for si, t in enumerate(total_per_sup):
        ws.cell(row, 5 + si * 4, '').border = border
        ws.cell(row, 6 + si * 4, '').border = border
        c = ws.cell(row, 7 + si * 4, round(t, 2))
        c.border = border; c.font = label_font
        c.alignment = Alignment(horizontal='center', vertical='center')
        if t > 0 and (total_min is None or t < total_min):
            total_min = t
    for si, t in enumerate(total_per_sup):
        if t > 0 and total_min is not None and abs(t - total_min) < 0.001:
            ws.cell(row, 7 + si * 4).font = min_font_s
    # 备注格空
    for si in range(n_sup):
        ws.cell(row, 9 + si * 4, '').border = border
    ws.cell(row, 4 + n_sup * 4 - 1, '★=该项最低价').font = note_font
    ws.cell(row, 4 + n_sup * 4 - 1).border = border
    ws.row_dimensions[row].height = 22
    row += 1
    if not sups:
        for ci in range(1, col_count + 1):
            ws.cell(row, ci, '（暂无供应商）').border = border
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=10)
    ws.cell(row, 1, '三、采购决策备注').font = head_font
    for col in range(1, 11):
        ws.cell(row, col).fill = head_fill; ws.cell(row, col).border = border
    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row + 3, end_column=10)
    # 生成品牌分析
    brand_analysis_lines = []
    for s in sups:
        if s['quote_price'] and s['quote_price'] > 0:
            # 使用商家填写的品牌信息
            brand_name = s.get('quote_brand') or s.get('brand') or ''
            if brand_name:
                brand_info = search_brand_info(brand_name, '')
                if brand_info and (brand_info.get('优点') or brand_info.get('缺点')):
                    line = '%s（%s）：优点-%s；缺点-%s' % (s['supplier_name'], 
                        brand_name,
                        brand_info.get('优点', ''), brand_info.get('缺点', ''))
                    brand_analysis_lines.append(line)
                elif brand_name:
                    # 如果没有搜索到品牌信息，显示商家填写的品牌
                    line = '%s（%s）：品牌已填写' % (s['supplier_name'], brand_name)
                    brand_analysis_lines.append(line)
    
    decision = ('本批次采购经过多方询价、比价与供应商综合考察，最终选择本供应商为合作方。')
    selected = next((s for s in sups if s['is_selected']), None)
    if selected:
        decision = ('经多方询价比价，推荐选择「%s」为合作方，报价¥%s，'
                    '性价比最优。%s' % (selected['supplier_name'],
                    format(float(selected['quote_price'] or 0), ',.2f'),
                    selected['quote_remark'] or '交货期及付款条件按合同约定'))
        # 在决策备注下方追加品牌分析
        if brand_analysis_lines:
            brand_text = '【品牌分析】' + chr(10) + chr(10).join(brand_analysis_lines)
            decision = decision + chr(10) + chr(10) + brand_text
    c = ws.cell(row, 1, decision)
    c.font = base_font; c.alignment = Alignment(vertical='top', wrap_text=True)
    for r2 in range(row, row + 4):
        for col in range(1, 11):
            ws.cell(r2, col).border = border
    row += 5
    ws.cell(row, 1, '编制人：').font = note_font
    ws.cell(row, 4, '审核人：').font = note_font
    ws.cell(row, 7, '日期：').font = note_font

    # 动态设置列宽，确保所有供应商列宽一致
    ws.column_dimensions['A'].width = 6   # 序号
    ws.column_dimensions['B'].width = 16  # 物料名称
    ws.column_dimensions['C'].width = 10  # 数量
    ws.column_dimensions['D'].width = 12  # 规格
    # 每个供应商4列: 单价(8) + 总价(10) + 品牌(10) + 备注(14)
    col_count = 4 + len(sups) * 4
    for ci in range(5, min(col_count + 1, 26)):
        col_letter = chr(64 + ci) if ci <= 26 else 'A' + chr(64 + ci - 26)
        if ci % 4 == 1:  # 备注列
            ws.column_dimensions[col_letter].width = 14
        elif ci % 4 == 0:  # 总价列
            ws.column_dimensions[col_letter].width = 10
        elif ci % 4 == 3:  # 品牌列
            ws.column_dimensions[col_letter].width = 10
        else:  # 单价列
            ws.column_dimensions[col_letter].width = 8

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = '询价单_%s.xlsx' % (i['inq_no'] or iid)
    resp = make_response(buf.getvalue())
    resp.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    resp.headers['Content-Disposition'] = "attachment; filename*=UTF-8''%s" % urllib.parse.quote(fname)
    log(session['user_name'], '导出询价单Excel', '%s' % (i['inq_no'] or iid))
    return resp

@app.route('/api/budgets')
@login_required
def api_budgets():
    conn = db(); rows = conn.execute("SELECT b.*,d.name as dept_name FROM budget_accounts b LEFT JOIN departments d ON b.dept_id=d.id").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/contracts')
@login_required
def api_contracts():
    conn = db(); rows = conn.execute("SELECT c.*,po.order_no FROM contracts c LEFT JOIN purchase_orders po ON c.order_id=po.id ORDER BY c.id DESC LIMIT 50").fetchall(); conn.close()
    out = []
    for r in rows:
        d = dict_row(r)
        try:
            d['amount_upper'] = rmb_upper(float(d.get('amount') or 0))
        except Exception:
            d['amount_upper'] = ''
        out.append(d)
    return jsonify(out)

@app.route('/api/contracts', methods=['POST'])
@login_required
def api_create_contract():
    d = request.json; conn = db()
    no = gen_contract_no(conn)
    # V4.1: 合同创建后进入审批流(待审批→审批通过→执行中), 审批通过前不能挂账
    conn.execute("INSERT INTO contracts(contract_no,order_id,contract_name,supplier,amount,sign_date,start_date,end_date,content,status,remark,urgent,attachment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no,d.get('order_id'),d.get('contract_name',''),d.get('supplier',''),float(d.get('amount',0)),
         d.get('sign_date'),d.get('start_date'),d.get('end_date'),d.get('content',''),'待审批',d.get('remark',''),
         1 if d.get('urgent') else 0,(d.get('attachment') or '').strip()))
    cid = conn.execute("SELECT id FROM contracts WHERE contract_no=?", (no,)).fetchone()[0]
    if d.get('order_id'): conn.execute("UPDATE purchase_orders SET status='已签合同',updated_at=? WHERE id=?", (now(),d['order_id']))
    conn.commit()
    create_approvals('contract', cid, float(d.get('amount',0)))
    start_instances('contract', cid)   # 飞书/钉钉同步发起合同审批(未配置则跳过)
    conn.close()
    log(session['user_name'],'创建合同',f'{no}')
    return jsonify({'success':True,'contract_no':no})

@app.route('/api/contracts/monthly-summary')
@login_required
def api_monthly_summary():
    """V11.68: 月结汇总 — 按厂家分组当月待月结订单(settle_type=月结且未月结)"""
    role = session.get('user_role')
    if role not in ('系统管理员', '采购员', '分管领导', '总经理'):
        return jsonify({'error': '无权限'}), 403
    conn = db()
    try: conn.execute("ALTER TABLE purchase_orders ADD COLUMN settled_at TEXT DEFAULT ''")
    except Exception: pass
    m = datetime.date.today().strftime('%Y-%m')
    rows = conn.execute("""SELECT supplier, COUNT(*) cnt, COALESCE(SUM(total_amount),0) amt,
        GROUP_CONCAT(order_no || ':' || item_name || 'x' || printf('%g',quantity) || ' ¥' || printf('%.2f',total_amount), '\n') detail
        FROM purchase_orders WHERE settle_type='月结' AND status IN ('审批通过','已通过')
        AND (settled_at IS NULL OR settled_at='') AND created_at LIKE ?
        GROUP BY supplier ORDER BY amt DESC""", (m + '%',)).fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/contracts/monthly-generate', methods=['POST'])
@login_required
def api_monthly_generate():
    """V11.68: 生成月度合同 — 指定厂家的当月待月结订单汇总成一份合同"""
    role = session.get('user_role')
    if role not in ('系统管理员', '采购员', '分管领导', '总经理'):
        return jsonify({'error': '无权限'}), 403
    d = request.json or {}
    supplier = (d.get('supplier') or '').strip()
    if not supplier:
        return jsonify({'error': '请指定厂家'}), 400
    conn = db()
    m = datetime.date.today().strftime('%Y-%m')
    orders = conn.execute("""SELECT * FROM purchase_orders WHERE supplier=? AND settle_type='月结'
        AND status IN ('审批通过','已通过') AND (settled_at IS NULL OR settled_at='') AND created_at LIKE ?""",
        (supplier, m + '%')).fetchall()
    if not orders:
        conn.close(); return jsonify({'error': '该厂家本月无待月结订单'}), 400
    total = sum(float(o['total_amount'] or 0) for o in orders)
    # 明细文本
    lines = []
    for o in orders:
        lines.append(f"{o['order_no']} {o['item_name']} x{o['quantity']}{o['unit'] or '个'} ¥{float(o['total_amount'] or 0):.2f}")
    content = '\n'.join(lines)
    order_nos = '、'.join(o['order_no'] for o in orders)
    # 生成月度合同(关联第一张单, 明细在content, 关联单号在remark)
    no = gen_contract_no(conn)
    y = datetime.date.today().strftime('%Y%m')
    conn.execute("""INSERT INTO contracts(contract_no,order_id,contract_name,supplier,amount,sign_date,start_date,end_date,content,status,remark,urgent,attachment)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (no, orders[0]['id'], f'{supplier}{y}月结算合同', supplier, round(total, 2),
         datetime.date.today().strftime('%Y-%m-%d'), '', '', content, '待审批',
         f'月结汇总: {order_nos}', 0, ''))
    cid = conn.execute("SELECT id FROM contracts WHERE contract_no=?", (no,)).fetchone()[0]
    # 标记订单已月结
    oids = [o['id'] for o in orders]
    for oid in oids:
        try: conn.execute("ALTER TABLE purchase_orders ADD COLUMN settled_at TEXT DEFAULT ''")
        except Exception: pass
        conn.execute("UPDATE purchase_orders SET settled_at=?, status='已签合同' WHERE id=?", (datetime.date.today().strftime('%Y-%m-%d'), oid))
    conn.commit()
    create_approvals('contract', cid, round(total, 2))
    conn.close()
    try: start_instances('contract', cid)
    except Exception: pass
    log(session['user_name'], '月结汇总', f'{supplier} {len(orders)}单 → 合同{no} ¥{total:.2f}')
    return jsonify({'success': True, 'contract_no': no, 'count': len(orders), 'amount': round(total, 2)})

@app.route('/api/deliveries')
@login_required
def api_deliveries():
    conn = db(); rows = conn.execute("SELECT * FROM deliveries ORDER BY id DESC LIMIT 50").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/deliveries', methods=['POST'])
@login_required
def api_create_delivery():
    """到货登记(模式1节点); 需求4/8: 送货单强制关联合同编号"""
    d = request.json; conn = db()
    cid = d.get('contract_id')
    cno = ''
    if cid:
        ct = conn.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
        if ct: cno = ct['contract_no']
    if not cid or not cno:
        conn.close(); return jsonify({'error': '到货登记必须关联合同编号(需求硬性规则)'}), 400
    no = gen_no('DH','deliveries','delivery_no')
    conn.execute("INSERT INTO deliveries(delivery_no,order_id,contract_id,contract_no,supplier,item_name,spec,quantity,unit,driver_name,vehicle_no,delivery_date,receiver,sign_status,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no,d.get('order_id'),cid,cno,d.get('supplier',''),d.get('item_name',''),d.get('spec',''),
         float(d.get('quantity',0)),d.get('unit','个'),d.get('driver_name',''),d.get('vehicle_no',''),
         d.get('delivery_date'),d.get('receiver',''),'待签收',d.get('remark','')))
    if d.get('order_id'): conn.execute("UPDATE purchase_orders SET status='已发货',updated_at=? WHERE id=?", (now(),d['order_id']))
    conn.commit(); conn.close()
    log(session['user_name'],'创建送货单',f'{no} 合同{cno}')
    return jsonify({'success':True,'delivery_no':no})

@app.route('/api/deliveries/<int:did>/sign', methods=['POST'])
@login_required
def api_sign_delivery(did):
    d = request.json; conn = db()
    dn = conn.execute("SELECT * FROM deliveries WHERE id=?", (did,)).fetchone()
    if not dn: return jsonify({'error':'not found'}),404
    conn.execute("UPDATE deliveries SET sign_status='已签收', receiver=?, sign_time=? WHERE id=?", (d.get('receiver','库房'),now(),did))
    # 任务4: 若该订单已自动生成入库单(货到付款下单即生成), 则复用, 不重复插入
    exist = conn.execute("SELECT id FROM receivings WHERE order_id=? AND status='待检验'", (dn['order_id'],)).fetchone()
    if exist:
        conn.execute("UPDATE receivings SET delivery_id=?, quantity=?, updated_at=datetime('now','localtime') WHERE id=?", (did, dn['quantity'], exist['id']))
        rno = conn.execute("SELECT receive_no FROM receivings WHERE id=?", (exist['id'],)).fetchone()[0]
    else:
        # V11.31: 自动带出部门(订单→申请单链)
        _dept = ''
        try:
            _po2 = conn.execute("SELECT req_id FROM purchase_orders WHERE id=?", (dn['order_id'],)).fetchone()
            if _po2 and _po2['req_id']:
                _pr2 = conn.execute("SELECT dept FROM purchase_requests WHERE id=?", (_po2['req_id'],)).fetchone()
                if _pr2 and _pr2['dept']:
                    _dept = _pr2['dept']
        except Exception:
            pass
        rno = gen_no('RK','receivings','receive_no')
        conn.execute("INSERT INTO receivings(receive_no,delivery_id,order_id,item_name,spec,quantity,unit,qualified_qty,status,received_at,dept) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rno,did,dn['order_id'],dn['item_name'],dn['spec'],dn['quantity'],dn['unit'],dn['quantity'],'待检验',now(),_dept))
    if dn['order_id']: conn.execute("UPDATE purchase_orders SET status='已到货',updated_at=? WHERE id=?", (now(),dn['order_id']))
    conn.commit(); conn.close()
    log(session['user_name'],'签收送货单',f'#{did}')
    return jsonify({'success':True,'receive_no':rno})

@app.route('/api/receivings')
@login_required
def api_receivings():
    # V11.64: 入库单 — 库管员/采购员/财务/领导/管理员可见(采购要发票核对+跟到货, 财务对账); 员工不看
    if session.get('user_role') == '员工':
        return jsonify([])
    # V11.29: 部门/类别筛选
    f_dept = (request.args.get('dept') or '').strip()
    f_cat = (request.args.get('cat') or '').strip()
    conn = db()
    sql = "SELECT r.*, po.trade_mode, po.order_no, po.supplier FROM receivings r LEFT JOIN purchase_orders po ON r.order_id=po.id"
    where = []; args = []
    if f_dept:
        where.append("r.dept=?"); args.append(f_dept)
    if f_cat:
        where.append("(r.item_name IN (SELECT item_name FROM inventory WHERE cat_code=(SELECT code FROM categories WHERE name=?)))")
        args.append(f_cat)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.id DESC LIMIT 80"
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        items = []
        if r['order_id']:
            items = [dict_row(x) for x in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (r['order_id'],)).fetchall()]
            cnt = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM order_items WHERE order_id=?", (r['order_id'],)).fetchone()
            if cnt and cnt[0]:
                d['item_count'] = cnt[0]; d['total_qty'] = cnt[1]
        if not items and r['items_json']:
            try:
                items = json.loads(r['items_json'])
            except Exception:
                items = []
        if not items:
            items = [{'item_name': r['item_name'], 'spec': r['spec'], 'quantity': r['quantity'], 'unit': r['unit']}]
        d['items'] = items
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/receivings/<int:rid>/arrived', methods=['POST'])
@login_required
def api_receiving_arrived(rid):
    """V11.37: 到货提醒单确认到货 — 货实际到了, 状态 待入库→入库中(可提交验收)"""
    conn = db()
    rn = conn.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        conn.close(); return jsonify({'error': '入库单不存在'}), 404
    if rn['status'] != '待入库':
        conn.close(); return jsonify({'error': f'当前状态({rn["status"]})无需确认到货'}), 400
    conn.execute("UPDATE receivings SET status='入库中', received_at=? WHERE id=?", (now(), rid))
    conn.commit(); conn.close()
    log(session['user_name'], '确认到货', f'#{rid} {rn["item_name"]} 合同自动生成单已确认到货')
    return jsonify({'success': True})

@app.route('/api/receivings/<int:rid>/complete', methods=['POST'])
@login_required
def api_complete_receiving(rid):
    """V5.0: 入库单提交审批 → 审批通过后由 finish_approvals 实际增加库存
    兼容旧调用(直接确认验收): 提交后自动走审批, 审批通过即入库
    V11.37: 到货提醒单(合同自动生成)需先点"确认到货"→状态入库中→再提交验收"""
    d = request.json; conn = db()
    rn = conn.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        conn.close(); return jsonify({'error': '入库单不存在'}), 404
    if rn['status'] in ('已入库', '已驳回', '已作废'):
        conn.close(); return jsonify({'error': f'当前状态({rn["status"]})不可提交审批'}), 400
    # 防重复提交: 待审批且已有待审实例 → 提示撤回而不是重复建链
    pend = conn.execute("SELECT 1 FROM approval_instances WHERE biz_type='receiving' AND biz_id=? AND status='pending' LIMIT 1", (rid,)).fetchone()
    if rn['status'] == '待审批' and pend:
        conn.close(); return jsonify({'error': '该入库单已在审批中，请勿重复提交（如需修改请先撤回）'}), 400
    warehouse = d.get('warehouse', '主库房'); inspector = d.get('inspector', '管理员')
    qty_override = d.get('items') or {}
    try: conn.execute("ALTER TABLE receivings ADD COLUMN completed_at TEXT")
    except Exception: pass
    try: conn.execute("ALTER TABLE receivings ADD COLUMN warehouse TEXT DEFAULT '主库房'")
    except Exception: pass
    oi = []
    if rn['order_id']:
        oi = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (rn['order_id'],)).fetchall()
    # 更新验收信息 + 置待审批
    total_q = 0.0
    # V8.1: 批量验收明细按行匹配(支持同名不同规格), 兼容旧 dict 按品名
    qty_list = qty_override if isinstance(qty_override, list) else None
    for idx, it in enumerate(oi):
        q = float(it['quantity'] or 0)
        if qty_list is not None and idx < len(qty_list):
            try: q = float(qty_list[idx].get('quantity', q) or 0)
            except Exception: pass
        elif isinstance(qty_override, dict) and it['item_name'] in qty_override:
            try: q = float(qty_override[it['item_name']] or 0)
            except Exception: pass
        total_q += max(q, 0)
    if not oi:
        total_q = float(d.get('qualified_qty', rn['quantity'] or 0))
    conn.execute("UPDATE receivings SET qualified_qty=?,defective_qty=?,inspector=?,warehouse=?,status='待审批',remark=? WHERE id=?",
                 (total_q, float(d.get('defective_qty',0)), inspector, warehouse,
                  (rn['remark'] or '') + ' 提交审批', rid))
    conn.commit()
    # 创建审批实例(入库单审批) + 同步发起钉钉/飞书审批
    create_approvals('receiving', rid, 0)
    conn.close()
    try: start_instances('receiving', rid)
    except Exception as e: print('receiving start_instances err:', e)
    log(session['user_name'], '提交入库审批', f'入库单#{rid} 待审批 {total_q}件')
    return jsonify({'success': True, 'message': f'入库单已提交审批，审批通过后自动增加库存（{total_q}件）', 'status': '待审批', 'items_in': len(oi) or 1})


def do_requisition_stock(c, rid, warehouse='主库房', operator='系统'):
    """V5.0: 出库审批通过后执行 — 扣减库存 + 写流水(幂等: 已有该单据出库流水则跳过)
    明细取 requisition_items; 扣减后允许库存为负(展示为负值, 与V5.0设计一致)"""
    rq = c.execute("SELECT * FROM requisitions WHERE id=?", (rid,)).fetchone()
    if not rq:
        return 0
    done = c.execute("SELECT 1 FROM inventory_flows WHERE doc_type='requisition' AND doc_id=? AND flow_type='出库' LIMIT 1", (rid,)).fetchone()
    if done:
        return 0
    its = c.execute("SELECT * FROM requisition_items WHERE requisition_id=? ORDER BY id", (rid,)).fetchall()
    if not its:
        return 0
    total_q = 0.0
    for it in its:
        q = float(it['quantity'] or 0)
        if q <= 0:
            continue
        total_q += q
        inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? AND (warehouse=? OR warehouse IS NULL OR warehouse='') ORDER BY quantity DESC LIMIT 1",
                        (it['item_name'], it['spec'] or '', warehouse)).fetchone()
        if inv is None:
            inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? ORDER BY quantity DESC LIMIT 1",
                            (it['item_name'], it['spec'] or '')).fetchone()
        if inv:
            new_q = (inv['quantity'] or 0) - q
            c.execute("UPDATE inventory SET quantity=?, last_move_date=?, updated_at=? WHERE id=?", (new_q, now(), now(), inv['id']))
        else:
            new_q = -q
            c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,last_move_date,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (it['item_name'], it['spec'] or '', it['unit'] or '个', new_q, warehouse, now(), now()))
        c.execute("INSERT INTO inventory_flows(item_name,spec,unit,flow_type,doc_type,doc_id,doc_no,qty,balance_after,operator,remark,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (it['item_name'], it['spec'] or '', it['unit'] or '个', '出库', 'requisition', rid, rq['req_no'],
                   -q, new_q, operator or '系统', f'出库单{rq["req_no"]}审批通过', now()))
    return total_q


def _op_name():
    """V11.12: 取操作人 — 后台线程(审批轮询/回调)无HTTP请求上下文, flask session 会抛异常; 有请求时返回登录用户, 否则返回'系统'"""
    try:
        from flask import session as _s
        return _s.get('user_name', '系统')
    except Exception:
        return '系统'


def do_receiving_stock(c, rid, warehouse='主库房', inspector='管理员', qty_override=None):
    """V5.0: 入库审批通过后执行 — 增加库存 + 写流水(幂等: 已有该单据入库流水则跳过)"""
    rn = c.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        return 0
    # 幂等判断: 用流水表而非状态(父状态可能已被 finish_approvals 更新)
    done = c.execute("SELECT 1 FROM inventory_flows WHERE doc_type='receiving' AND doc_id=? AND flow_type='入库' LIMIT 1", (rid,)).fetchone()
    if done:
        return 0
    qty_override = qty_override or {}
    oi = []
    if rn['order_id']:
        oi = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (rn['order_id'],)).fetchall()
    # 手动入库单: 明细在 items_json
    manual_items = []
    if rn['items_json']:
        try:
            manual_items = json.loads(rn['items_json'] or '[]')
        except Exception:
            manual_items = []
    total_q = 0.0
    if manual_items:
        # ── 手动入库单: 逐条明细入库存(带含税单价) ──
        for it in manual_items:
            q = float(it.get('quantity', 0) or 0)
            if q <= 0: continue
            total_q += q
            _price = float(it.get('price', 0) or 0); _tr = float(it.get('tax_rate', 13) or 13)
            inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? AND warehouse=?",
                            (it['item_name'], it.get('spec', '') or '', warehouse)).fetchone()
            if inv:
                _up = "quantity=quantity+?, last_move_date=?, updated_at=?"
                _args = [q, now(), now()]
                if (not inv['price'] or inv['price'] == 0) and _price:
                    _up += ", price=?"; _args.append(_price)
                if _tr and (not inv['tax_rate'] or inv['tax_rate'] == 0):
                    _up += ", tax_rate=?"; _args.append(_tr)
                _args.append(inv['id'])
                c.execute("UPDATE inventory SET " + _up + " WHERE id=?", _args)
                new_bal = (inv['quantity'] or 0) + q
            else:
                c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,tax_rate,last_move_date,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (it['item_name'], it.get('spec','') or '', it.get('unit','个') or '个', q, warehouse, _price, _tr, now(), now()))
                new_bal = q
            c.execute("INSERT INTO inventory_flows(item_name,spec,unit,flow_type,doc_type,doc_id,doc_no,qty,balance_after,operator,remark,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (it['item_name'], it.get('spec','') or '', it.get('unit','个') or '个', '入库', 'receiving', rid, rn['receive_no'], q, new_bal,
                       _op_name(), f'入库单{rn["receive_no"]}审批通过', now()))
    elif oi:
        _po = c.execute("SELECT category, trade_mode, supplier FROM purchase_orders WHERE id=?", (rn['order_id'],)).fetchone()
        _po_sup = (_po['supplier'] or '') if _po else ''
        qty_list = qty_override if isinstance(qty_override, list) else None
        for idx, it in enumerate(oi):
            q = float(it['quantity'] or 0)
            if qty_list is not None and idx < len(qty_list):
                try: q = float(qty_list[idx].get('quantity', q) or 0)
                except Exception: pass
            elif isinstance(qty_override, dict) and it['item_name'] in qty_override:
                try: q = float(qty_override[it['item_name']] or 0)
                except Exception: pass
            if q <= 0: continue
            total_q += q
            _price = float(it['price'] or 0); _tr = float(it['tax_rate'] or 13)
            _cat = _po['category'] or '' if _po else ''
            inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? AND warehouse=?",
                            (it['item_name'], it['spec'] or '', warehouse)).fetchone()
            if inv:
                _up = "quantity=quantity+?, last_move_date=?, updated_at=?"
                _args = [q, now(), now()]
                if (not inv['price'] or inv['price'] == 0) and _price:
                    _up += ", price=?"; _args.append(_price)
                if _cat and not inv['cat_code']:
                    _up += ", cat_code=?"; _args.append(_cat)
                if _po_sup and not inv['supplier']:
                    _up += ", supplier=?"; _args.append(_po_sup)
                _args.append(inv['id'])
                c.execute("UPDATE inventory SET " + _up + " WHERE id=?", _args)
                new_bal = (inv['quantity'] or 0) + q
            else:
                c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,tax_rate,cat_code,last_move_date,updated_at,supplier) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                          (it['item_name'], it['spec'] or '', it['unit'] or '个', q, warehouse, _price, _tr, _cat, now(), now(), _po_sup))
                new_bal = q
            c.execute("INSERT INTO inventory_flows(item_name,spec,unit,flow_type,doc_type,doc_id,doc_no,qty,balance_after,operator,remark,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (it['item_name'], it['spec'] or '', it['unit'] or '个', '入库', 'receiving', rid, rn['receive_no'], q, new_bal,
                       _op_name(), f'入库单{rn["receive_no"]}审批通过', now()))
    else:
        q = float(rn['quantity'] or 0)
        total_q = q
        _price = 0; _tr = 13; _cat = ''; _sup = ''
        if rn['order_id']:
            _po2 = c.execute("SELECT price, tax_rate, category, supplier FROM purchase_orders WHERE id=?", (rn['order_id'],)).fetchone()
            if _po2:
                _price = _po2['price'] or 0; _tr = _po2['tax_rate'] or 13; _cat = _po2['category'] or ''; _sup = _po2['supplier'] or ''
        inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? AND warehouse=?",
                        (rn['item_name'], rn['spec'] or '', warehouse)).fetchone()
        if inv:
            _up = "quantity=quantity+?, last_move_date=?, updated_at=?"
            _args = [q, now(), now()]
            if (not inv['price'] or inv['price'] == 0) and _price:
                _up += ", price=?"; _args.append(_price)
            if _cat and not inv['cat_code']:
                _up += ", cat_code=?"; _args.append(_cat)
            if _sup and not inv['supplier']:
                _up += ", supplier=?"; _args.append(_sup)
            _args.append(inv['id'])
            c.execute("UPDATE inventory SET " + _up + " WHERE id=?", _args)
            new_bal = (inv['quantity'] or 0) + q
        else:
            c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,price,tax_rate,cat_code,last_move_date,updated_at,supplier) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (rn['item_name'], rn['spec'] or '', rn['unit'] or '个', q, warehouse, _price, _tr, _cat, now(), now(), _sup))
            new_bal = q
        c.execute("INSERT INTO inventory_flows(item_name,spec,unit,flow_type,doc_type,doc_id,doc_no,qty,balance_after,operator,remark,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rn['item_name'], rn['spec'] or '', rn['unit'] or '个', '入库', 'receiving', rid, rn['receive_no'], q, new_bal,
                   _op_name(), f'入库单{rn["receive_no"]}审批通过', now()))
    c.execute("UPDATE receivings SET status='已入库',completed_at=?,warehouse=?,inspector=? WHERE id=?",
              (now(), warehouse, inspector or '管理员', rid))
    if rn['order_id']:
        po = c.execute("SELECT * FROM purchase_orders WHERE id=?", (rn['order_id'],)).fetchone()
        if po and po['trade_mode'] == '先款后货':
            c.execute("UPDATE purchase_orders SET status='已核销',updated_at=? WHERE id=?", (now(), rn['order_id']))
        else:
            c.execute("UPDATE purchase_orders SET status='已入库',updated_at=? WHERE id=?", (now(), rn['order_id']))
    return total_q

# ---- V6: 入库单下载(生成标准入库单 xlsx) ----
@app.route('/api/receivings/<int:rid>/download')
@login_required
def api_receiving_download(rid):
    """入库单下载(V8.4b: 紧凑布局, 无空白列)
    标题/日期+票号+供应商+品种数/仓库/表头(No|品名|规格|数量|单位|不含税单价|税率|不含税金额|含税金额|备注)/小计/合计大写/签字区"""
    conn = db()
    rn = conn.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        conn.close(); return jsonify({'error': '入库单不存在'}), 404
    po = None
    if rn['order_id']:
        po = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (rn['order_id'],)).fetchone()
    oi = []
    if rn['order_id']:
        oi = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (rn['order_id'],)).fetchall()
    conn.close()
    supplier = (po['supplier'] if po else '') or ''
    rows = []
    if oi:
        for it in oi:
            price = float(it['price'] or 0)
            tr = float(it['tax_rate'] or 13)
            rows.append({'name': it['item_name'], 'spec': it['spec'] or '', 'qty': float(it['quantity'] or 0),
                         'unit': it['unit'] or '个', 'price': price, 'tax': tr,
                         'amt_no': price * float(it['quantity'] or 0),
                         'amt_tax': price * float(it['quantity'] or 0) * (1 + tr / 100),
                         'remark': (dict(it).get('remark') or '')})
    elif rn['items_json']:
        try:
            for it in json.loads(rn['items_json']):
                price = float(it.get('price') or 0); tr = float(it.get('tax_rate') or 13)
                q = float(it.get('quantity') or 0)
                rows.append({'name': it.get('item_name', ''), 'spec': it.get('spec') or '', 'qty': q,
                             'unit': it.get('unit') or '个', 'price': price, 'tax': tr,
                             'amt_no': price * q, 'amt_tax': price * q * (1 + tr / 100),
                             'remark': it.get('remark') or ''})
        except Exception:
            rows = []
    if not rows:
        price = float((po['price'] if po else 0) or 0); tr = 13
        q = float(rn['quantity'] or 0)
        rows = [{'name': rn['item_name'], 'spec': rn['spec'] or '', 'qty': q, 'unit': rn['unit'] or '个',
                 'price': price, 'tax': tr, 'amt_no': price * q, 'amt_tax': price * q * (1 + tr / 100),
                 'remark': rn['remark'] or ''}]
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side
    wb = Workbook(); ws = wb.active; ws.title = '入库单'
    thin = Side(style='thin', color='000000')
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    CN = lambda bold=False, size=10: Font(name='宋体', bold=bold, size=size)
    # 标题
    ws.merge_cells('A1:J1')
    c = ws['A1']; c.value = '河曲县正成洗选煤有限责任公司入库单'
    c.font = CN(bold=True, size=14); c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 26
    # 第2行: 日期/票号/供应商
    ws['A2'] = '日期：'; ws['B2'] = (rn['completed_at'] or rn['created_at'] or '')[:10]
    ws['C2'] = '票号：'; ws['D2'] = rn['receive_no']
    ws['E2'] = '供应商：'; ws.merge_cells('F2:J2'); ws['F2'] = supplier
    for cc in ('A2','B2','C2','D2','E2','F2'):
        ws[cc].font = CN()
    # 第3行: 仓库/品种数 + V11.29 归属部门
    ws['A3'] = '仓库：'; ws.merge_cells('B3:C3'); ws['B3'] = rn['warehouse'] or '生产库房'
    ws['D3'] = '品种数：'; ws['E3'] = len(rows)
    ws['F3'] = '归属部门：'; ws.merge_cells('G3:J3'); ws['G3'] = rn['dept'] if 'dept' in rn.keys() and rn['dept'] else ''
    for cc in ('A3','B3','D3','E3'):
        ws[cc].font = CN()
    # 第4行表头 (10列连续, 无空白列)
    headers = ['No.', '品名', '规格', '数量', '单位', '不含税单价', '税率', '不含税金额', '含税金额', '备注']
    for j, h in enumerate(headers, 1):
        cc = ws.cell(row=4, column=j, value=h)
        cc.font = CN(bold=True); cc.border = bd
        cc.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[4].height = 18
    # 明细行
    r = 5
    t_qty = 0.0; t_no = 0.0; t_tax = 0.0
    for idx, it in enumerate(rows, 1):
        t_qty += it['qty']; t_no += it['amt_no']; t_tax += it['amt_tax']
        vals = [idx, it['name'], it['spec'], it['qty'], it['unit'], it['price'],
                f"{it['tax']}%", round(it['amt_no'], 2), round(it['amt_tax'], 2), it['remark']]
        for j, v in enumerate(vals, 1):
            cc = ws.cell(row=r, column=j, value=v)
            cc.font = CN(); cc.border = bd
            cc.alignment = Alignment(vertical='center', horizontal='left' if j == 2 else 'center')
        r += 1
    # 小计行
    ws.cell(row=r, column=1, value='小计').font = CN(bold=True)
    ws.cell(row=r, column=4, value=t_qty).font = CN(bold=True)
    ws.cell(row=r, column=8, value=round(t_no, 2)).font = CN(bold=True)
    ws.cell(row=r, column=9, value=round(t_tax, 2)).font = CN(bold=True)
    for j in range(1, 11):
        ws.cell(row=r, column=j).border = bd
    r += 1
    # 合计大写
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
    ws.cell(row=r, column=1, value=f'合计: 人民币大写：{rmb_upper(t_tax)}').font = CN(bold=True)
    for j in range(1, 11):
        ws.cell(row=r, column=j).border = bd
    r += 1
    # 签字区
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
    ws.cell(row=r, column=1, value='库管员签字：____________    验收员签字：____________    采购员：____________').font = CN()
    for j in range(1, 11):
        ws.cell(row=r, column=j).border = bd
    ws.row_dimensions[r].height = 22
    r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
    ws.cell(row=r, column=1, value='第1页/共1页').font = CN()
    ws.cell(row=r, column=1).alignment = Alignment(horizontal='center')
    for j, w in enumerate([6, 18, 18, 10, 8, 12, 8, 12, 12, 20], 1):
        ws.column_dimensions[chr(64 + j)].width = w
    # V11.27: 审批通过 → 盖章领导预录签名
    stamp_leader_sign(ws, r - 1, 'receiving', rn['id'])
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    from flask import send_file
    resp = send_file(bio, as_attachment=True, download_name=f'{rn["receive_no"]}入库单.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/requisitions/<int:rid>/download')
@login_required
def api_requisition_download(rid):
    """出库单下载(V8.4b: 紧凑布局, 无空白列) 数量/金额为负值"""
    conn = db()
    rq = conn.execute("SELECT * FROM requisitions WHERE id=?", (rid,)).fetchone()
    if not rq:
        conn.close(); return jsonify({'error': '出库单不存在'}), 404
    its = conn.execute("SELECT * FROM requisition_items WHERE requisition_id=? ORDER BY id", (rid,)).fetchall()
    price_map = {}
    try:
        # V9.1: 按 名称+规格 取价, 避免同名不同规格串价
        for inv in conn.execute("SELECT item_name, spec, price FROM inventory WHERE price IS NOT NULL").fetchall():
            price_map.setdefault((inv['item_name'], inv['spec'] or ''), float(inv['price'] or 0))
    except Exception:
        pass
    conn.close()
    if not its:
        its = [{'item_name': rq['item_name'], 'spec': rq['spec'] or '', 'unit': rq['unit'] or '个',
                'quantity': rq['quantity'], 'purpose': rq['purpose'] or ''}]
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side
    wb = Workbook(); ws = wb.active; ws.title = '出库单'
    thin = Side(style='thin', color='000000')
    bd = Border(left=thin, right=thin, top=thin, bottom=thin)
    CN = lambda bold=False, size=10: Font(name='宋体', bold=bold, size=size)
    # 标题
    ws.merge_cells('A1:G1')
    c = ws['A1']; c.value = '河曲县洗选煤有限责任公司部门出库单'
    c.font = CN(bold=True, size=14); c.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 26
    # 第2行: 日期/票号/领取人
    ws['A2'] = '日期：'; ws['B2'] = (rq['issued_at'] or rq['created_at'] or '')[:10]
    ws['C2'] = '票号：'; ws['D2'] = rq['req_no']
    ws['E2'] = '领取人：'; ws['F2'] = rq['receiver'] or rq['requester'] or ''
    for cc in ('A2','B2','C2','D2','E2','F2'):
        ws[cc].font = CN()
    # 第3行: 部门/仓库/品种数
    ws['A3'] = '部门：'; ws['B3'] = rq['dept'] or ''
    ws['C3'] = '仓库：'; ws['D3'] = '主库房'
    ws['E3'] = '领取部门：'; ws['F3'] = rq['receive_dept'] or rq['dept'] or ''
    for cc in ('A3','B3','C3','D3','E3','F3'):
        ws[cc].font = CN()
    # 第4行表头 (7列连续, 无空白列)
    headers = ['No.', '品名', '规格', '单位', '数量', '单价', '金额']
    for j, h in enumerate(headers, 1):
        cc = ws.cell(row=4, column=j, value=h)
        cc.font = CN(bold=True); cc.border = bd
        cc.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[4].height = 18
    # 明细行
    r = 5
    t_qty = 0.0; t_amt = 0.0
    for idx, it in enumerate(its, 1):
        q = float(it['quantity'] or 0); t_qty += q
        price = price_map.get((it['item_name'], (dict(it).get('spec') or '') or ''), 0)
        amt = q * price; t_amt += amt
        vals = [idx, it['item_name'], (dict(it).get('spec') or '') or '',
                (dict(it).get('unit') or '个') or '个', -q, price, -amt]
        for j, v in enumerate(vals, 1):
            cc = ws.cell(row=r, column=j, value=v)
            cc.font = CN(); cc.border = bd
            cc.alignment = Alignment(vertical='center', horizontal='left' if j == 2 else 'center')
        r += 1
    # 本页合计
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=3)
    ws.cell(row=r, column=1, value='本页合计：').font = CN(bold=True)
    ws.cell(row=r, column=5, value=f'数量：{-t_qty}').font = CN(bold=True)
    ws.cell(row=r, column=7, value=f'金额：{-t_amt:.2f}').font = CN(bold=True)
    for j in range(1, 8):
        ws.cell(row=r, column=j).border = bd
    r += 1
    # 总金额
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    ws.cell(row=r, column=1, value=f'总金额：￥{-t_amt:.2f}元（人民币大写：{rmb_upper(t_amt)}）').font = CN(bold=True)
    for j in range(1, 8):
        ws.cell(row=r, column=j).border = bd
    r += 1
    # 签字区
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    ws.cell(row=r, column=1, value='采购员：____________    操作员：____________').font = CN()
    for j in range(1, 8):
        ws.cell(row=r, column=j).border = bd
    ws.row_dimensions[r].height = 22
    for j, w in enumerate([6, 20, 20, 8, 10, 12, 14], 1):
        ws.column_dimensions[chr(64 + j)].width = w
    # V11.27: 审批通过 → 盖章领导预录签名
    stamp_leader_sign(ws, r, 'requisition', rq['id'])
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    from flask import send_file
    resp = send_file(bio, as_attachment=True, download_name=f'{rq["req_no"]}出库单.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/api/requisitions')
@login_required
def api_requisitions():
    """出库单列表 — V11.64: 库管员/领导/部门负责人可见; 员工只看自己的; 采购员/财务不看(库房的事)"""
    role = session.get('user_role')
    if role in ('采购员', '财务'):
        return jsonify([])
    conn = db()
    if role in ('员工',):
        rows = conn.execute("SELECT * FROM requisitions WHERE requester=? ORDER BY id DESC LIMIT 100", (session.get('user_name', ''),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM requisitions ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        cnt = conn.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM requisition_items WHERE requisition_id=?", (r['id'],)).fetchone()
        d['item_count'] = cnt[0] or 1
        d['total_qty'] = cnt[1] or r['quantity']
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/requisitions', methods=['POST'])
@login_required
def api_create_requisition():
    """V5.0: 新建出库单(批量商品) — 提交走审批, 审批通过自动扣减库存(展示为负值)"""
    d = request.json; conn = db()
    items = d.get('items') or []
    if not items and d.get('item_name'):
        items = [{'item_name': d.get('item_name'), 'spec': d.get('spec'), 'unit': d.get('unit', '个'),
                  'quantity': d.get('quantity', 0), 'purpose': d.get('purpose', '')}]
    items = [it for it in items if it.get('item_name') and float(it.get('quantity', 0) or 0) > 0]
    if not items:
        conn.close(); return jsonify({'error': '请至少填写一个商品及数量'}), 400
    # 库存校验(拦截超量) — V9.1: 名称+规格双条件匹配独立SKU
    for it in items:
        inv = conn.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? ORDER BY quantity DESC",
                           (it['item_name'], it.get('spec', '') or '')).fetchone()
        if not inv:
            conn.close(); return jsonify({'error': '库存中无此物资: %s %s' % (it['item_name'], it.get('spec', '') or '')}), 400
        if inv['quantity'] < float(it['quantity']):
            conn.close(); return jsonify({'error': '库存不足: %s(%s) 当前%s%s' % (it['item_name'], it.get('spec', '') or '', inv['quantity'], inv['unit'] or '个')}), 400
    no = gen_no('CK', 'requisitions', 'req_no', conn)
    total_q = sum(float(it['quantity']) for it in items)
    first = items[0]
    # V11.25: 领取人/领取部门 — 出库留痕可追溯(丢了东西能找到人)
    receiver = (d.get('receiver') or '').strip()
    receive_dept = (d.get('receive_dept') or '').strip()
    if not receiver:
        receiver = session['user_name']
    conn.execute("INSERT INTO requisitions(req_no,dept,requester,item_name,spec,quantity,unit,purpose,status,receiver,receive_dept,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (no, d.get('dept', ''), session['user_name'], first['item_name'], first.get('spec', ''),
                  total_q, first.get('unit', '个'), d.get('purpose', first.get('purpose', '')), '待审批', receiver, receive_dept, now()))
    rid = conn.execute("SELECT id FROM requisitions WHERE req_no=?", (no,)).fetchone()[0]
    for it in items:
        conn.execute("INSERT INTO requisition_items(requisition_id,item_name,spec,unit,quantity,purpose,created_at) VALUES(?,?,?,?,?,?,?)",
                     (rid, it['item_name'], it.get('spec', ''), it.get('unit', '个'),
                      float(it['quantity']), it.get('purpose', d.get('purpose', '')), now()))
    conn.commit()
    create_approvals('requisition', rid, 0)
    conn.close()
    try: start_instances('requisition', rid)
    except Exception as e: print('requisition start_instances err:', e)
    log(session['user_name'], '新建出库单', f'{no} {len(items)}项 {total_q}件 待审批')
    return jsonify({'success': True, 'req_no': no, 'id': rid, 'message': f'出库单 {no} 已提交审批，审批通过后自动扣减库存'})


@app.route('/api/receivings', methods=['POST'])
@login_required
def api_create_receiving():
    """新建入库单(不依赖订单): 多商品明细, 提交走审批, 审批通过自动加库存"""
    d = request.json; conn = db()
    items = d.get('items') or []
    if not items and d.get('item_name'):
        items = [{'item_name': d.get('item_name'), 'spec': d.get('spec'), 'unit': d.get('unit', '个'),
                  'quantity': d.get('quantity', 0), 'price': d.get('price', 0), 'tax_rate': d.get('tax_rate', 13)}]
    items = [it for it in items if it.get('item_name') and float(it.get('quantity', 0) or 0) > 0]
    if not items:
        conn.close(); return jsonify({'error': '请至少填写一个商品及数量'}), 400
    no = gen_no('RK', 'receivings', 'receive_no', conn)
    total_q = sum(float(it['quantity']) for it in items)
    first = items[0]
    try: conn.execute("ALTER TABLE receivings ADD COLUMN items_json TEXT DEFAULT ''")
    except Exception: pass
    # V11.26: 验收照片/视频附件(责任留证)
    _atts = d.get('attachments') or []
    _atts_json = json.dumps([str(a) for a in _atts if a], ensure_ascii=False) if _atts else ''
    # V11.29: 归属部门(必选, 按部门分类入库)
    _dept = (d.get('dept') or '').strip()[:30]
    # V11.54: 入库单默认"暂估"(货到发票未到), 采购收到发票后红冲转正式
    _is_est = 1 if d.get('is_est', True) else 0
    _est_amt = round(float(d.get('est_amount') or 0), 2)
    conn.execute("INSERT INTO receivings(receive_no,order_id,item_name,spec,quantity,unit,qualified_qty,status,received_at,remark,items_json,attachments,dept,is_est,est_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (no, d.get('order_id'), first['item_name'], first.get('spec', ''), total_q,
                  first.get('unit', '个'), 0, '待审批', now(), '手动入库单: %d项商品' % len(items),
                  json.dumps(items, ensure_ascii=False), _atts_json, _dept, _is_est, _est_amt))
    rid = conn.execute("SELECT id FROM receivings WHERE receive_no=?", (no,)).fetchone()[0]
    # 手动入库单没有 order_items, 明细暂存 remark; 审批通过时按 quantity 入库
    conn.commit()
    create_approvals('receiving', rid, 0)
    conn.close()
    try: start_instances('receiving', rid)
    except Exception as e: print('receiving start_instances err:', e)
    log(session['user_name'], '新建入库单', f'{no} {len(items)}项 {total_q}件 待审批')
    return jsonify({'success': True, 'receive_no': no, 'message': f'入库单 {no} 已提交审批，审批通过后自动增加库存'})

@app.route('/api/inventory')
@login_required
def api_inventory():
    """V55需求3: 库存列表含 不含税单价/税率/含税单价/合计 计算字段
    V6: 带出供应商(入库时写入, 存量空值回填自订单历史)"""
    cat = request.args.get('cat', '')
    conn = db()
    # 存量回填: supplier 为空时从订单历史取最近供应商
    conn.execute("""UPDATE inventory SET supplier=(SELECT po.supplier FROM purchase_orders po
                    WHERE po.item_name=inventory.item_name AND po.supplier!='' ORDER BY po.id DESC LIMIT 1)
                    WHERE (supplier IS NULL OR supplier='') AND item_name IN
                    (SELECT DISTINCT item_name FROM purchase_orders WHERE supplier!='')""")
    conn.commit()
    if cat:
        rows = conn.execute("SELECT i.*,c.name as cat_name FROM inventory i LEFT JOIN categories c ON i.cat_code=c.code WHERE i.cat_code=? ORDER BY i.id", (cat,)).fetchall()
    else:
        rows = conn.execute("SELECT i.*,c.name as cat_name FROM inventory i LEFT JOIN categories c ON i.cat_code=c.code ORDER BY i.id").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict_row(r)
        # V11.64: 库管员/员工/部门负责人 看库存数量但价格脱敏(防利益)
        if not can_see_price():
            d['price'] = None
        tr = float(d.get('tax_rate') or 13)
        price0 = d.get('price') or 0
        d['price_taxed'] = round(price0 * (1 + tr / 100.0), 4)
        d['total_untaxed'] = round((d.get('quantity') or 0) * price0, 2)
        d['total_taxed'] = round((d.get('quantity') or 0) * d['price_taxed'], 2)
        out.append(d)
    return jsonify(out)

@app.route('/api/inventory/stock-check')
@login_required
def api_inventory_stock_check():
    """V11.38: 申请单填物资时查库存 — 按物资名称(支持规格模糊)返回库存情况"""
    name = (request.args.get('name') or '').strip()
    if not name:
        return jsonify({'error': '缺少物资名称'}), 400
    conn = db()
    rows = conn.execute("SELECT * FROM inventory WHERE item_name=? ORDER BY id", (name,)).fetchall()
    if not rows:
        # 模糊匹配(名称包含)
        rows = conn.execute("SELECT * FROM inventory WHERE item_name LIKE ? ORDER BY id LIMIT 5", ('%' + name + '%',)).fetchall()
    conn.close()
    out = [{'id': r['id'], 'item_name': r['item_name'], 'spec': r['spec'] or '', 'unit': r['unit'] or '个',
            'quantity': r['quantity'] or 0, 'warehouse': r['warehouse'] or '主库房',
            'supplier': r['supplier'] or '', 'cat_name': ''} for r in rows]
    total = sum(float(x['quantity']) for x in out)
    return jsonify({'matches': out, 'total_qty': total, 'found': len(out) > 0})

@app.route('/api/logs')
@login_required
def api_logs():
    # V11.64: 操作日志仅管理员/领导可见(审计记录敏感)
    if session.get('user_role') not in ('系统管理员', '分管领导', '总经理'):
        return jsonify([])
    conn = db(); rows = conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 100").fetchall(); conn.close()
    return jsonify([dict_row(r) for r in rows])

# ============================================================
# V55 ── 预警中心: 14类预警引擎 + 首页集中展示 + 已处理/日志/配置
# ============================================================
def build_alerts():
    """重建全部待处理预警(幂等): 先清pending再按规则扫描插入; 已处理记录保留作预警日志"""
    c = db()
    c.execute("DELETE FROM alert_items WHERE status='pending'")
    warn_arrive = int(cfg_get('warn_arrive_days', '3') or 3)
    warn_contract = int(cfg_get('warn_contract_days', '30') or 30)
    warn_pay = int(cfg_get('warn_pay_days', '3') or 3)
    warn_approve = int(cfg_get('warn_approve_hours', '24') or 24)
    warn_idle = int(cfg_get('warn_idle_days', '90') or 90)
    warns = []
    def add(at, lv, title, content, btype='', bid=0):
        warns.append((at, lv, title, content, btype, bid))
    # 1) 库存缺货
    for r in c.execute("SELECT * FROM inventory WHERE safe_stock>0 AND quantity<=safe_stock"):
        lv = 'red' if r['quantity'] <= 0 else 'orange'
        add('库存缺货', lv, '缺货: %s' % r['item_name'],
            '当前库存 %s%s，低于安全库存 %s%s，请尽快发起采购申请' % (r['quantity'], r['unit'], r['safe_stock'], r['unit']), 'inventory', r['id'])
    # 2) 库存超储
    for r in c.execute("SELECT * FROM inventory WHERE max_stock>0 AND quantity>=max_stock"):
        add('库存超储', 'orange', '超储: %s' % r['item_name'],
            '当前库存 %s%s，已达库存上限 %s%s，避免过量囤积占用资金' % (r['quantity'], r['unit'], r['max_stock'], r['unit']), 'inventory', r['id'])
    # 3) 呆滞库存
    for r in c.execute("SELECT * FROM inventory WHERE quantity>0 AND last_move_date!='' AND last_move_date < date('now',?)", ('-%d days' % warn_idle,)):
        add('呆滞库存', 'orange', '呆滞: %s' % r['item_name'],
            '库存 %s%s，已超过%d天无出入库记录，建议盘点处理' % (r['quantity'], r['unit'], warn_idle), 'inventory', r['id'])
    # 3.5) 物料临期(有保质期字段的物资, 提前30天预警优先领用)
    for r in c.execute("SELECT * FROM inventory WHERE expiry_date!='' AND expiry_date<=date('now','+30 days') AND expiry_date>=date('now')"):
        add('物料临期', 'orange', '临期: %s' % r['item_name'],
            '保质期至 %s，请优先领用或尽快处理' % r['expiry_date'], 'inventory', r['id'])
    # 4) 订单待到货(临近交货日)
    for r in c.execute("SELECT * FROM purchase_orders WHERE status NOT IN ('已完成','已关闭','已核销','已入库') AND target_date!='' AND target_date<=date('now',?) AND target_date>=date('now')", ('+%d days' % warn_arrive,)):
        add('待到货', 'orange', '待到货: %s' % r['order_no'],
            '%s x%s%s，约定交货日 %s，请跟进供应商发货' % (r['item_name'], r['quantity'], r['unit'], r['target_date']), 'order', r['id'])
    # 5) 订单超期未到货
    for r in c.execute("SELECT * FROM purchase_orders WHERE status NOT IN ('已完成','已关闭','已核销','已入库') AND target_date!='' AND target_date<date('now')"):
        add('超期未到货', 'red', '超期未到货: %s' % r['order_no'],
            '%s 约定 %s 到货，已超期，请立即联系供应商' % (r['item_name'], r['target_date']), 'order', r['id'])
    # 6) 合同到期
    for r in c.execute("SELECT * FROM contracts WHERE status IN ('执行中','待审批') AND end_date!='' AND end_date<=date('now',?) AND end_date>=date('now')", ('+%d days' % warn_contract,)):
        add('合同到期', 'orange', '合同到期: %s' % r['contract_no'],
            '%s 将于 %s 到期，请评估是否续签' % (r['contract_name'] or '', r['end_date']), 'contract', r['id'])
    # 7) 合同超期未归档
    for r in c.execute("SELECT * FROM contracts WHERE status='执行中' AND (file_path IS NULL OR file_path='')"):
        add('合同未归档', 'orange', '合同未归档: %s' % r['contract_no'],
            '交易已进行但合同文件未上传归档，请经办人补齐资料', 'contract', r['id'])
    # 8) 应付款到期
    for r in c.execute("SELECT * FROM payment_requests WHERE status IN ('待审批','已通过') AND expect_pay_date!='' AND expect_pay_date<=date('now',?) AND expect_pay_date>=date('now')", ('+%d days' % warn_pay,)):
        add('应付款到期', 'orange', '应付款到期: %s' % r['payment_no'],
            '事由: %s，金额 ¥%s，期望付款日 %s' % (r['payment_reason'] or '', '%.2f' % (r['amount'] or 0), r['expect_pay_date']), 'payment', r['id'])
    # 9) 应付款逾期
    for r in c.execute("SELECT * FROM payment_requests WHERE status IN ('待审批','已通过') AND expect_pay_date!='' AND expect_pay_date<date('now')"):
        add('应付款逾期', 'red', '应付款逾期: %s' % r['payment_no'],
            '应于 %s 付款 ¥%s，已逾期，请尽快安排资金' % (r['expect_pay_date'], '%.2f' % (r['amount'] or 0)), 'payment', r['id'])
    # 10) 预付款跟踪(先款后货已付款但订单未核销)
    for r in c.execute("SELECT * FROM payment_requests WHERE trade_mode='先款后货' AND status='已付款'"):
        if r['contract_id']:
            ct2 = c.execute("SELECT order_id FROM contracts WHERE id=?", (r['contract_id'],)).fetchone()
            if ct2 and ct2['order_id']:
                po = c.execute("SELECT * FROM purchase_orders WHERE id=? AND status NOT IN ('已核销','已完成')", (ct2['order_id'],)).fetchone()
                if po:
                    add('预付款跟踪', 'red', '预付款未核销: %s' % r['payment_no'],
                        '已付款 ¥%s，对应订单 %s 长期未入库核销，请跟进' % ('%.2f' % (r['amount'] or 0), po['order_no']), 'payment', r['id'])
    # 11) 审批超时
    for r in c.execute("SELECT * FROM approval_instances WHERE status='pending' AND created_at<=datetime('now','localtime',?)", ('-%d hours' % warn_approve,)):
        add('审批超时', 'orange', '审批超时: %s#%s' % (r['biz_type'], r['biz_id']),
            '等待 %s 审批已超过%d小时，请尽快处理' % (r['role'] or '', warn_approve), r['biz_type'], r['biz_id'])
    # 12) 加急审批(标红置顶专区)
    for t, tbl, no_col, name_col in [('purchase_request','purchase_requests','req_no','purpose'),
                                      ('purchase_order','purchase_orders','order_no','item_name'),
                                      ('contract','contracts','contract_no','contract_name'),
                                      ('payment','payment_requests','payment_no','payment_reason')]:
        for r in c.execute("SELECT * FROM %s WHERE urgent=1 AND status IN ('待审批','审批通过','已通过','执行中')" % tbl):
            add('加急审批', 'red', '加急: %s' % r[no_col], '【加急】%s 请优先审批处理' % (r[name_col] or ''), t, r['id'])
    # 13) 多次驳回汇总
    for r in c.execute("SELECT biz_type, biz_id, COUNT(*) cnt FROM approval_instances WHERE status='rejected' GROUP BY biz_type, biz_id HAVING COUNT(*)>=2"):
        add('多次驳回', 'orange', '多次驳回: %s#%s' % (r['biz_type'], r['biz_id']),
            '该单据已被驳回 %d 次，请核对资料后整改重提' % r['cnt'], r['biz_type'], r['biz_id'])
    # 14) 供应商风险
    for r in c.execute("SELECT s.id, s.name, COUNT(d.id) bad FROM suppliers s LEFT JOIN deliveries d ON d.supplier=s.name AND d.sign_status='待签收' AND d.delivery_date<date('now') GROUP BY s.id HAVING bad>=2"):
        add('供应商风险', 'orange', '供应商延迟: %s' % r['name'],
            '该供应商有 %d 笔送货超期未签收，注意供货风险' % r['bad'], 'supplier', r['id'])
    for r in c.execute("SELECT id, name FROM suppliers WHERE created_at!='' AND created_at<=datetime('now','localtime','-30 days')"):
        q = c.execute("SELECT COUNT(*) FROM price_comparisons WHERE supplier=?", (r['name'],)).fetchone()[0]
        if q == 0:
            add('供应商风险', 'green', '供应商沉默: %s' % r['name'], '已超30天无报价记录，建议评估供应商活跃度', 'supplier', r['id'])
    for w in warns:
        c.execute("INSERT INTO alert_items(alert_type,level,title,content,biz_type,biz_id) VALUES(?,?,?,?,?,?)", w)
    c.commit(); c.close()

@app.route('/api/alerts')
@login_required
def api_alerts():
    """V55: 预警中心-全部待处理预警(首页集中展示)"""
    build_alerts()
    conn = db()
    rows = conn.execute("SELECT * FROM alert_items WHERE status='pending' ORDER BY CASE level WHEN 'red' THEN 0 WHEN 'orange' THEN 1 ELSE 2 END, id DESC LIMIT 50").fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/alerts/<int:aid>/process', methods=['POST'])
@login_required
def api_process_alert(aid):
    """V55: 手动标记预警已处理(处理后从预警列表清除, 留日志)"""
    conn = db()
    r = conn.execute("SELECT * FROM alert_items WHERE id=?", (aid,)).fetchone()
    if not r: conn.close(); return jsonify({'error': '预警不存在'}), 404
    conn.execute("UPDATE alert_items SET status='processed', processed_at=?, processed_by=? WHERE id=?", (now(), session['user_name'], aid))
    conn.commit(); conn.close()
    log(session['user_name'], '处理预警', '%s' % r['title'])
    return jsonify({'success': True})

@app.route('/api/alerts/log')
@login_required
def api_alerts_log():
    """V55: 预警历史日志(含已处理)"""
    conn = db()
    rows = conn.execute("SELECT * FROM alert_items ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/alerts/config', methods=['GET', 'POST'])
@login_required
def api_alerts_config():
    """V55: 预警参数后台配置(是否开启/提前天数/超时小时/呆滞天数)"""
    if request.method == 'POST':
        if not can_manage_config(): return jsonify({'error': '仅系统管理员可配置'}), 403
        d = request.json or {}
        for k in ('warn_enabled', 'warn_arrive_days', 'warn_contract_days', 'warn_pay_days', 'warn_approve_hours', 'warn_idle_days'):
            if k in d: cfg_set(k, d[k])
        changed = [k for k in ('warn_enabled','warn_arrive_days','warn_contract_days','warn_pay_days','warn_approve_hours','warn_idle_days') if k in d]
        log(session.get('user_name',''), '修改预警参数', '变更项: %s' % (','.join(changed) or '无'))
        return jsonify({'success': True})
    return jsonify({k: cfg_get(k, defv) for k, defv in [
        ('warn_enabled', '1'), ('warn_arrive_days', '3'), ('warn_contract_days', '30'),
        ('warn_pay_days', '3'), ('warn_approve_hours', '24'), ('warn_idle_days', '90')]})

# ============================================================
# V4.1 ── 飞书 API: 回调 + 管理配置
# ============================================================
@app.route('/api/feishu/callback', methods=['GET', 'POST'])
def api_feishu_callback():
    """飞书事件订阅回调: GET为URL验证, POST为事件推送(审批结果同步)"""
    if request.method == 'GET':
        token = request.args.get('token', '')
        vt = cfg_get('feishu_verification_token')
        if vt and token and token != vt: return 'invalid token', 403
        return jsonify({'challenge': request.args.get('challenge', '')})
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}
    if body.get('type') == 'url_verification':
        return jsonify({'challenge': body.get('challenge', '')})
    try:
        if body.get('encrypt'):
            body = fs_decrypt(body)
    except Exception as e:
        log('系统', '飞书回调解密失败', str(e))
        return jsonify({'code': 0})  # 返回成功, 避免飞书重试轰炸
    header = body.get('header', {}) or {}
    vt = cfg_get('feishu_verification_token')
    if vt and header.get('token') and header['token'] != vt:
        return jsonify({'code': 0})
    event = body.get('event', {}) or {}
    etype = header.get('event_type', '') or event.get('type', '')
    if 'approval' in etype:  # 审批实例状态变更: APPROVED/REJECTED/CANCELED...
        code = event.get('instance_code', '')
        status = event.get('status', '')
        if code and status:
            if status == 'APPROVED': fs_sync_result(code, 'approved')
            elif status == 'REJECTED': fs_sync_result(code, 'rejected')
            elif status in ('CANCELED', 'REVOKED', 'RECALLED', 'TERMINATED'): fs_mark_cancelled(code)
    return jsonify({'code': 0})

@app.route('/api/feishu/config', methods=['GET', 'POST'])
@login_required
def api_feishu_config():
    if request.method == 'POST':
        if not can_manage_config(): return jsonify({'error': '仅系统管理员可配置'}), 403
        d = request.json or {}
        for k in ('feishu_app_id','feishu_app_secret','feishu_verification_token','feishu_encrypt_key',
                  'feishu_report_chat','feishu_approve_hours','feishu_order_days'):
            if k in d: cfg_set(k, d[k])
        if 'feishu_enabled' in d: cfg_set('feishu_enabled', '1' if d['feishu_enabled'] else '0')
        changed = [k for k in ('feishu_app_id','feishu_app_secret','feishu_verification_token','feishu_encrypt_key',
                  'feishu_report_chat','feishu_approve_hours','feishu_order_days') if k in d]
        if 'feishu_enabled' in d: changed.append('feishu_enabled')
        log(session.get('user_name',''), '修改飞书配置', '变更项: %s' % (','.join(changed) or '无'))
        return jsonify({'success': True})
    c = db()
    rows = c.execute("SELECT key,value FROM sys_config WHERE key LIKE 'feishu_%'").fetchall()
    c.close()
    cfg = {r['key']: r['value'] for r in rows}
    cfg['feishu_approval_codes'] = fs_approval_codes()
    return jsonify(cfg)

@app.route('/api/feishu/test', methods=['POST'])
@login_required
def api_feishu_test():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    try:
        tk = fs_token()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    target = (request.json or {}).get('open_id', '')
    if target:
        ok = fs_send(target, '✅ 采购系统飞书对接测试成功', 'green')
        return jsonify({'success': ok, 'token': tk[:20] + '...', 'msg': '测试消息已发送' if ok else '消息发送失败, 请检查机器人权限'})
    return jsonify({'success': True, 'token': tk[:20] + '...'})

@app.route('/api/feishu/init-definitions', methods=['POST'])
@login_required
def api_feishu_init_defs():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    return jsonify(fs_init_definitions())

@app.route('/api/feishu/bind', methods=['POST'])
@login_required
def api_feishu_bind():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    d = request.json or {}
    c = db()
    c.execute("UPDATE users SET feishu_open_id=? WHERE id=?", (str(d.get('open_id','')).strip(), int(d.get('user_id', 0))))
    c.commit(); c.close()
    return jsonify({'success': True})

@app.route('/api/feishu/lookup', methods=['POST'])
@login_required
def api_feishu_lookup():
    """按手机号查飞书open_id(需通讯录权限)"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    mobile = str((request.json or {}).get('mobile', '')).strip()
    if not mobile: return jsonify({'success': False, 'error': '请输入手机号'})
    code_r, resp = fs_post('/contact/v3/users/batch_get_id', {'mobiles': [mobile]})
    if code_r != 0: return jsonify({'success': False, 'error': resp.get('msg', '查询失败')})
    for u in resp.get('data', {}).get('user_list', []):
        if u.get('mobile') == mobile:
            return jsonify({'success': True, 'open_id': u.get('open_id', ''), 'user_id': u.get('user_id', ''), 'name': u.get('name', '')})
    return jsonify({'success': False, 'error': '未找到该手机号对应的飞书用户'})

@app.route('/api/feishu/status', methods=['GET'])
@login_required
def api_feishu_status():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    c = db()
    bound = c.execute("SELECT COUNT(*) FROM users WHERE feishu_open_id IS NOT NULL AND feishu_open_id!=''").fetchone()[0]
    insts = c.execute("SELECT COUNT(*) FROM feishu_instances").fetchone()[0]
    pend_sync = c.execute("SELECT COUNT(*) FROM feishu_instances WHERE status='pending'").fetchone()[0]
    c.close()
    return jsonify({
        'enabled': feishu_enabled(),
        'config_ok': bool(cfg_get('feishu_app_id') and cfg_get('feishu_app_secret')),
        'token_ok': bool(_TOKEN['t']),
        'bound_users': bound, 'instances': insts, 'pending_sync': pend_sync,
        'definitions': fs_approval_codes(),
    })

@app.route('/api/feishu/remind-now', methods=['POST'])
@login_required
def api_feishu_remind_now():
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    fs_send_reminders(force=True)
    return jsonify({'success': True})

@app.route('/api/feishu/sync-instances', methods=['POST'])
@login_required
def api_feishu_sync_instances():
    """把系统内所有待审批但未在飞书发起的单据, 补发到飞书"""
    if not can_manage_config(): return jsonify({'error': '仅系统管理员'}), 403
    c = db()
    rows = c.execute("SELECT DISTINCT biz_type, biz_id FROM approval_instances WHERE status='pending'").fetchall()
    c.close()
    n = 0
    for r in rows:
        if fs_start_instance(r['biz_type'], r['biz_id']): n += 1
    return jsonify({'success': True, 'pushed': n, 'total': len(rows)})

@app.route('/api/feishu/instances', methods=['GET'])
@login_required
def api_feishu_instances():
    c = db()
    rows = c.execute("SELECT * FROM feishu_instances ORDER BY id DESC LIMIT 30").fetchall()
    c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/public-url')
def api_public_url():
    """当前公网地址(由守护脚本写入 public_url.txt; 隧道重启地址变化后自动更新)"""
    f = os.path.join(BASE, 'data', 'public_url.txt')
    try:
        return jsonify({'url': open(f).read().strip()})
    except Exception:
        return jsonify({'url': ''})

# ============================================================
# V4.3 ── 飞书统一认证 (OAuth 单点登录, 网页应用点开即用)
# ============================================================
@app.route('/api/feishu/oauth/url')
def api_feishu_oauth_url():
    """生成飞书授权URL(前端登录页跳转)"""
    app_id = cfg_get('feishu_app_id')
    if not app_id: return jsonify({'error': '飞书应用未配置, 请先在飞书设置填写 App ID'}), 400
    state = uuid.uuid4().hex[:16]
    session['oauth_state'] = state
    redirect_uri = request.url_root.rstrip('/') + '/api/feishu/oauth/callback'
    url = 'https://open.feishu.cn/open-apis/authen/v1/index?app_id=%s&redirect_uri=%s&state=%s' % (
        app_id, urllib.parse.quote(redirect_uri, safe=''), state)
    return jsonify({'url': url, 'redirect_uri': redirect_uri})

@app.route('/api/feishu/oauth/callback')
def api_feishu_oauth_callback():
    """飞书授权回调: code换token → 用户信息 → 匹配系统用户 → 登录"""
    code = request.args.get('code', '')
    state = request.args.get('state', '')
    if session.get('oauth_state') and state and state != session.get('oauth_state'):
        return 'state 校验失败, 请重新发起登录', 400
    if not code: return '缺少授权码', 400
    redirect_uri = request.url_root.rstrip('/') + '/api/feishu/oauth/callback'
    body = {'grant_type': 'authorization_code', 'client_id': cfg_get('feishu_app_id'),
            'client_secret': cfg_get('feishu_app_secret'), 'code': code, 'redirect_uri': redirect_uri}
    code_r, resp = fs_post('/authen/v2/oauth/token', body)
    if code_r != 0:
        return f'换取登录凭证失败: {resp.get("msg","")}', 400
    data = resp.get('data', {}) or {}
    utoken = data.get('access_token', '')
    open_id = data.get('open_id', '')
    ui = {}
    if utoken:
        try:
            req = urllib.request.Request(FS_API + '/authen/v1/user_info',
                                         headers={'Authorization': 'Bearer ' + utoken})
            with urllib.request.urlopen(req, timeout=10) as r:
                ui = json.loads(r.read().decode('utf-8')).get('data', {}) or {}
        except Exception:
            pass
    conn = db()
    u = conn.execute("SELECT * FROM users WHERE feishu_open_id=? AND is_active=1", (open_id,)).fetchone()
    if not u and ui.get('mobile'):
        u = conn.execute("SELECT * FROM users WHERE phone=? AND is_active=1", (ui['mobile'],)).fetchone()
    if not u:
        conn.close()
        return ('<html><body style="font-family:sans-serif;padding:40px;text-align:center">'
                f'<h3>⚠️ 飞书账号（{ui.get("name","")}）未绑定系统用户</h3>'
                '<p>请联系系统管理员，在「飞书设置 → 用户飞书绑定」中绑定你的账号</p>'
                '<p><a href="/">返回系统首页</a></p></body></html>'), 403
    session['user_id'] = u['id']; session['username'] = u['username']; session['user_name'] = u['name']; session['user_role'] = u['role']
    session['dept_id'] = u['dept_id']; session['dept_name'] = ''
    conn.close()
    log(u['name'], '飞书统一认证登录', f"open_id={open_id} 手机号={ui.get('mobile','')}")
    return redirect('/')

# ============================================================
# V4.2 ── 报表中心 + 库存盘点 (好生意刚需版补齐)
# ============================================================
@app.route('/api/reports')
@login_required
def api_reports():
    c = db()
    months = c.execute("""SELECT strftime('%Y-%m',created_at) m, SUM(total_amount) s
        FROM purchase_orders WHERE created_at >= datetime('now','localtime','-11 months')
        GROUP BY m ORDER BY m""").fetchall()
    sups = c.execute("""SELECT supplier, COUNT(*) cnt, SUM(total_amount) s FROM purchase_orders
        WHERE supplier!='' GROUP BY supplier ORDER BY s DESC LIMIT 10""").fetchall()
    cats = c.execute("""SELECT category, SUM(total_amount) s FROM purchase_orders
        WHERE category!='' GROUP BY category ORDER BY s DESC""").fetchall()
    todo = {
        '待审批': c.execute("SELECT COUNT(*) FROM approval_instances WHERE status='pending'").fetchone()[0],
        '待签收': c.execute("SELECT COUNT(*) FROM deliveries WHERE sign_status='待签收'").fetchone()[0],
        '待检验': c.execute("SELECT COUNT(*) FROM receivings WHERE status='待检验'").fetchone()[0],
        '待付款': c.execute("SELECT COUNT(*) FROM payment_requests WHERE status='待审批'").fetchone()[0],
        '库存预警': c.execute("SELECT COUNT(*) FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchone()[0],
    }
    c.close()
    return jsonify({'monthly': [dict_row(r) for r in months],
                    'suppliers': [dict_row(r) for r in sups],
                    'categories': [dict_row(r) for r in cats],
                    'todo': todo})

@app.route('/api/counts')
@login_required
def api_counts():
    c = db()
    rows = c.execute("SELECT * FROM inventory_counts ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/counts', methods=['POST'])
@login_required
def api_create_count():
    d = request.json
    c = db()
    no = gen_no('PD', 'inventory_counts', 'count_no')
    c.execute("INSERT INTO inventory_counts(count_no,status,remark) VALUES(?,?,?)", (no, '盘点中', d.get('remark','')))
    cid = c.execute("SELECT id FROM inventory_counts WHERE count_no=?", (no,)).fetchone()[0]
    items = c.execute("SELECT * FROM inventory").fetchall()
    for it in items:
        c.execute("INSERT INTO inventory_count_items(count_id,inventory_id,item_name,book_qty,actual_qty) VALUES(?,?,?,?,?)",
                  (cid, it['id'], it['item_name'], it['quantity'], it['quantity']))
    c.commit(); c.close()
    log(session['user_name'], '开始盘点', f'{no} 共{len(items)}项')
    return jsonify({'success': True, 'count_no': no, 'id': cid, 'items': len(items)})

@app.route('/api/counts/<int:cid>')
@login_required
def api_count(cid):
    c = db()
    ct = c.execute("SELECT * FROM inventory_counts WHERE id=?", (cid,)).fetchone()
    items = c.execute("SELECT * FROM inventory_count_items WHERE count_id=?", (cid,)).fetchall()
    c.close()
    return jsonify({'count': dict_row(ct), 'items': [dict_row(i) for i in items]})

@app.route('/api/counts/<int:cid>/finish', methods=['POST'])
@login_required
def api_finish_count(cid):
    """完成盘点: 差异项自动生成报溢/报损单(待审批), 审批通过后才调库存(V11.34 账实相符闭环)"""
    d = request.json
    c = db()
    diffs = 0
    for it in (d.get('items') or []):
        row = c.execute("SELECT * FROM inventory_count_items WHERE id=?", (it.get('id'),)).fetchone()
        if not row: continue
        aq = float(it.get('actual_qty', row['book_qty']))
        c.execute("UPDATE inventory_count_items SET actual_qty=? WHERE id=?", (aq, row['id']))
        if abs(aq - row['book_qty']) > 0.001:
            diffs += 1
            # V11.34: 差异 → 报溢/报损单(待审批), 不再直接改库存
            diff_qty = aq - row['book_qty']
            adj_type = '报溢' if diff_qty > 0 else '报损'
            inv = c.execute("SELECT * FROM inventory WHERE id=?", (row['inventory_id'],)).fetchone()
            adj_no = gen_no('SY' if diff_qty > 0 else 'BS', 'inventory_adjustments', 'adj_no')
            c.execute("""INSERT INTO inventory_adjustments(adj_no,adj_type,inventory_id,item_name,spec,unit,book_qty,adj_qty,reason,status,source,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (adj_no, adj_type, row['inventory_id'], row['item_name'], inv['spec'] if inv else '',
                 inv['unit'] if inv else '个', row['book_qty'], abs(diff_qty),
                 f'盘点差异自动生成: 账面{row["book_qty"]:g} 实盘{aq:g}', '待审批', '盘点', session['user_name']))
    c.execute("UPDATE inventory_counts SET status='已完成', finished_at=? WHERE id=?", (now(), cid))
    c.commit(); c.close()
    log(session['user_name'], '完成盘点', f'#{cid} 差异{diffs}项(已生成报溢/报损单待审批)')
    return jsonify({'success': True, 'diff_items': diffs})

# ============================================================
# V11.34 ── 报溢/报损单(库存账实相符)
# ============================================================
@app.route('/api/adjustments')
@login_required
def api_adjustments():
    """报溢/报损单列表"""
    c = db()
    rows = c.execute("SELECT * FROM inventory_adjustments ORDER BY id DESC LIMIT 100").fetchall()
    c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/adjustments', methods=['POST'])
@login_required
def api_create_adjustment():
    """手动报溢/报损单(未盘点也可用: 到货多/物资损坏)"""
    d = request.json
    inv_id = d.get('inventory_id')
    adj_type = d.get('adj_type', '')
    qty = float(d.get('adj_qty') or 0)
    reason = (d.get('reason') or '').strip()
    if adj_type not in ('报溢', '报损') or qty <= 0:
        return jsonify({'error': '请选择类型(报溢/报损)并填写数量'}), 400
    if not reason:
        return jsonify({'error': '请填写原因(审批依据)'}), 400
    c = db()
    inv = c.execute("SELECT * FROM inventory WHERE id=?", (inv_id,)).fetchone() if inv_id else None
    if not inv:
        c.close(); return jsonify({'error': '库存物资不存在'}), 404
    adj_no = gen_no('SY' if adj_type == '报溢' else 'BS', 'inventory_adjustments', 'adj_no')
    c.execute("""INSERT INTO inventory_adjustments(adj_no,adj_type,inventory_id,item_name,spec,unit,book_qty,adj_qty,reason,status,source,created_by)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (adj_no, adj_type, inv_id, inv['item_name'], inv['spec'] or '', inv['unit'] or '个',
         inv['quantity'] or 0, qty, reason, '待审批', '手动', session['user_name']))
    aid = c.execute("SELECT id FROM inventory_adjustments WHERE adj_no=?", (adj_no,)).fetchone()[0]
    c.commit(); c.close()
    log(session['user_name'], f'新建{adj_type}单', f'{adj_no} {inv["item_name"]} x{qty:g} {reason[:30]}')
    return jsonify({'success': True, 'adj_no': adj_no, 'id': aid})

@app.route('/api/inventory/<int:iid>/trace')
@login_required
def api_inventory_trace(iid):
    """V11.36: 库存溯源 — 该物资全部出入库/报溢报损流水"""
    c = db()
    inv = c.execute("SELECT * FROM inventory WHERE id=?", (iid,)).fetchone()
    if not inv:
        c.close(); return jsonify({'error': '库存物资不存在'}), 404
    flows = c.execute("""SELECT * FROM inventory_flows WHERE item_name=? AND (spec=? OR (?='' AND (spec='' OR spec IS NULL)))
        ORDER BY id DESC LIMIT 50""", (inv['item_name'], inv['spec'] or '', inv['spec'] or '')).fetchall()
    c.close()
    return jsonify({'inventory': dict_row(inv), 'flows': [dict_row(f) for f in flows]})

@app.route('/api/adjustments/<int:aid>/approve', methods=['POST'])
@login_required
def api_adjust_approve(aid):
    """审批报溢/报损单: 通过→调库存; 驳回→不改库存"""
    d = request.json or {}
    action = d.get('action', 'approved')
    c = db()
    r = c.execute("SELECT * FROM inventory_adjustments WHERE id=?", (aid,)).fetchone()
    if not r:
        c.close(); return jsonify({'error': '单据不存在'}), 404
    if r['status'] != '待审批':
        c.close(); return jsonify({'error': '该单据已处理'}), 400
    if action == 'rejected':
        c.execute("UPDATE inventory_adjustments SET status='已驳回' WHERE id=?", (aid,))
        c.commit(); c.close()
        log(session['user_name'], '驳回' + r['adj_type'], f"{r['adj_no']} {r['item_name']}")
        return jsonify({'success': True})
    # 通过 → 调库存
    if r['adj_type'] == '报溢':
        c.execute("UPDATE inventory SET quantity=quantity+? WHERE id=?", (r['adj_qty'], r['inventory_id']))
    else:
        # 报损: 库存不够则按现有量扣(不扣成负)
        inv = c.execute("SELECT quantity FROM inventory WHERE id=?", (r['inventory_id'],)).fetchone()
        cur = float(inv['quantity'] or 0) if inv else 0
        c.execute("UPDATE inventory SET quantity=? WHERE id=?", (max(0, cur - r['adj_qty']), r['inventory_id']))
    # V11.36: 报溢/报损写入库存流水(溯源完整闭环)
    _new_qty = c.execute("SELECT quantity FROM inventory WHERE id=?", (r['inventory_id'],)).fetchone()
    _bal = float(_new_qty[0] or 0) if _new_qty else 0
    c.execute("INSERT INTO inventory_flows(item_name,spec,unit,flow_type,doc_type,doc_id,doc_no,qty,balance_after,operator,remark,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
              (r['item_name'], r['spec'] or '', r['unit'] or '个', r['adj_type'], 'adjustment', aid, r['adj_no'],
               r['adj_qty'] if r['adj_type'] == '报溢' else -r['adj_qty'], _bal, session['user_name'],
               f'{r["adj_type"]}单{r["adj_no"]}审批通过', now()))
    c.execute("UPDATE inventory_adjustments SET status='已通过', approved_at=? WHERE id=?", (now(), aid))
    c.commit(); c.close()
    log(session['user_name'], f'{r["adj_type"]}审批通过', f"{r['adj_no']} {r['item_name']} x{r['adj_qty']:g}{r['unit']} 已调库存")
    return jsonify({'success': True})

# ============================================================
# V4.4 ── 需求文档补充: 加购下单/月度对账/合并开票/往来台账
# ============================================================
@app.route('/api/orders/from-requests', methods=['POST'])
@login_required
def api_orders_from_requests():
    """同批已通过申请合并为一张订单(多明细), 一次审批; 支持增补物资"""
    d = request.json
    req_ids = d.get('req_ids') or []
    tm = d.get('trade_mode', '货到付款')
    if tm not in ('货到付款', '先款后货'): tm = '货到付款'
    extras = d.get('extras') or []  # 增补物资: [{req_id, item_name, spec, quantity, unit, price}]
    if not req_ids:
        return jsonify({'error': '请选择待下单的采购申请'}), 400
    conn = db()
    rows = []
    used_reqs = []
    for rid in req_ids:
        pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (rid,)).fetchone()
        if not pr or pr['status'] != '已通过':
            continue
        if conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (rid,)).fetchone()[0] > 0:
            continue
        items = conn.execute("SELECT * FROM request_items WHERE req_id=?", (rid,)).fetchall()
        for it in items:
            rows.append((it['item_name'], it['spec'] or '', it['unit'] or '个', float(it['quantity']),
                         float(it['estimated_price'] or 0), rid))
        used_reqs.append(rid)
    for ex in extras:
        if not ex.get('req_id'): continue
        rows.append((ex.get('item_name',''), ex.get('spec','') or '', ex.get('unit','个') or '个',
                     float(ex.get('quantity',1)), float(ex.get('price',0) or 0), ex.get('req_id')))
    if not rows:
        conn.close(); return jsonify({'error': '没有可下单的申请（已通过且未下单）'}), 400
    no = gen_no('CG', 'purchase_orders', 'order_no', conn)
    total_qty = 0.0; grand_amt = 0.0; grand_tax = 0.0; grand_total = 0.0
    for it in rows:
        qty = it[3]; price = it[4]
        amt = qty*price; tax = amt*0.13
        total_qty += qty; grand_amt += amt; grand_tax += tax; grand_total += amt+tax
    first = rows[0]
    conn.execute("""INSERT INTO purchase_orders(order_no,req_id,item_name,spec,quantity,unit,price,amount,tax_rate,tax_amount,total_amount,
        supplier,requester,category,owner,owner_id,target_date,trade_mode,remark,urgent,attachments) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (no, used_reqs[0] if used_reqs else None, first[0], first[1], total_qty, first[2], first[4], grand_amt, 13, grand_tax, grand_total,
         d.get('supplier',''), session['user_name'], d.get('category','后勤类'), session['user_name'], session['user_id'],
         d.get('target_date',''), tm, '加购: 多申请合并下单', 1 if d.get('urgent') else 0, '[]'))
    oid = conn.execute("SELECT id FROM purchase_orders WHERE order_no=?", (no,)).fetchone()[0]
    for it in rows:
        qty = it[3]; price = it[4]; amt = qty*price; tax = amt*0.13
        conn.execute("INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,amount,tax_rate,tax_amount,total_amount,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (oid, it[0], it[1], it[2], qty, price, amt, 13, tax, amt+tax, ''))
    rno = None
    if tm == '货到付款':
        rno = gen_no('RK', 'receivings', 'receive_no', conn)
        conn.execute("INSERT INTO receivings(receive_no,delivery_id,order_id,item_name,spec,quantity,unit,qualified_qty,status,received_at,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rno, None, oid, first[0], first[1], total_qty, first[2], 0, '待入库', now(), '货到付款: 下单后自动进入入库板块(整批%d项)' % len(rows)))
    for rid in used_reqs:
        conn.execute("UPDATE purchase_requests SET status='已下单', updated_at=? WHERE id=?", (now(), rid))
    conn.commit()
    create_approvals('purchase_order', oid, grand_total)   # 一张订单一次审批
    start_instances('purchase_order', oid)
    conn.close()
    log(session['user_name'], '加购下单', '%s 合并%d项商品 ¥%.0f 模式:%s' % (no, len(rows), grand_total, tm))
    return jsonify({'success': True, 'orders': [no], 'order_no': no, 'id': oid, 'receive_no': rno,
                    'item_count': len(rows), 'total_amount': grand_total})

@app.route('/api/settlements')
@login_required
def api_settlements():
    c = db()
    rows = c.execute("SELECT * FROM settlements ORDER BY id DESC LIMIT 50").fetchall()
    c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/settlements', methods=['POST'])
@login_required
def api_create_settlement():
    """需求5-月度对账: 按合同/供应商批量生成对账单(取已入库订单金额)"""
    d = request.json
    period = d.get('period', '')       # 如 2026-07
    supplier = d.get('supplier', '')
    if not period:
        return jsonify({'error': '请选择对账月份'}), 400
    conn = db()
    # 该月份已入库/已挂账/已核销订单
    rows = conn.execute("""SELECT * FROM purchase_orders WHERE trade_mode='货到付款'
        AND status IN ('已入库','已挂账','已核销','已完成')
        AND strftime('%Y-%m', COALESCE(updated_at, created_at)) = ?
        AND (?='' OR supplier=?) ORDER BY id""", (period, supplier, supplier)).fetchall()
    if not rows:
        conn.close(); return jsonify({'error': '该月份没有符合条件的已入库订单'}), 400
    oids = [str(r['id']) for r in rows]
    total = sum(r['total_amount'] or 0 for r in rows)
    cnos = list({(c['contract_no'] or '') for c in [conn.execute("SELECT contract_no FROM contracts WHERE order_id=?", (r['id'],)).fetchone() for r in rows] if c})
    no = gen_no('DZ', 'settlements', 'settlement_no')
    conn.execute("INSERT INTO settlements(settlement_no,period,supplier,contract_ids,order_ids,total_amount,status,remark) VALUES(?,?,?,?,?,?,'待确认',?)",
                 (no, period, supplier, ','.join(cnos), ','.join(oids), total, d.get('remark','')))
    sid = conn.execute("SELECT id FROM settlements WHERE settlement_no=?", (no,)).fetchone()[0]
    conn.commit(); conn.close()
    log(session['user_name'], '生成对账单', f'{no} {period} ¥{total:.0f} 共{len(rows)}单')
    return jsonify({'success': True, 'settlement_no': no, 'id': sid, 'orders': len(rows), 'total': total})

@app.route('/api/settlements/<int:sid>')
@login_required
def api_settlement_detail(sid):
    """对账单详情: 对账单头 + 关联订单及明细"""
    conn = db()
    s = conn.execute("SELECT * FROM settlements WHERE id=?", (sid,)).fetchone()
    if not s:
        conn.close(); return jsonify({'error': '对账单不存在'}), 404
    oids = [int(x) for x in (s['order_ids'] or '').split(',') if x.strip().isdigit()]
    orders = []
    for oid in oids:
        o = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
        if not o: continue
        od = dict_row(o)
        od['items'] = [dict_row(i) for i in conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()]
        od['contract_no'] = ''
        ct = conn.execute("SELECT contract_no FROM contracts WHERE order_id=?", (oid,)).fetchone()
        if ct: od['contract_no'] = ct['contract_no']
        orders.append(od)
    conn.close()
    return jsonify({'settlement': dict_row(s), 'orders': orders})

@app.route('/api/settlements/<int:sid>/confirm', methods=['POST'])
@login_required
def api_confirm_settlement(sid):
    conn = db()
    st = conn.execute("SELECT * FROM settlements WHERE id=?", (sid,)).fetchone()
    if not st: conn.close(); return jsonify({'error': '对账单不存在'}), 404
    conn.execute("UPDATE settlements SET status='已确认', confirmed_at=? WHERE id=?", (now(), sid))
    conn.commit(); conn.close()
    log(session['user_name'], '确认对账单', f'#{sid}')
    return jsonify({'success': True})

# ============================================================
# V11.53 ── 月底三表(领导流程: 暂估/红冲/白入 导出Excel给财务)
# V11.56 ── 三表系统内展示 + 自动核对差异(不用人工核查)
# ============================================================
@app.route('/api/reports/est-view')
@login_required
def api_est_view():
    """三表数据+自动核对: 返回 暂估/红冲/白入 三组明细 + 差异核对结果"""
    conn = db()
    rows = conn.execute("SELECT * FROM receivings WHERE status IN ('已入库','已通过','审批通过') ORDER BY received_at DESC").fetchall()
    conn.close()
    est, hc, br = [], [], []
    for row in rows:
        is_est = bool(row['is_est']); has_inv = bool(row['invoice_no'])
        d = {
            'receive_no': row['receive_no'], 'item_name': row['item_name'], 'spec': row['spec'] or '',
            'quantity': row['quantity'], 'unit': row['unit'] or '个', 'amount': row['est_amount'] or 0,
            'invoice_no': row['invoice_no'] or '', 'date': str(row['received_at'] or '')[:10],
            'remark': row['remark'] or '', 'id': row['id'],
        }
        if is_est and not has_inv: est.append(d)     # 暂估未红冲(发票没回)
        elif is_est and has_inv: hc.append(d)         # 已红冲
        else: br.append(d)                            # 正式入库
    # 自动核对(系统自己比对, 不用人工):
    checks = []
    # ① 暂估未红冲 = 发票没回, 要催采购
    if est:
        checks.append({'level': 'warn', 'msg': f'⚠️ {len(est)} 张暂估入库单发票未回(采购需核对红冲)', 'count': len(est)})
    else:
        checks.append({'level': 'ok', 'msg': '✅ 暂估入库单均已红冲, 无发票未回', 'count': 0})
    # ② 红冲了但不在白入(异常: 红冲了却没转正式)
    hc_nos = set(x['receive_no'] for x in hc)
    br_nos = set(x['receive_no'] for x in br)
    abnormal = hc_nos - br_nos
    if abnormal:
        checks.append({'level': 'danger', 'msg': f'🚨 {len(abnormal)} 张已红冲但未计入白入(异常, 需检查): {", ".join(list(abnormal)[:3])}', 'count': len(abnormal)})
    else:
        checks.append({'level': 'ok', 'msg': '✅ 红冲与白入一致, 无异常', 'count': 0})
    # ③ 汇总
    checks.append({'level': 'info', 'msg': f'📊 暂估 {len(est)} 张 / 红冲 {len(hc)} 张 / 白入 {len(br)} 张', 'count': len(est) + len(hc) + len(br)})
    return jsonify({'est': est, 'hc': hc, 'br': br, 'checks': checks})

def _gen_est_export(kind):
    """生成 暂估/红冲/白入 明细表Excel
    kind: est=暂估入库明细(货到发票未到) / hc=红字冲销明细(发票到已冲) / br=白入明细(正式入库)"""
    import io as _io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    conn = db()
    rows = conn.execute("SELECT * FROM receivings WHERE status IN ('已入库','已通过','审批通过') ORDER BY received_at DESC").fetchall()
    conn.close()
    wb = Workbook(); ws = wb.active
    thin = Side(style='thin', color='B0B0B0')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    tf = Font(name='微软雅黑', size=14, bold=True)
    hf = Font(name='微软雅黑', size=10, bold=True, color='FFFFFF')
    hfill = PatternFill('solid', fgColor='2F5597')
    bf = Font(name='微软雅黑', size=10)
    titles = {'est': '暂估入库明细表', 'hc': '红字冲销明细表', 'br': '白入(正式入库)明细表'}
    heads = ['入库单号', '物资名称', '规格', '数量', '单位', '暂估金额(元)', '发票号', '入库日期', '备注']
    ws.merge_cells('A1:I1')
    ws['A1'] = titles[kind]; ws['A1'].font = tf
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 30
    for ci, h in enumerate(heads, 1):
        c = ws.cell(3, ci, h); c.font = hf; c.fill = hfill; c.border = border
        c.alignment = Alignment(horizontal='center', vertical='center')
    r = 4
    cnt = 0
    for row in rows:
        is_est = bool(row['is_est'])
        has_inv = bool(row['invoice_no'])
        # 过滤: est=暂估未红冲 / hc=已红冲 / br=正式入库
        if kind == 'est' and (not is_est or has_inv): continue
        if kind == 'hc' and not (is_est and has_inv): continue
        if kind == 'br' and is_est: continue
        vals = [row['receive_no'], row['item_name'], row['spec'] or '', row['quantity'],
                row['unit'] or '个', row['est_amount'] or 0, row['invoice_no'] or '',
                str(row['received_at'] or '')[:10], row['remark'] or '']
        for ci, v in enumerate(vals, 1):
            c = ws.cell(r, ci, v); c.border = border; c.font = bf
            c.alignment = Alignment(horizontal='center' if ci in (1, 3, 4, 5, 6, 7, 8) else 'left', vertical='center', wrap_text=True)
        ws.row_dimensions[r].height = 22
        r += 1; cnt += 1
    if cnt == 0:
        ws.merge_cells(f'A{r}:I{r}')
        ws.cell(r, 1, '（本月暂无数据）').font = bf
    widths = [16, 20, 14, 10, 6, 14, 14, 12, 26]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + j)].width = w
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    bio = _io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio, titles[kind]

@app.route('/api/reports/est-export')
@login_required
def api_est_export():
    """月底三表导出: ?kind=est|hc|br"""
    kind = request.args.get('kind', 'est')
    if kind not in ('est', 'hc', 'br'):
        return jsonify({'error': 'kind 只能是 est/hc/br'}), 400
    from flask import send_file
    bio, title = _gen_est_export(kind)
    return send_file(bio, as_attachment=True, download_name=f'{title}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/api/receivings/<int:rid>/invoice-match', methods=['POST'])
@login_required
def api_receiving_invoice_match(rid):
    """V11.53: 采购发票核对 — 暂估入库单收到发票后, 填发票号/金额 → 红冲转正式入库"""
    d = request.json or {}
    invoice_no = (d.get('invoice_no') or '').strip()
    amount = float(d.get('amount') or 0)
    invoice_type = d.get('invoice_type') or '增值税专用发票'
    if not invoice_no:
        return jsonify({'error': '请填写发票号'}), 400
    conn = db()
    rn = conn.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        conn.close(); return jsonify({'error': '入库单不存在'}), 404
    if not rn['is_est']:
        conn.close(); return jsonify({'error': '该入库单不是暂估单,无需红冲'}), 400
    if rn['invoice_no']:
        conn.close(); return jsonify({'error': f'该暂估单已红冲(发票{rn["invoice_no"]})'}), 400
    # 红冲: 暂估→正式, 记发票号+类型
    try: conn.execute("ALTER TABLE receivings ADD COLUMN invoice_type TEXT DEFAULT ''")
    except Exception: pass
    conn.execute("UPDATE receivings SET is_est=0, invoice_no=?, est_amount=?, invoice_type=? WHERE id=?",
                 (invoice_no, amount if amount > 0 else rn['est_amount'], invoice_type, rid))
    conn.commit(); conn.close()
    log(session['user_name'], '发票核对红冲', f'{rn["receive_no"]} 发票{invoice_no} 已红冲转正式')
    return jsonify({'success': True, 'receive_no': rn['receive_no']})

@app.route('/api/invoices')
@login_required
def api_invoices():
    """获取发票列表"""
    conn = db()
    rows = conn.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/invoices/merge', methods=['POST'])
@login_required
def api_merge_invoice():
    """需求5-合并结算开票: 多笔合同/订单合并生成一张发票"""
    d = request.json
    order_ids = d.get('order_ids') or []
    if not order_ids:
        return jsonify({'error': '请选择要合并开票的订单'}), 400
    conn = db()
    rows = conn.execute(f"SELECT * FROM purchase_orders WHERE id IN ({','.join('?'*len(order_ids))})", order_ids).fetchall()
    if not rows: conn.close(); return jsonify({'error': '未找到订单'}), 400
    total = sum(r['total_amount'] or 0 for r in rows)
    amt = float(d.get('amount', total))
    supplier = d.get('supplier', '') or (rows[0]['supplier'] if rows else '')
    cno = ''
    ct = conn.execute("SELECT c.* FROM contracts c WHERE c.order_id=?", (rows[0]['id'],)).fetchone()
    if ct: cno = ct['contract_no']
    inv_no = d.get('invoice_no', '')
    if not inv_no:
        conn.close(); return jsonify({'error': '请填写发票号码'}), 400
    conn.execute("INSERT INTO invoices(invoice_no,invoice_code,order_id,order_ids,contract_id,contract_no,supplier,amount,tax_amount,total_amount,invoice_date,invoice_type,file_path,status,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (inv_no, d.get('invoice_code',''), rows[0]['id'], ','.join(str(x) for x in order_ids), ct['id'] if ct else None, cno, supplier,
         amt, float(d.get('tax_amount',0)), float(d.get('total_amount', amt)), d.get('invoice_date',''), d.get('invoice_type','增值税专用发票'),
         d.get('file_path',''), '待验证', f"合并开票{len(rows)}单"))
    conn.commit(); conn.close()
    log(session['user_name'], '合并开票', f'{inv_no} 合并{len(rows)}单 ¥{amt:.0f}')
    return jsonify({'success': True, 'invoice_no': inv_no, 'orders': len(rows)})

@app.route('/api/ledger')
@login_required
def api_ledger():
    """需求4-往来台账: 按供应商/合同/时间段筛选全流程单据"""
    supplier = request.args.get('supplier', '')
    contract = request.args.get('contract', '')
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    c = db()
    def q(table, cols, date_col, extra=''):
        sql = f"SELECT {cols} FROM {table} WHERE 1=1"
        args = []
        if supplier: sql += ' AND supplier=?'; args.append(supplier)
        if contract and 'contract_no' in [r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()]:
            sql += ' AND contract_no LIKE ?'; args.append(f'%{contract}%')
        if start: sql += f' AND {date_col} >= ?'; args.append(start + ' 00:00:00')
        if end: sql += f' AND {date_col} <= ?'; args.append(end + ' 23:59:59')
        sql += extra
        return c.execute(sql, args).fetchall()
    out = []
    for r in q('purchase_orders', "id,order_no,item_name,supplier,total_amount,trade_mode,status,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '采购订单', 'no': r['order_no'], 'supplier': r['supplier'],
                    'contract': '', 'item': r['item_name'], 'amount': r['total_amount'], 'status': r['status']})
    for r in q('deliveries', "id,delivery_no,item_name,supplier,contract_no,quantity,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '到货', 'no': r['delivery_no'], 'supplier': r['supplier'],
                    'contract': r['contract_no'], 'item': r['item_name'], 'amount': None, 'status': '已登记'})
    for r in q('receivings', "id,receive_no,item_name,contract_no,quantity,status,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '入库', 'no': r['receive_no'], 'supplier': '',
                    'contract': r['contract_no'], 'item': r['item_name'], 'amount': None, 'status': r['status']})
    for r in q('invoices', "id,invoice_no,supplier,contract_no,total_amount,status,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '发票', 'no': r['invoice_no'], 'supplier': r['supplier'],
                    'contract': r['contract_no'], 'item': '', 'amount': r['total_amount'], 'status': r['status']})
    for r in q('credit_notes', "id,credit_no,supplier,contract_no,item_name,amount,status,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '挂账', 'no': r['credit_no'], 'supplier': r['supplier'],
                    'contract': r['contract_no'], 'item': r['item_name'], 'amount': r['amount'], 'status': r['status']})
    for r in q('payment_requests', "id,payment_no,supplier,contract_id,amount,trade_mode,status,created_at", 'created_at'):
        out.append({'date': r['created_at'], 'type': '付款', 'no': r['payment_no'], 'supplier': r['supplier'],
                    'contract': '', 'item': '', 'amount': r['amount'], 'status': r['status']})
    c.close()
    out.sort(key=lambda x: x['date'] or '', reverse=True)
    return jsonify(out)


# ============================================================
# V5 ── 44需求: 全局搜索/合同模板自动生成/导出/作废/权限
# ============================================================
@app.route('/api/search')
@login_required
def api_search():
    """需求44-全局搜索: 菜单/单据/档案快速检索(覆盖全部业务类目)"""
    q = request.args.get('q', '').strip()
    # 兼容GBK编码客户端(飞书/钉钉内置浏览器或部分系统按ANSI编码发送URL):
    # 若UTF-8解码失败出现替换符, 用原始字节按GBK重新解码
    if q and '\ufffd' in q:
        try:
            from urllib.parse import parse_qsl
            raw_qs = request.query_string.decode('latin-1', 'replace')
            # 指定 latin-1 保留百分号解码后的原始字节, 再整体按 GBK 还原
            for k, v in parse_qsl(raw_qs, encoding='latin-1'):
                if k == 'q':
                    q2 = v.encode('latin-1', 'replace').decode('gbk', 'replace').strip()
                    if q2:
                        q = q2
                    break
        except Exception:
            pass
    if not q: return jsonify({'orders': [], 'requests': [], 'contracts': [], 'suppliers': [], 'items': [],
                              'deliveries': [], 'receivings': [], 'credits': [], 'payments': [],
                              'inventory': [], 'invoices': [], 'settlements': [], 'approvals': []})
    like = f'%{q}%'
    c = db()
    # V11.64: 搜索范围按角色域限制(员工只搜自己的申请; 库管员不搜采购/财务)
    role = session.get('user_role')
    _own = f" AND pr.requester_id={session.get('user_id', 0)}" if role in ('员工', '部门负责人') else ''
    # 主表搜索 + 明细表联查(物资名在 request_items/order_items 里, 必须覆盖否则搜不到)
    orders = c.execute("SELECT po.id,po.order_no,po.item_name,po.supplier,po.status FROM purchase_orders po WHERE po.order_no LIKE ? OR po.item_name LIKE ? OR po.supplier LIKE ? OR po.remark LIKE ? OR po.id IN (SELECT order_id FROM order_items WHERE item_name LIKE ? OR spec LIKE ? OR remark LIKE ?) LIMIT 8",
                       (like, like, like, like, like, like, like)).fetchall()
    reqs = c.execute("SELECT pr.id,pr.req_no,pr.purpose,pr.dept,pr.status FROM purchase_requests pr WHERE pr.req_no LIKE ? OR pr.purpose LIKE ? OR pr.dept LIKE ? OR pr.requester LIKE ? OR pr.id IN (SELECT req_id FROM request_items WHERE item_name LIKE ? OR spec LIKE ? OR remark LIKE ?)" + _own + " LIMIT 8",
                     (like, like, like, like, like, like, like)).fetchall()
    cons = c.execute("SELECT id,contract_no,contract_name,supplier,status FROM contracts WHERE contract_no LIKE ? OR contract_name LIKE ? OR supplier LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    sups = c.execute("SELECT id,name,contact,phone FROM suppliers WHERE name LIKE ? OR contact LIKE ? OR phone LIKE ? OR category LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    items = c.execute("SELECT id,name,spec,unit FROM items WHERE name LIKE ? OR spec LIKE ? OR cat_code LIKE ? OR supplier LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    try:
        dels = c.execute("SELECT id,delivery_no,item_name,supplier,delivery_date,sign_status FROM deliveries WHERE delivery_no LIKE ? OR item_name LIKE ? OR supplier LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    except Exception:
        dels = []
    try:
        recs = c.execute("SELECT r.id,r.receive_no,r.item_name,COALESCE(po.supplier,'') AS supplier,r.status FROM receivings r LEFT JOIN purchase_orders po ON r.order_id=po.id WHERE r.receive_no LIKE ? OR r.item_name LIKE ? OR r.warehouse LIKE ? OR r.contract_no LIKE ? OR r.remark LIKE ? OR COALESCE(po.supplier,'') LIKE ? LIMIT 8", (like, like, like, like, like, like)).fetchall()
    except Exception:
        recs = []
    try:
        crds = c.execute("SELECT id,credit_no,supplier,amount,status FROM credit_notes WHERE credit_no LIKE ? OR supplier LIKE ? OR item_name LIKE ? OR category LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like, like, like)).fetchall()
    except Exception:
        crds = []
    try:
        pays = c.execute("SELECT id,payment_no,supplier,amount,status FROM payment_requests WHERE payment_no LIKE ? OR supplier LIKE ? OR payment_reason LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    except Exception:
        pays = []
    try:
        invs = c.execute("SELECT id,item_name,warehouse,quantity,unit FROM inventory WHERE item_name LIKE ? OR warehouse LIKE ? OR spec LIKE ? OR cat_code LIKE ? LIMIT 8", (like, like, like, like)).fetchall()
    except Exception:
        invs = []
    try:
        fins = c.execute("SELECT id,invoice_no,supplier,amount,status FROM invoices WHERE invoice_no LIKE ? OR supplier LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like)).fetchall()
    except Exception:
        fins = []
    try:
        setls = c.execute("SELECT id,settlement_no,supplier,total_amount AS amount,status FROM settlements WHERE settlement_no LIKE ? OR supplier LIKE ? OR remark LIKE ? LIMIT 8", (like, like, like)).fetchall()
    except Exception:
        setls = []
    try:
        # 审批: 关联单据编号(通过 biz_type/biz_id 反查各主表单号), 搜单号也能出审批
        apps = c.execute("SELECT ai.id,ai.biz_type,ai.biz_id,ai.role,ai.status,ai.created_at FROM approval_instances ai WHERE ai.biz_type LIKE ? OR ai.role LIKE ? OR ai.status LIKE ? OR ai.id IN (SELECT a2.id FROM approval_instances a2 WHERE a2.biz_type='purchase_request' AND a2.biz_id IN (SELECT id FROM purchase_requests WHERE req_no LIKE ?)) OR ai.id IN (SELECT a3.id FROM approval_instances a3 WHERE a3.biz_type='purchase_order' AND a3.biz_id IN (SELECT id FROM purchase_orders WHERE order_no LIKE ?)) LIMIT 8",
                         (like, like, like, like, like)).fetchall()
    except Exception:
        apps = []
    try:
        usrs = c.execute("SELECT id,username,name,role FROM users WHERE is_active=1 AND (username LIKE ? OR name LIKE ? OR role LIKE ?) LIMIT 8", (like, like, like)).fetchall()
    except Exception:
        usrs = []
    c.close()
    return jsonify({'orders': [dict_row(r) for r in orders], 'requests': [dict_row(r) for r in reqs],
                    'contracts': [dict_row(r) for r in cons], 'suppliers': [dict_row(r) for r in sups],
                    'items': [dict_row(r) for r in items], 'deliveries': [dict_row(r) for r in dels],
                    'receivings': [dict_row(r) for r in recs], 'credits': [dict_row(r) for r in crds],
                    'payments': [dict_row(r) for r in pays], 'inventory': [dict_row(r) for r in invs],
                    'invoices': [dict_row(r) for r in fins], 'settlements': [dict_row(r) for r in setls],
                    'approvals': [dict_row(r) for r in apps], 'users': [dict_row(r) for r in usrs]})

# ---- 合同模板管理 ----
@app.route('/api/contract-templates')
@login_required
def api_ctpls():
    c = db(); rows = c.execute("SELECT * FROM contract_templates ORDER BY id").fetchall(); c.close()
    return jsonify([dict_row(r) for r in rows])

@app.route('/api/contract-templates', methods=['POST'])
@admin_required
def api_ctpl_create():
    d = request.json
    c = db()
    first = c.execute("SELECT COUNT(*) FROM contract_templates").fetchone()[0] == 0
    c.execute("INSERT INTO contract_templates(name,file_path,version,status,is_default,remark) VALUES(?,?,?,?,?,?)",
              (d.get('name', '合同模板'), d.get('file_path', ''), d.get('version', 'V1'), '启用',
               1 if first or d.get('is_default') else 0, d.get('remark', '')))
    c.commit(); c.close()
    log(session.get('user_name',''), '新增合同模板', '%s (%s)' % (d.get('name','合同模板'), d.get('version','V1')))
    return jsonify({'success': True})

@app.route('/api/contract-templates/<int:tid>', methods=['POST'])
@admin_required
def api_ctpl_update(tid):
    d = request.json; c = db()
    if 'status' in d: c.execute("UPDATE contract_templates SET status=? WHERE id=?", (d['status'], tid))
    if 'name' in d: c.execute("UPDATE contract_templates SET name=? WHERE id=?", (d['name'], tid))
    if 'file_path' in d: c.execute("UPDATE contract_templates SET file_path=? WHERE id=?", (d['file_path'], tid))
    if 'is_default' in d and d['is_default']:
        c.execute("UPDATE contract_templates SET is_default=0")
        c.execute("UPDATE contract_templates SET is_default=1 WHERE id=?", (tid,))
    c.commit(); c.close()
    log(session.get('user_name',''), '修改合同模板', '模板#%s 变更: %s' % (tid, ','.join(k for k in ('status','name','file_path','is_default') if k in d) or '无'))
    return jsonify({'success': True})

@app.route('/api/contract-templates/<int:tid>', methods=['DELETE'])
@admin_required
def api_ctpl_delete(tid):
    c = db()
    tpl = c.execute("SELECT * FROM contract_templates WHERE id=?", (tid,)).fetchone()
    c.execute("DELETE FROM contract_templates WHERE id=?", (tid,)); c.commit(); c.close()
    log(session.get('user_name',''), '删除合同模板', '模板#%s %s' % (tid, tpl['name'] if tpl else ''))
    return jsonify({'success': True})

# ---- 人民币金额大写 ----
def rmb_upper(n):
    units = ['', '拾', '佰', '仟']
    bigs = ['', '万', '亿', '万亿']
    digs = '零壹贰叁肆伍陆柒捌玖'
    n = round(float(n) + 1e-9, 2)
    int_part = int(n)
    dec = int(round((n - int_part) * 100))
    jiao, fen = dec // 10, dec % 10
    if int_part == 0:
        s = '零元'
    else:
        s = ''
        segs = []
        v = int_part
        while v > 0:
            segs.append(v % 10000); v //= 10000
        for i in range(len(segs) - 1, -1, -1):
            seg = segs[i]
            zero = False
            if seg == 0:
                if s and not s.endswith('零') and i > 0:
                    s += '零'
                continue
            if s and (s.endswith('元') or s.endswith('零')):
                pass
            if s and i < len(segs) - 1 and segs[i] < 1000 and s and not s.endswith('零'):
                s += '零'
            s += fmt_wan(seg, digs, units) + bigs[i]
            if i > 0 and seg % 10 == 0:
                s = s.rstrip('零') + bigs[i]
        s = s.replace('零零', '零') + '元'
    if jiao == 0 and fen == 0:
        s += '整'
    elif jiao == 0:
        s += '零' + digs[fen] + '分'
    elif fen == 0:
        s += digs[jiao] + '角整'
    else:
        s += digs[jiao] + '角' + digs[fen] + '分'
    return s

def fmt_wan(seg, digs, units):
    s = ''
    zero = False
    for i in range(3, -1, -1):
        d = seg // (10 ** i) % 10
        if d == 0:
            if s and not s.endswith('零'):
                zero = True
        else:
            if zero:
                s += '零'; zero = False
            s += digs[d] + units[i]
    return s

# ---- 需求44: 采购合同自动生成 ----
@app.route('/api/contracts/generate', methods=['POST'])
@login_required
def api_contract_generate():
    """订单下单后一键生成采购合同: 调默认模板→自动填充甲方/乙方/明细/结算方式→生成docx归档"""
    d = request.json
    oid = d.get('order_id')
    conn = db()
    o = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
    if not o:
        conn.close(); return jsonify({'error': '订单不存在'}), 400
    sup = conn.execute("SELECT * FROM suppliers WHERE name=?", (o['supplier'],)).fetchone() if o['supplier'] else None
    tpl = None
    if d.get('template_id'):
        tpl = conn.execute("SELECT * FROM contract_templates WHERE id=?", (d['template_id'],)).fetchone()
    if not tpl:
        tpl = conn.execute("SELECT * FROM contract_templates WHERE is_default=1 AND status='启用'").fetchone()
    if not tpl:
        conn.close(); return jsonify({'error': '未找到启用的合同模板, 请到 系统设置→合同模板管理 上传模板'}), 400
    # 甲方预设
    cname = cfg_get('company_name', '正成能源有限公司')
    caddr = cfg_get('company_address', '山西省')
    ccontact = cfg_get('company_contact', '采购部')
    cphone = cfg_get('company_phone', '')
    cno = gen_contract_no(conn)
    tm = o['trade_mode'] or '货到付款'
    # V11.7: 结算方式跟随订单交易模式 — 自定义模式(如 预付30%)直接带入, 内置两种保留详细说明
    if tm == '货到付款':
        settle = '货到付款：到货验收入库后，月度对账、合并开票、挂账后付款'
    elif tm == '先款后货':
        settle = '先款后货：合同签订后预付货款，供应商收款后发货，到货入库后挂账核销'
    else:
        settle = tm
    items_txt = ''
    _oi = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()
    if _oi:
        items_txt = '\n'.join(f"{i+1}. {r['item_name']} {r['spec'] or ''} {r['quantity']}{r['unit'] or '个'} 单价¥{r['price'] or 0}" for i, r in enumerate(_oi))
    elif o['req_id']:
        rows = conn.execute("SELECT * FROM request_items WHERE req_id=?", (o['req_id'],)).fetchall()
        if rows:
            items_txt = '\n'.join(f"{i+1}. {r['item_name']} {r['spec'] or ''} {r['quantity']}{r['unit'] or '个'}" for i, r in enumerate(rows))
    else:
        items_txt = f"1. {o['item_name']} {o['spec'] or ''} {o['quantity']}{o['unit'] or '个'} 单价¥{o['price'] or 0}"
    mapping = {
        '{合同编号}': cno, '{订单编号}': o['order_no'],
        '{甲方名称}': cname, '{甲方地址}': caddr, '{甲方联系人}': ccontact, '{甲方电话}': cphone,
        '{乙方名称}': sup['name'] if sup else (o['supplier'] or ''),
        '{乙方地址}': sup['bank'] if sup else '', '{乙方联系人}': sup['contact'] if sup else '',
        '{乙方电话}': sup['phone'] if sup else '', '{乙方开户行}': sup['bank'] if sup else '',
        '{乙方账号}': sup['account'] if sup else '',
        '{下单日期}': (o['created_at'] or '')[:10], '{预计交货日期}': o['target_date'] or '',
        '{结算方式}': settle, '{明细清单}': items_txt,
        '{合计金额}': f"¥{o['total_amount'] or 0:,.2f}（人民币大写：{rmb_upper(o['total_amount'] or 0)}）",
    }
    try:
        from docx import Document
        tpl_path = os.path.join(BASE, 'uploads', tpl['file_path'])
        if not os.path.exists(tpl_path):
            conn.close(); return jsonify({'error': '模板文件缺失, 请重新上传'}), 400
        doc = Document(tpl_path)
        # 明细行(多商品订单取 order_items 汇总)
        if _oi:
            amt = sum(float(r['amount'] or 0) for r in _oi)
            tax = sum(float(r['tax_amount'] or 0) for r in _oi)
            total = sum(float(r['total_amount'] or 0) for r in _oi)
            tax_rate = float(_oi[0]['tax_rate'] or 13) if _oi else 13
        else:
            amt = float(o['amount'] or 0)
            tax_rate = float(o['tax_rate'] or 13)
            tax = amt * tax_rate / 100.0
            total = amt + tax
        # 交付天数
        days = ''
        if o['target_date']:
            try:
                d1 = datetime.datetime.strptime(o['target_date'][:10], '%Y-%m-%d').date()
                days = str(max((d1 - datetime.date.today()).days, 1))
            except Exception:
                days = ''
        today_s = datetime.date.today().strftime('%Y年 %m月 %d日')
        # V8.4: 合同文本通用处理(段落+表格共用) — 合计金额中文大写/税率/税金/不含税/收款账户/日期
        def _apply_ct(t):
            if '合计金额：¥' in t:
                return (f"合计金额：¥{total:,.2f}元（人民币大写金额：{rmb_upper(total)}）。"
                        f"税金（税率 {tax_rate:.0f}%）为：¥{tax:,.2f}元（人民币大写金额：{rmb_upper(tax)}）；"
                        f"不含税价款为：¥{amt:,.2f}元（人民币大写金额：{rmb_upper(amt)}）。")
            reps = [
                (r'合同签订后\s+日内交付', f'合同签订后{days or "30"}日内交付'),
                (r'运抵甲方指定地点后\s+日内', '运抵甲方指定地点后7日内'),
                (r'乙方应在\s+日内更换', '乙方应在15日内更换'),
                (r'质保期为\s+年', '质保期为1年'),
                (r'乙方应于\s+日内向甲方提供全额增值税', '乙方应于30日内向甲方提供全额增值税'),
                (r'收到发票后\s+日内支付合同总价的\s+%', '收到发票后30日内支付合同总价的90%'),
                (r'质保期满后若无质量纠纷，\s+日内支付剩余价款', '质保期满后若无质量纠纷，30日内支付剩余价款'),
                (r'延迟交付货物超过\s+天', '延迟交付货物超过15天'),
                (r'需提前\s+天通知对方', '需提前15天通知对方'),
            ]
            for pat, repx in reps:
                if re.search(pat, t):
                    t = re.sub(pat, repx, t)
            if '收款账户名称：' in t:
                t = t.replace('收款账户名称：', '收款账户名称：' + (sup['name'] if sup else (o['supplier'] or '')))
            if '收款账号：' in t:
                t = t.replace('收款账号：', '收款账号：' + (sup['account'] if sup and sup['account'] else ''))
            if '收款银行：' in t:
                t = t.replace('收款银行：', '收款银行：' + (sup['bank'] if sup and sup['bank'] else ''))
            elif re.search(r'20\d\d年\s*\d+\s*月\s*\d+日', t):
                t = re.sub(r'20\d\d年\s*\d+\s*月\s*\d+日', today_s, t)
            return t
        # 1) 段落: 占位符 + 框架合同字段填充
        for para in doc.paragraphs:
            t = para.text
            for k, v in mapping.items():
                if k in t:
                    t = t.replace(k, v)
            # 框架合同: 合同编码/乙方/合计金额/交付/验收/质保/结算/解除/签署日期
            # (用正则精确匹配空白占位, 不污染行首缩进)
            if '合同编码' in t and 'HT-' not in t:
                t = '合同编码：' + cno
            elif t.strip().startswith('乙方：') and len(t.strip()) <= 5:
                t = '乙方：' + (sup['name'] if sup else (o['supplier'] or '')) + '（供应方）'
            t = _apply_ct(t)
            if t != para.text:
                para.text = t
        # 2) 表格: 先处理所有单元格段落(合计金额大写/税金/税率/收款账户等), 再填明细
        for table in doc.tables:
            for _row in table.rows:
                for _cell in _row.cells:
                    for _p in _cell.paragraphs:
                        if _p.text.strip():
                            _nt = _apply_ct(_p.text)
                            if _nt != _p.text:
                                _p.text = _nt
            rows = table.rows
            if len(rows) < 3:
                continue
            header = [c.text.strip() for c in rows[0].cells]
            if '标的物' in header or '标的' in header[0] or '品名' in header[0]:
                # 明细行: 多商品订单取 order_items 逐行填充, 旧单取订单单商品
                det_rows = _oi if _oi else [None]
                # 先定位合计行(含"合计"字样的行)作为明细边界
                total_row_i = None
                for i in range(1, len(rows)):
                    cells = [cc.text.strip() for cc in rows[i].cells]
                    if any('合计' in cc for cc in cells):
                        total_row_i = i
                        break
                # 再找第一个可写空行(合计行之前)
                idx = 1
                boundary = total_row_i if total_row_i is not None else len(rows)
                for i in range(1, boundary):
                    cells = [cc.text.strip() for cc in rows[i].cells]
                    if not any(cells) or all(cc.strip() == '' for cc in cells):
                        idx = i; break
                else:
                    idx = boundary  # 合计行前无空行 → 从合计行位置开始填(会先插入)
                try:
                    # 需要行数 > 可用空行 → 插入新行(在合计行之前)
                    need = len(det_rows)
                    avail = boundary - idx
                    while avail < need:
                        from docx.oxml.ns import qn as _qn
                        new_tr = rows[idx]._tr.makeelement(_qn('w:tr'), {})
                        for _ in range(len(rows[idx].cells)):
                            tc = rows[idx]._tr.makeelement(_qn('w:tc'), {})
                            new_tr.append(tc)
                        if total_row_i is not None:
                            rows[total_row_i]._tr.addprevious(new_tr)
                            total_row_i += 1
                        else:
                            table._tbl.append(new_tr)
                        avail += 1
                    rows = table.rows
                    # 逐行填充
                    for k, oi_row in enumerate(det_rows):
                        if oi_row is not None:
                            qty_v = oi_row['quantity']
                            qty_s = str(int(qty_v)) if float(qty_v).is_integer() else str(qty_v)
                            line = [oi_row['item_name'] or '', oi_row['spec'] or '', oi_row['unit'] or '',
                                    qty_s, f"{oi_row['price'] or 0:,.2f}", f"{oi_row['amount'] or 0:,.2f}", '']
                        else:
                            qty_v = o['quantity']
                            qty_s = str(int(qty_v)) if float(qty_v).is_integer() else str(qty_v)
                            line = [o['item_name'] or '', o['spec'] or '', o['unit'] or '',
                                    qty_s, f"{o['price'] or 0:,.2f}", f"{amt:,.2f}", '']
                        tr = rows[idx + k]
                        for j, val in enumerate(line):
                            if j < len(tr.cells):
                                tr.cells[j].text = str(val)
                    # 合计行: 可能有"合计"标签行 + "合计金额：¥   元"行, 两处都要填
                    for i in range(idx + len(det_rows), len(rows)):
                        cells = [cc.text.strip() for cc in rows[i].cells]
                        joined = ' | '.join(cells)
                        if any('合计' in cc for cc in cells):
                            if '合计金额' in joined:
                                # 只替换"合计金额：¥"后的数字(税金/不含税/大写已由 _apply_ct 填好, 不能整格替换)
                                for cell in rows[i].cells:
                                    if '合计金额：¥' in cell.text:
                                        cell.text = re.sub(r'(合计金额：¥)[\d,\.\s]*(元)',
                                                           lambda m: f'{m.group(1)}{total:,.2f}{m.group(2)}', cell.text)
                                        break
                            else:
                                # V8.4: 合计行金额列同时写数字+人民币大写(一、标的表格)
                                rows[i].cells[-2].text = f"¥{total:,.2f}元\n人民币大写：{rmb_upper(total)}"
                            # 继续检查下一行是否也是"合计金额"行
                except Exception:
                    pass
            for row in rows:
                for cell in row.cells:
                    for k, v in mapping.items():
                        if k in cell.text:
                            cell.text = cell.text.replace(k, v)
        fname = f"contract_{cno}.docx"
        doc.save(os.path.join(BASE, 'uploads', fname))
        # 合同全文(供在线编辑)
        full_text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        for t in doc.tables:
            for row in t.rows:
                full_text += '\n' + ' | '.join(c.text for c in row.cells)
    except Exception as e:
        conn.close(); return jsonify({'error': f'合同生成失败: {e}'}), 500
    conn.execute("""INSERT INTO contracts(contract_no,order_id,contract_name,supplier,amount,sign_date,start_date,end_date,content,file_path,status,remark,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cno, oid, f"{o['item_name']}采购合同", o['supplier'] or '', o['total_amount'] or 0, (o['created_at'] or '')[:10],
         (o['created_at'] or '')[:10], o['target_date'], full_text, fname, '待审批', f"由订单{o['order_no']}自动生成", datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    cid = conn.execute("SELECT id FROM contracts WHERE contract_no=?", (cno,)).fetchone()[0]
    conn.commit()
    create_approvals('contract', cid, o['total_amount'] or 0)
    start_instances('contract', cid)
    conn.close()
    log(session['user_name'], '自动生成合同', f'{cno} 订单{o["order_no"]} 模式:{tm}')
    return jsonify({'success': True, 'contract_no': cno, 'file': fname})

# ---- 合同在线编辑: 读取文本(旧合同自动从Word提取) ----
@app.route('/api/contracts/<int:cid>/content')
@login_required
def api_get_contract_content(cid):
    conn = db()
    ct = conn.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    if not ct:
        conn.close(); return jsonify({'error': '合同不存在'}), 404
    text = ct['content'] or ''
    if not text and ct['file_path']:
        try:
            from docx import Document
            doc = Document(os.path.join(BASE, 'uploads', ct['file_path']))
            text = '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
            for t in doc.tables:
                for row in t.rows:
                    text += '\n' + ' | '.join(cc.text for cc in row.cells)
        except Exception:
            pass
    conn.close()
    return jsonify({'content': text})

# ---- 合同在线编辑: 修改文本→重建Word文件归档 ----
@app.route('/api/contracts/<int:cid>/content', methods=['POST'])
@login_required
def api_save_contract_content(cid):
    """需求44: 允许操作人员手动修改合同文本、补充特殊约定条款;
    保存后按编辑文本重建Word合同文件, 与原订单绑定归档"""
    d = request.json
    text = (d.get('content') or '').strip()
    if not text:
        return jsonify({'error': '合同内容不能为空'}), 400
    conn = db()
    ct = conn.execute("SELECT * FROM contracts WHERE id=?", (cid,)).fetchone()
    if not ct:
        conn.close(); return jsonify({'error': '合同不存在'}), 404
    conn.execute("UPDATE contracts SET content=?, updated_at=? WHERE id=?", (text, now(), cid))
    # 重建 Word 文件: 保留明细表格, 正文段落替换为编辑后文本
    try:
        from docx import Document
        from docx.oxml.ns import qn
        orig = os.path.join(BASE, 'uploads', ct['file_path']) if ct['file_path'] else ''
        fname = ct['file_path'] or f"contract_{ct['contract_no']}.docx"
        if orig and os.path.exists(orig):
            doc = Document(orig)
        else:
            tpl_def = conn.execute("SELECT file_path FROM contract_templates WHERE is_default=1 AND status='启用'").fetchone()
            doc = Document(os.path.join(BASE, 'uploads', tpl_def['file_path']))
        body = doc.element.body
        # 清除所有段落(保留表格)
        for child in list(body):
            if child.tag != qn('w:tbl'):
                body.remove(child)
        # 按编辑文本逐行重建段落, 插到表格之前
        lines = text.split('\n')
        tbl = body.find(qn('w:tbl'))
        for line in lines:
            p = doc.add_paragraph(line)
            if tbl is not None:
                tbl.addprevious(p._element)
        doc.save(os.path.join(BASE, 'uploads', fname))
    except Exception as e:
        conn.close(); return jsonify({'error': f'合同文件更新失败: {e}'}), 500
    conn.commit(); conn.close()
    log(session['user_name'], '编辑合同', f'#{cid} {ct["contract_no"]}')
    return jsonify({'success': True, 'file': fname})

# ---- 文件上传: 申请附件/付款附件/合同模板等 ----
@app.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    """通用文件上传: multipart 表单 file 字段 → 存 uploads/ → 返回 {file_path}"""
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': '未选择文件'}), 400
    fn = os.path.basename(f.filename)  # 去路径, 防穿越
    if not fn or fn in ('.', '..'):
        return jsonify({'error': '非法文件名'}), 400
    # 限制扩展名: 文档/图片/表格/视频(V11.26: 入库验收照片视频)
    ext = os.path.splitext(fn)[1].lower()
    allow = {'.doc', '.docx', '.pdf', '.xls', '.xlsx', '.png', '.jpg', '.jpeg', '.gif', '.txt', '.zip', '.rar', '.mp4', '.mov', '.avi', '.webm'}
    if ext not in allow:
        return jsonify({'error': f'不支持的文件类型 {ext or "空"}（允许: doc/docx/pdf/xls/xlsx/图片/视频/zip/rar）'}), 400
    os.makedirs(os.path.join(BASE, 'uploads'), exist_ok=True)
    # 时间戳前缀防重名
    ts = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    store = f"{ts}_{fn}"
    f.save(os.path.join(BASE, 'uploads', store))
    log(session['user_name'], '上传文件', store)
    return jsonify({'success': True, 'file_path': store})

# ---- 文件下载/预览: 合同Word/附件等 ----
@app.route('/uploads/<path:filename>')
def api_upload_file(filename):
    """下载/预览上传的文件(合同docx等), 安全校验防路径穿越"""
    if '..' in filename or filename.startswith('/'):
        return 'bad path', 400
    d = os.path.join(BASE, 'uploads')
    full = os.path.join(d, filename)
    if not os.path.exists(full):
        return '文件不存在', 404
    from flask import send_from_directory
    if filename.lower().endswith('.docx'):
        return send_from_directory(d, filename, as_attachment=False,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            download_name=filename)
    return send_from_directory(d, filename, as_attachment=True)

# ---- V55: 采购申请单下载(单个申请生成标准xlsx含明细行) ----
@app.route('/api/prequests/<int:rid>/download')
@login_required
def api_prequest_download(rid):
    """按桌面模板《物资采购申请单》格式生成 xlsx:
    A1:L2 合并标题行(含申请部门/申请日期/编号), 12列表头, 明细, 底部签字区"""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    conn = db()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (rid,)).fetchone()
    if not pr:
        conn.close(); return jsonify({'error': '申请单不存在'}), 404
    items = conn.execute("SELECT * FROM request_items WHERE req_id=? ORDER BY id", (rid,)).fetchall()
    # 库存量(按物品名称汇总, 供 J列展示)
    stock_map = {}
    for inv in conn.execute("SELECT item_name, quantity FROM inventory WHERE quantity>0").fetchall():
        stock_map[inv['item_name']] = stock_map.get(inv['item_name'], 0) + inv['quantity']
    conn.close()
    wb = Workbook(); ws = wb.active; ws.title = '物资采购申请单'
    thin = Side(style='thin'); border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill('solid', fgColor='D9E2F3')
    # V8.4: 全表统一宋体(兼容WPS/其他电脑)
    CN = lambda bold=False, size=11: Font(name='宋体', bold=bold, size=size)
    # ── 标题行 A1:L2 合并: 标题 + 部门/日期/编号 ──
    ws.merge_cells('A1:K2')
    d = pr['apply_date'] or pr['created_at'] or ''
    ds = str(d)[:10]
    try:
        if '年' in ds:
            # 中文格式 2026年8月6日
            y, m, dd = re.split(r'[年月日]', ds)[:3]
            date_cn = f'{int(y)}年{int(m)}月{int(dd)}日'
        else:
            y, m, dd = ds.split('-')
            date_cn = f'{y}年{int(m)}月{int(dd)}日'
    except Exception:
        date_cn = ds
    ws['A1'] = f'物资采购申请单\n 申请部门：{pr["dept"] or ""}        申请日期：{date_cn}        编号：{pr["req_no"]}'
    ws['A1'].font = CN(bold=True, size=16)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 42
    # ── 表头行 (第3行, 11列 — V11.47去掉到货日期列) ──
    headers = ['序号', '采购类别', '物品名称', '厂家/品牌/技术参数', '规格型号', '单位',
               '数量', '用途', '库存', '请购数量', '备注']
    for j, h in enumerate(headers, 1):
        cell = ws.cell(row=3, column=j, value=h)
        cell.font = CN(bold=True); cell.fill = fill; cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 28
    # ── 明细行 (第4行起) ──
    r = 4
    from openpyxl.drawing.image import Image as XLImage
    import os as _os
    for i, it in enumerate(items, 1):
        use = (it['remark'] or '').strip() or (pr['purpose'] or '')
        cat = it['category'] if 'category' in it.keys() and it['category'] else ''
        brand = it['brand_param'] if 'brand_param' in it.keys() and it['brand_param'] else ''
        # V11.46: 行级附件图片(备注列插图)
        _attach = (it['attach'] if 'attach' in it.keys() and it['attach'] else '').strip()
        remark_txt = it['remark'] or ''
        # V11.47b: 填了到货日期 → 备注带"需到货:xx"(没填不显示)
        _arr = (it['arrival_date'] if 'arrival_date' in it.keys() and it['arrival_date'] else '') or ''
        if _arr:
            _arr = str(_arr)[:10]
            remark_txt = (remark_txt + ' ' if remark_txt else '') + f'【需到货:{_arr}】'
        vals = [i, cat, it['item_name'], brand, it['spec'] or '', it['unit'] or '个', it['quantity'],
                use, stock_map.get(it['item_name'], 0), it['quantity'], remark_txt]
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=j, value=v)
            cell.border = border
            cell.font = CN()
            # V11.44: 自动换行, 长文字(厂家/技术参数/规格/用途)不被遮挡
            cell.alignment = Alignment(horizontal='center' if j in (1, 2, 3, 5, 6, 7, 10, 11) else 'left',
                                       vertical='center', wrap_text=True)
        # V11.46: 备注列插入行级附件图片
        if _attach:
            _img_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'uploads', _attach.split('/')[-1])
            if _os.path.exists(_img_path):
                try:
                    img = XLImage(_img_path)
                    img.width = min(img.width, 90); img.height = min(img.height, 90)
                    _anchor = f'K{r}'
                    ws.add_image(img, _anchor)
                except Exception:
                    pass
        # V11.44: 行高按内容自动计算(参考模板: 内容多行高30-93), 至少22; 有图的行加高到90
        _maxlen = max(len(str(v or '')) for v in vals)
        _rows_h = max(22, min(90, 22 + int(_maxlen / 6) * 9))
        if _attach:
            _rows_h = max(_rows_h, 90)
        ws.row_dimensions[r].height = _rows_h
        r += 1
    # ── 底部签字区 (动态下移) ──
    sign1 = r + 1
    sign2 = sign1 + 2
    ws.merge_cells(f'A{sign1}:K{sign1 + 1}')
    ws.cell(row=sign1, column=1, value='经办人：                        部门负责人：')
    ws.cell(row=sign1, column=1).font = CN()
    ws.cell(row=sign1, column=1).alignment = Alignment(horizontal='left', vertical='center')
    for rr in range(sign1, sign1 + 2):
        for cc in range(1, 12):
            ws.cell(row=rr, column=cc).border = border
    ws.merge_cells(f'A{sign2}:K{sign2 + 2}')
    ws.cell(row=sign2, column=1, value='计划采购负责人：                         采购员：')
    ws.cell(row=sign2, column=1).font = CN()
    ws.cell(row=sign2, column=1).alignment = Alignment(horizontal='left', vertical='center')
    for rr in range(sign2, sign2 + 3):
        for cc in range(1, 12):
            ws.cell(row=rr, column=cc).border = border
    # ── 列宽: 对齐桌面模板 申请单.xlsx ──
    widths = [5.1, 10.9, 24.6, 12.1, 23.4, 5.2, 8.2, 17.2, 7.3, 9.0, 14.0]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + j)].width = w
    # V11.27: 审批通过 → 盖章领导预录签名
    stamp_leader_sign(ws, sign1, 'purchase_request', rid)
    # ── 打印设置: A4 横向 ──
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.fitToWidth = 1
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    from flask import send_file
    return send_file(bio, as_attachment=True, download_name=f'{pr["req_no"]}物资采购申请单.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ---- V11.4: 采购订单下载(单个订单生成标准xlsx, 含商家信息/明细/金额/审批签名) ----
@app.route('/api/orders/<int:oid>/download')
@login_required
def api_order_download(oid):
    """生成标准《采购订单》xlsx: 标题行+基础信息(单号/供应商/交易模式/金额)+商品明细+合计+审批进度(含电子签名)+签字区"""
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.drawing.image import Image as XLImage
    import base64
    conn = db()
    o = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (oid,)).fetchone()
    if not o:
        conn.close(); return jsonify({'error': '订单不存在'}), 404
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()
    approvals = conn.execute("SELECT * FROM approval_instances WHERE biz_type='purchase_order' AND biz_id=? ORDER BY level_no", (oid,)).fetchall()
    pr = conn.execute("SELECT * FROM purchase_requests WHERE id=?", (o['req_id'],)).fetchone() if o['req_id'] else None
    conn.close()
    wb = Workbook(); ws = wb.active; ws.title = '采购订单'
    thin = Side(style='thin'); border = Border(left=thin, right=thin, top=thin, bottom=thin)
    fill = PatternFill('solid', fgColor='D9E2F3')
    CN = lambda bold=False, size=11: Font(name='宋体', bold=bold, size=size)
    # ── 标题行 ──
    ws.merge_cells('A1:H1')
    ws['A1'] = '采 购 订 单'
    ws['A1'].font = CN(bold=True, size=18)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 36
    # ── 单据编号行 ──
    ws.merge_cells('A2:H2')
    ws['A2'] = '订单编号：%s' % o['order_no']
    ws['A2'].font = CN(size=11)
    ws['A2'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[2].height = 20
    # ── 基础信息区 ──
    info = [
        ('供应商', o['supplier'] or '', '交易模式', o['trade_mode'] or ''),
        ('需求部门', o['requester'] or '', '品类', o['category'] or ''),
        ('负责人', o['owner'] or '', '下单日期', str(o['created_at'] or '')[:10]),
        ('目标到货日', o['target_date'] or '', '订单状态', o['status'] or ''),
        ('订单金额', '¥%s' % format(float(o['total_amount'] or 0), ',.2f'), '', ''),
        ('询价/备注', (o['remark'] or '')[:80], '', ''),
    ]
    r = 4
    for lk, lv, rk, rv in info:
        ws.cell(r, 1, lk).font = CN(bold=True); ws.cell(r, 1).fill = fill
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
        ws.cell(r, 2, lv).font = CN()
        if rk:
            ws.cell(r, 4, rk).font = CN(bold=True); ws.cell(r, 4).fill = fill
            ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=8)
            ws.cell(r, 5, rv).font = CN()
        else:
            ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=8)
        for cc in range(1, 9):
            ws.cell(r, cc).border = border
        ws.row_dimensions[r].height = 22
        r += 1
    # ── 商品明细 ──
    r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
    ws.cell(r, 1, '商品明细').font = CN(bold=True); ws.cell(r, 1).fill = fill
    for cc in range(1, 9): ws.cell(r, cc).border = border
    r += 1
    headers = ['序号', '物资名称', '规格型号', '单位', '数量', '单价(元)', '税率%', '金额(元)']
    for j, h in enumerate(headers, 1):
        cell = ws.cell(r, j, h); cell.font = CN(bold=True); cell.fill = fill; cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center')
    r += 1
    total_qty = 0.0; total_amt = 0.0
    for i, it in enumerate(items, 1):
        vals = [i, it['item_name'], it['spec'] or '', it['unit'] or '个', it['quantity'],
                it['price'] or 0, it['tax_rate'] or 13, it['total_amount'] or it['amount'] or 0]
        total_qty += float(it['quantity'] or 0); total_amt += float(it['total_amount'] or it['amount'] or 0)
        for j, v in enumerate(vals, 1):
            cell = ws.cell(r, j, v); cell.border = border; cell.font = CN()
            if j in (1, 3, 4, 5, 6, 7): cell.alignment = Alignment(horizontal='center', vertical='center')
        ws.row_dimensions[r].height = 20
        r += 1
    ws.cell(r, 4, '合计').font = CN(bold=True)
    ws.cell(r, 5, total_qty).font = CN(bold=True)
    ws.cell(r, 8, round(total_amt, 2)).font = CN(bold=True)
    for cc in range(1, 9): ws.cell(r, cc).border = border
    # ── 审批进度(含电子签名) ──
    if approvals:
        r += 2
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        ws.cell(r, 1, '审批进度').font = CN(bold=True); ws.cell(r, 1).fill = fill
        for cc in range(1, 9): ws.cell(r, cc).border = border
        r += 1
        for a in approvals:
            st = {'approved': '✅通过', 'rejected': '❌驳回', 'pending': '⏳待审批'}.get(a['status'], a['status'])
            # V11.6: 钉钉审批记录显示为"钉钉OA电子审批"(审批人实名认证, 与手写签名同效)
            apv = a['approver'] or ''
            if apv == '钉钉':
                apv = '钉钉OA电子审批'
            line = '%s ｜ %s ｜ %s %s' % (a['role'] or '', st, apv, str(a['processed_at'] or '')[:16])
            if a['comment']:
                line += ' ｜ ' + (a['comment'] or '')
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
            ws.cell(r, 1, line).font = CN(size=10)
            for cc in range(1, 9): ws.cell(r, cc).border = border
            # 电子签名图片(系统内手写签名) / 钉钉审批标记
            if a['signature'] and a['signature'].startswith('data:image/png'):
                try:
                    imgdata = base64.b64decode(a['signature'].split(',')[1])
                    img = XLImage(io.BytesIO(imgdata))
                    img.width = 70; img.height = 30
                    ws.add_image(img, 'H%d' % r)
                except Exception:
                    pass
            else:
                ws.cell(r, 8, '✔️' if (a['approver'] or '') == '钉钉' and a['status'] == 'approved' else '').font = Font(name='宋体', size=12, color='2F5597')
            ws.row_dimensions[r].height = 32
            r += 1
    # ── 签字区 ──
    r += 1
    ws.merge_cells(f'A{r}:H{r + 1}')
    ws.cell(row=r, column=1, value='采购经办人：                        部门负责人：')
    ws.cell(row=r, column=1).font = CN()
    ws.cell(row=r, column=1).alignment = Alignment(horizontal='left', vertical='center')
    for rr in range(r, r + 2):
        for cc in range(1, 9): ws.cell(row=rr, column=cc).border = border
    # ── 列宽/打印 ──
    widths = [6, 22, 18, 7, 9, 12, 8, 14]
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + j)].width = w
    # V11.27: 审批通过 → 盖章领导预录签名
    stamp_leader_sign(ws, r, 'purchase_order', oid)
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = 9
    ws.page_setup.fitToWidth = 1
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    from flask import send_file
    return send_file(bio, as_attachment=True, download_name=f'{o["order_no"]}采购订单.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ---- 单据导出 xlsx ----
@app.route('/api/export')
@login_required
def api_export():
    t = request.args.get('type', 'orders')
    from openpyxl import Workbook
    import io
    wb = Workbook(); ws = wb.active
    c = db()
    if t == 'orders':
        ws.append(['订单号', '物资', '规格', '数量', '单位', '单价', '含税总价', '供应商', '交易模式', '状态', '创建时间'])
        for r in c.execute("SELECT * FROM purchase_orders ORDER BY id DESC"):
            ws.append([r['order_no'], r['item_name'], r['spec'], r['quantity'], r['unit'], r['price'], r['total_amount'], r['supplier'], r['trade_mode'], r['status'], r['created_at']])
    elif t == 'requests':
        ws.append(['申请单号', '部门', '用途', '金额', '状态', '申请日期'])
        for r in c.execute("SELECT * FROM purchase_requests ORDER BY id DESC"):
            ws.append([r['req_no'], r['dept'], r['purpose'], r['total_estimated'], r['status'], r['created_at']])
    elif t == 'inventory':
        # V55需求9: 库存报表独立导出, 支持按分类展示(参数 cat=品类代码)
        # V5.0: 物料名称前新增【供应商】列
        cat = request.args.get('cat', '')
        ws.append(['序号', '供应商', '物料名称', '规格型号', '品类', '单位', '剩余库存量', '不含税单价', '税率%', '含税单价', '合计', '备注'])
        q = "SELECT * FROM inventory"
        params = ()
        if cat:
            q += " WHERE cat_code=?"
            params = (cat,)
        q += " ORDER BY cat_code, id"
        i = 0
        for r in c.execute(q, params):
            i += 1
            tr = float(r['tax_rate'] or 13)
            pt = round((r['price'] or 0) * (1 + tr / 100.0), 4)
            total = round((r['quantity'] or 0) * pt, 2)
            ws.append([i, r['supplier'] or '', r['item_name'], r['spec'], r['cat_code'], r['unit'], r['quantity'], r['price'] or 0, tr, pt, total, r['remark'] or ''])
        ws.column_dimensions['A'].width = 6; ws.column_dimensions['B'].width = 18
        ws.column_dimensions['C'].width = 16; ws.column_dimensions['D'].width = 8
        ws.column_dimensions['E'].width = 6; ws.column_dimensions['F'].width = 8
        ws.column_dimensions['G'].width = 12; ws.column_dimensions['H'].width = 8
        ws.column_dimensions['I'].width = 12; ws.column_dimensions['J'].width = 12
        ws.column_dimensions['K'].width = 20
    elif t == 'credits':
        ws.append(['挂账单号', '订单', '合同', '供应商', '金额', '状态', '创建时间'])
        for r in c.execute("SELECT * FROM credit_notes ORDER BY id DESC"):
            ws.append([r['credit_no'], r['order_id'], r['contract_no'], r['supplier'], r['amount'], r['status'], r['created_at']])
    elif t == 'payments':
        ws.append(['付款单号', '供应商', '金额', '模式', '状态', '创建时间'])
        for r in c.execute("SELECT * FROM payment_requests ORDER BY id DESC"):
            ws.append([r['payment_no'], r['supplier'], r['amount'], r['trade_mode'], r['status'], r['created_at']])
    elif t == 'contracts':
        ws.append(['合同号', '订单', '名称', '供应商', '金额', '状态', '创建时间'])
        for r in c.execute("SELECT * FROM contracts ORDER BY id DESC"):
            ws.append([r['contract_no'], r['order_id'], r['contract_name'], r['supplier'], r['amount'], r['status'], r['created_at']])
    else:
        ws.append(['名称', '联系人', '电话', '类别', '评级'])
        for r in c.execute("SELECT * FROM suppliers ORDER BY id"):
            ws.append([r['name'], r['contact'], r['phone'], r['category'], r['rating']])
    c.close()
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    from flask import send_file
    return send_file(bio, as_attachment=True, download_name=f'{t}_{today()}.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

# ---- 单据作废 ----
@app.route('/api/orders/<int:oid>/void', methods=['POST'])
@login_required
def api_void_order(oid):
    c = db(); c.execute("UPDATE purchase_orders SET status='已作废' WHERE id=?", (oid,)); c.commit(); c.close()
    log(session['user_name'], '作废订单', f'#{oid}')
    return jsonify({'success': True})

@app.route('/api/contracts/<int:cid>/void', methods=['POST'])
@login_required
def api_void_contract(cid):
    c = db(); c.execute("UPDATE contracts SET status='已作废' WHERE id=?", (cid,)); c.commit(); c.close()
    log(session['user_name'], '作废合同', f'#{cid}')
    return jsonify({'success': True})

@app.route('/api/credits/<int:cid>/void', methods=['POST'])
@login_required
def api_void_credit(cid):
    c = db(); c.execute("UPDATE credit_notes SET status='已作废' WHERE id=?", (cid,)); c.commit(); c.close()
    log(session['user_name'], '作废挂账', f'#{cid}')
    return jsonify({'success': True})


@app.route('/api/payment_requests')
@login_required
def api_payment_requests():
    """获取付款申请列表"""
    conn = db()
    rows = conn.execute("SELECT * FROM payment_requests ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        out.append(d)
    conn.close()
    return jsonify(out)
@app.route('/api/payments')
@login_required
def api_payments():
    """获取付款列表"""
    conn = db()
    rows = conn.execute("SELECT * FROM payment_requests ORDER BY id DESC LIMIT 100").fetchall()
    out = []
    for r in rows:
        d = dict_row(r)
        out.append(d)
    conn.close()
    return jsonify(out)

@app.route('/api/payments/<int:pid>/void', methods=['POST'])
@login_required
def api_void_payment(pid):
    c = db(); c.execute("UPDATE payment_requests SET status='已作废' WHERE id=?", (pid,)); c.commit(); c.close()
    log(session['user_name'], '作废付款', f'#{pid}')
    return jsonify({'success': True})

@app.route('/api/receivings/<int:rid>/void', methods=['POST'])
@login_required
def api_receiving_void(rid):
    """V5.0: 入库单作废 — 已入库(已加库存)作废则回滚库存+写流水; 未入库直接作废"""
    c = db()
    rn = c.execute("SELECT * FROM receivings WHERE id=?", (rid,)).fetchone()
    if not rn:
        c.close(); return jsonify({'error': '入库单不存在'}), 404
    if rn['status'] == '已作废':
        c.close(); return jsonify({'error': '该入库单已作废'}), 400
    if rn['status'] == '已入库':
        # 回滚库存: 删除该单入库流水 + 扣回库存
        flows = c.execute("SELECT * FROM inventory_flows WHERE doc_type='receiving' AND doc_id=? AND flow_type='入库'", (rid,)).fetchall()
        for f in flows:
            inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? ORDER BY quantity DESC",
                            (f['item_name'], f['spec'] or '')).fetchone()
            if inv:
                new_q = inv['quantity'] - f['qty']
                c.execute("UPDATE inventory SET quantity=?, updated_at=? WHERE id=?", (new_q, now(), inv['id']))
        c.execute("DELETE FROM inventory_flows WHERE doc_type='receiving' AND doc_id=?", (rid,))
        c.execute("UPDATE receivings SET status='已作废', updated_at=? WHERE id=?", (now(), rid))
        c.execute("UPDATE approval_instances SET status='rejected', comment='单据作废' WHERE biz_type='receiving' AND biz_id=? AND status='pending'", (rid,))
        c.commit(); c.close()
        log(session['user_name'], '作废入库单', f'{rn["receive_no"]} (已入库, 库存已回滚)')
        return jsonify({'success': True, 'message': '入库单已作废，库存已回滚'})
    c.execute("UPDATE receivings SET status='已作废', updated_at=? WHERE id=?", (now(), rid))
    c.execute("UPDATE approval_instances SET status='rejected', comment='单据作废' WHERE biz_type='receiving' AND biz_id=? AND status='pending'", (rid,))
    c.commit(); c.close()
    log(session['user_name'], '作废入库单', f'{rn["receive_no"]} (未入库, 库存未变动)')
    return jsonify({'success': True, 'message': '入库单已作废（未入库，库存未变动）'})

@app.route('/api/requisitions/<int:rid>/void', methods=['POST'])
@login_required
def api_requisition_void(rid):
    """V5.0: 出库单作废 — 已出库(已扣库存)作废则回滚库存+写流水; 未出库直接作废"""
    c = db()
    rq = c.execute("SELECT * FROM requisitions WHERE id=?", (rid,)).fetchone()
    if not rq:
        c.close(); return jsonify({'error': '出库单不存在'}), 404
    if rq['status'] == '已作废':
        c.close(); return jsonify({'error': '该出库单已作废'}), 400
    if rq['status'] == '已出库':
        # 回滚库存: 删除该单出库流水 + 加回库存
        flows = c.execute("SELECT * FROM inventory_flows WHERE doc_type='requisition' AND doc_id=? AND flow_type='出库'", (rid,)).fetchall()
        for f in flows:
            inv = c.execute("SELECT * FROM inventory WHERE item_name=? AND spec=? ORDER BY quantity DESC",
                            (f['item_name'], f['spec'] or '')).fetchone()
            if inv:
                new_q = inv['quantity'] - f['qty']  # f['qty'] 为负值, 减负=加回
                c.execute("UPDATE inventory SET quantity=?, updated_at=? WHERE id=?", (new_q, now(), inv['id']))
            else:
                c.execute("INSERT INTO inventory(item_name,spec,unit,quantity,warehouse,created_at) VALUES(?,?,?,?,?,?)",
                          (f['item_name'], f['spec'] or '', f['unit'] or '个', -f['qty'], '主库房', now()))
        c.execute("DELETE FROM inventory_flows WHERE doc_type='requisition' AND doc_id=?", (rid,))
        c.execute("UPDATE requisitions SET status='已作废', updated_at=? WHERE id=?", (now(), rid))
        c.execute("UPDATE approval_instances SET status='rejected', comment='单据作废' WHERE biz_type='requisition' AND biz_id=? AND status='pending'", (rid,))
        c.commit(); c.close()
        log(session['user_name'], '作废出库单', f'{rq["req_no"]} (已出库, 库存已回滚)')
        return jsonify({'success': True, 'message': '出库单已作废，库存已回滚'})
    c.execute("UPDATE requisitions SET status='已作废', updated_at=? WHERE id=?", (now(), rid))
    c.execute("UPDATE approval_instances SET status='rejected', comment='单据作废' WHERE biz_type='requisition' AND biz_id=? AND status='pending'", (rid,))
    c.commit(); c.close()
    log(session['user_name'], '作废出库单', f'{rq["req_no"]} (未出库, 库存未变动)')
    return jsonify({'success': True, 'message': '出库单已作废（未出库，库存未变动）'})

@app.route('/api/docs/<biz_type>/<int:bid>/withdraw', methods=['POST'])
@login_required
def api_doc_withdraw(biz_type, bid):
    """V8.0: 撤回审批 — 每个环节即使已审批也可撤回
    规则: 审批中(待审批/部分通过)或已通过 → 撤回 → 回到'待审批'重新走完整流程
    - 撤回后原审批实例作废, 重新生成审批链(按当前配置)
    - 钉钉实例终止(若有), 重新发起
    - 已执行库存操作(入库已入库/出库已出库)的: 需先作废回滚, 不可直接撤回
    - 已生成下游单据(申请→订单/订单→入库/合同)的: 禁止撤回"""
    if not can_manage_config():
        return jsonify({'error': '仅系统管理员可撤回审批'}), 403
    if biz_type not in _DELETE_TABLE:
        return jsonify({'error': f'不支持的撤回类型: {biz_type}'}), 400
    table = _DELETE_TABLE[biz_type]
    no_col = _DELETE_NO_COL[biz_type]
    biz = _DELETE_BIZTYPE[biz_type]
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (bid,)).fetchone()
    if not row:
        conn.close(); return jsonify({'error': '单据不存在'}), 404
    status = row['status'] if 'status' in row.keys() else ''
    no = row[no_col] if no_col in row.keys() else str(bid)
    # 状态校验: 仅 待审批/审批中/已通过 可撤回
    if status in ('已驳回', '已作废', '草稿'):
        conn.close(); return jsonify({'error': f'当前状态({status})无需撤回'}), 400
    # 下游保护
    if biz_type == 'purchase_request':
        if conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该申请已被订单引用，无法撤回（可先删除订单）'}), 400
    if biz_type == 'purchase_order':
        if conn.execute("SELECT COUNT(*) FROM receivings WHERE order_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该订单已有入库记录，无法撤回（可先作废入库）'}), 400
        if conn.execute("SELECT COUNT(*) FROM contracts WHERE order_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该订单已生成合同，无法撤回（可先作废合同）'}), 400
    # 库存操作保护
    if biz_type == 'receiving' and status == '已入库':
        conn.close(); return jsonify({'error': '该入库单已完成入库(已加库存)，请先作废回滚后再撤回'}), 400
    if biz_type == 'requisition' and status == '已出库':
        conn.close(); return jsonify({'error': '该出库单已完成出库(已扣库存)，请先作废回滚后再撤回'}), 400
    # 撤回: 状态回待审批, 原审批实例作废, 重新生成
    conn.execute(f"UPDATE {table} SET status='待审批', updated_at=? WHERE id=?", (now(), bid))
    conn.execute("UPDATE approval_instances SET status='rejected', comment='发起人撤回' WHERE biz_type=? AND biz_id=? AND status IN ('pending','approved')", (biz, bid))
    conn.execute("DELETE FROM dingtalk_instances WHERE biz_type=? AND biz_id=?", (biz, bid))
    conn.commit()
    # 重新生成审批链 + 钉钉发起
    amount = 0
    try:
        if 'total_amount' in row.keys(): amount = float(row['total_amount'] or 0)
        elif 'total_estimated' in row.keys(): amount = float(row['total_estimated'] or 0)
        elif 'amount' in row.keys(): amount = float(row['amount'] or 0)
    except Exception:
        amount = 0
    create_approvals(biz, bid, amount)
    start_instances(biz, bid)
    conn.close()
    log(session['user_name'], '撤回审批', f'{biz_type}#{bid} {no} 重新进入审批流')
    return jsonify({'success': True, 'message': f'单据 {no} 已撤回，重新进入审批流'})


@app.route('/api/docs/<biz_type>/<int:bid>/update', methods=['POST'])
@login_required
def api_doc_update(biz_type, bid):
    """V8.0: 单据通用编辑 — 全字段可修改, 保存实时生效
    支持: purchase_request/purchase_order/contract/receiving/requisition/inventory
    - 主表全字段更新(白名单过滤, 防注入)
    - 明细子表重建(request_items/order_items/requisition_items/items_json)
    - 金额字段自动重算(单价x数量x税率)
    - 审批中/已通过单据: 编辑后回到待审批重新走流程(撤回原审批)"""
    if not can_manage_config():
        return jsonify({'error': '仅系统管理员可编辑单据'}), 403
    d = request.json or {}
    if biz_type not in _DELETE_TABLE:
        return jsonify({'error': f'不支持的编辑类型: {biz_type}'}), 400
    table = _DELETE_TABLE[biz_type]
    no_col = _DELETE_NO_COL[biz_type]
    biz = _DELETE_BIZTYPE[biz_type]
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (bid,)).fetchone()
    if not row:
        conn.close(); return jsonify({'error': '单据不存在'}), 404
    old_status = row['status'] if 'status' in row.keys() else ''
    no = row[no_col] if no_col in row.keys() else str(bid)

    # ---- 主表字段白名单 + 更新 ----
    EDITABLE = {
        'purchase_request': ['dept', 'requester', 'budget_code', 'purpose', 'target_date', 'remark', 'urgent', 'apply_date'],
        'purchase_order': ['supplier', 'requester', 'category', 'target_date', 'remark', 'trade_mode', 'urgent'],
        'contract': ['contract_name', 'supplier', 'amount', 'sign_date', 'start_date', 'end_date', 'content', 'remark', 'urgent'],
        'receiving': ['warehouse', 'inspector', 'remark', 'urgent'],
        'requisition': ['dept', 'requester', 'purpose', 'issued_at'],
        'inventory': ['item_name', 'spec', 'cat_code', 'unit', 'quantity', 'safe_stock', 'warehouse', 'price', 'tax_rate', 'remark', 'max_stock', 'expiry_date', 'supplier'],
    }
    fields = EDITABLE.get(biz_type, [])
    updates = []
    vals = []
    for f in fields:
        if f in d and d[f] is not None:
            updates.append(f"{f}=?")
            vals.append(d[f])
    if updates:
        updates.append("updated_at=?")
        vals.append(now())
        vals.append(bid)
        conn.execute(f"UPDATE {table} SET {', '.join(updates)} WHERE id=?", vals)
        conn.commit()

    # ---- 明细子表重建 ----
    items = d.get('items')
    if items is not None and isinstance(items, list):
        items = [it for it in items if it.get('item_name') and float(it.get('quantity', 0) or 0) > 0]
        if biz_type == 'purchase_request':
            total = sum(float(it.get('quantity', 1)) * float(it.get('estimated_price', 0)) for it in items)
            conn.execute("UPDATE purchase_requests SET total_estimated=? WHERE id=?", (total, bid))
            conn.execute("DELETE FROM request_items WHERE req_id=?", (bid,))
            for it in items:
                tp = float(it.get('quantity', 1)) * float(it.get('estimated_price', 0))
                conn.execute("INSERT INTO request_items(req_id,item_name,spec,unit,quantity,estimated_price,total_price,remark,category,brand_param,arrival_date) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (bid, it.get('item_name', ''), it.get('spec', ''), it.get('unit', '个'), float(it.get('quantity', 1)),
                              float(it.get('estimated_price', 0)), tp, it.get('remark', ''),
                              it.get('category', ''), it.get('brand_param', ''), it.get('arrival_date', '')))
        elif biz_type == 'purchase_order':
            grand_amt = 0.0; grand_tax = 0.0; grand_total = 0.0
            conn.execute("DELETE FROM order_items WHERE order_id=?", (bid,))
            for it in items:
                qty = float(it.get('quantity', 1)); price = float(it.get('price', 0))
                tr = float(it.get('tax_rate', 13))
                amt = qty * price; tax = amt * tr / 100; tot = amt + tax
                grand_amt += amt; grand_tax += tax; grand_total += tot
                conn.execute("INSERT INTO order_items(order_id,item_name,spec,unit,quantity,price,amount,tax_rate,tax_amount,total_amount,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                             (bid, it.get('item_name', ''), it.get('spec', ''), it.get('unit', '个'),
                              qty, price, amt, tr, tax, tot, it.get('remark', '')))
            first = items[0]
            conn.execute("UPDATE purchase_orders SET item_name=?, spec=?, quantity=?, unit=?, price=?, amount=?, tax_amount=?, total_amount=?, tax_rate=? WHERE id=?",
                         (first.get('item_name', ''), first.get('spec', ''), sum(float(it.get('quantity', 1)) for it in items),
                          first.get('unit', '个'), first.get('price', 0), grand_amt, grand_tax, grand_total, first.get('tax_rate', 13), bid))
        elif biz_type == 'requisition':
            total_q = sum(float(it.get('quantity', 0)) for it in items)
            conn.execute("DELETE FROM requisition_items WHERE requisition_id=?", (bid,))
            for it in items:
                conn.execute("INSERT INTO requisition_items(requisition_id,item_name,spec,unit,quantity,purpose,created_at) VALUES(?,?,?,?,?,?,?)",
                             (bid, it.get('item_name', ''), it.get('spec', ''), it.get('unit', '个'),
                              float(it.get('quantity', 0)), it.get('purpose', d.get('purpose', '')), now()))
            first = items[0]
            conn.execute("UPDATE requisitions SET item_name=?, spec=?, quantity=?, unit=? WHERE id=?", (first.get('item_name', ''), first.get('spec', ''), total_q, first.get('unit', '个'), bid))
        elif biz_type == 'receiving':
            total_q = sum(float(it.get('quantity', 0)) for it in items)
            conn.execute("UPDATE receivings SET item_name=?, spec=?, quantity=?, unit=?, items_json=? WHERE id=?",
                         (items[0].get('item_name', ''), items[0].get('spec', ''), total_q, items[0].get('unit', '个'),
                          json.dumps(items, ensure_ascii=False), bid))

    # ---- 审批状态处理: 已完成的单据编辑后重新走审批 ----
    if biz_type in ('purchase_request', 'purchase_order', 'contract', 'receiving', 'requisition'):
        if old_status in ('已通过', '审批通过', '已入库', '已出库'):
            conn.execute(f"UPDATE {table} SET status='待审批', updated_at=? WHERE id=?", (now(), bid))
            conn.execute("UPDATE approval_instances SET status='rejected', comment='编辑后重新审批' WHERE biz_type=? AND biz_id=? AND status IN ('pending','approved')", (biz, bid))
            conn.execute("DELETE FROM dingtalk_instances WHERE biz_type=? AND biz_id=?", (biz, bid))
            conn.commit()
            amount = 0
            try:
                if 'total_amount' in row.keys(): amount = float(d.get('total_amount', row['total_amount']) or 0)
                elif 'total_estimated' in row.keys(): amount = float(d.get('total_estimated', row['total_estimated']) or 0)
                elif 'amount' in row.keys(): amount = float(d.get('amount', row['amount']) or 0)
            except Exception:
                amount = 0
            create_approvals(biz, bid, amount)
            start_instances(biz, bid)
        else:
            conn.commit()

    conn.close()
    log(session['user_name'], '编辑单据', f'{biz_type}#{bid} {no}')
    return jsonify({'success': True, 'message': f'单据 {no} 已更新（数据实时生效）'})



_DELETE_TABLE = {
    'purchase_request': 'purchase_requests', 'purchase_order': 'purchase_orders',
    'contract': 'contracts', 'receiving': 'receivings',
    'requisition': 'requisitions', 'payment': 'payment_requests',
    'inventory': 'inventory',
}
_DELETE_NO_COL = {
    'purchase_request': 'req_no', 'purchase_order': 'order_no', 'contract': 'contract_no',
    'receiving': 'receive_no', 'requisition': 'req_no', 'payment': 'pay_no',
    'inventory': 'item_name',
}
_DELETE_BIZTYPE = {
    'purchase_request': 'purchase_request', 'purchase_order': 'purchase_order', 'contract': 'contract',
    'receiving': 'receiving', 'requisition': 'requisition', 'payment': 'payment',
    'inventory': 'inventory',
}

@app.route('/api/docs/<biz_type>/<int:bid>/delete', methods=['POST'])
@login_required
def api_doc_delete(biz_type, bid):
    """V8.0: 单据删除 — 支持 申请/订单/合同/入库/出库/付款
    安全规则:
    - 仅系统管理员可删(需传 confirm=1)
    - 已审批通过且已影响库存(入库已入库/出库已出库)的单据: 需先作废回滚库存, 不可直接删
    - 有下游单据引用(订单引用申请/入库引用订单/合同引用订单)的: 禁止删除
    - 级联清理: 明细行/审批实例/钉钉实例/库存流水"""
    if not can_manage_config():
        return jsonify({'error': '仅系统管理员可删除单据'}), 403
    d = request.json or {}
    if not d.get('confirm'):
        return jsonify({'error': '请确认删除(confirm=1)'}), 400
    if biz_type not in _DELETE_TABLE:
        return jsonify({'error': f'不支持的删除类型: {biz_type}'}), 400
    table = _DELETE_TABLE[biz_type]
    no_col = _DELETE_NO_COL[biz_type]
    biz = _DELETE_BIZTYPE[biz_type]
    conn = db()
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (bid,)).fetchone()
    if not row:
        conn.close(); return jsonify({'error': '单据不存在'}), 404
    status = row['status'] if 'status' in row.keys() else ''
    no = row[no_col] if no_col in row.keys() else str(bid)
    # 1. 下游引用保护
    if biz_type == 'purchase_request':
        if conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE req_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该申请已被订单引用，无法删除（可作废）'}), 400
    if biz_type == 'purchase_order':
        if conn.execute("SELECT COUNT(*) FROM receivings WHERE order_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该订单已有入库记录，无法删除（可作废）'}), 400
        if conn.execute("SELECT COUNT(*) FROM contracts WHERE order_id=?", (bid,)).fetchone()[0] > 0:
            conn.close(); return jsonify({'error': '该订单已生成合同，无法删除（可作废）'}), 400
    if biz_type == 'receiving' and status == '已入库':
        conn.close(); return jsonify({'error': '该入库单已完成入库(已增加库存)，请先作废回滚库存后再删除'}), 400
    if biz_type == 'requisition' and status == '已出库':
        conn.close(); return jsonify({'error': '该出库单已完成出库(已扣减库存)，请先作废回滚库存后再删除'}), 400
    # 2. 级联删除
    rel_map = {
        'purchase_request': [('request_items', 'req_id')],
        'purchase_order': [('order_items', 'order_id')],
        'receiving': [],
        'requisition': [('requisition_items', 'requisition_id')],
        'contract': [], 'payment': [],
    }
    for rel, fk in rel_map.get(biz_type, []):
        conn.execute(f"DELETE FROM {rel} WHERE {fk}=?", (bid,))
    conn.execute("DELETE FROM approval_instances WHERE biz_type=? AND biz_id=?", (biz, bid))
    conn.execute("DELETE FROM dingtalk_instances WHERE biz_type=? AND biz_id=?", (biz, bid))
    conn.execute("DELETE FROM inventory_flows WHERE doc_type=? AND doc_id=?", (biz, bid))
    conn.execute(f"DELETE FROM {table} WHERE id=?", (bid,))
    conn.commit(); conn.close()
    log(session['user_name'], '删除单据', f'{biz_type}#{bid} {no}')
    return jsonify({'success': True, 'message': f'单据 {no} 已删除'})

# ---- 报表中心扩充数据 ----
@app.route('/api/reports2')
@login_required
def api_reports2():
    """需求44-报表中心: 采购/库存/往来报表聚合"""
    c = db()
    # 采购订单执行表
    exec_rows = c.execute("""SELECT order_no,item_name,supplier,total_amount,trade_mode,status,target_date,created_at
        FROM purchase_orders ORDER BY id DESC LIMIT 200""").fetchall()
    # 进货统计(按商品)
    by_item = c.execute("""SELECT item_name, COUNT(*) cnt, SUM(quantity) qty, SUM(total_amount) s FROM purchase_orders
        GROUP BY item_name ORDER BY s DESC LIMIT 20""").fetchall()
    # 进货统计(按供应商)
    by_sup = c.execute("""SELECT supplier, COUNT(*) cnt, SUM(total_amount) s FROM purchase_orders
        WHERE supplier!='' GROUP BY supplier ORDER BY s DESC LIMIT 20""").fetchall()
    # 采购历史价格跟踪
    price_track = c.execute("""SELECT item_name,spec,price,supplier,created_at FROM purchase_orders
        ORDER BY created_at DESC LIMIT 50""").fetchall()
    # 库存: 缺货预警
    stock_low = c.execute("SELECT * FROM inventory WHERE quantity<=safe_stock AND safe_stock>0").fetchall()
    # 出入库流水
    inout = c.execute("""SELECT '入库' type, receive_no no, item_name, qualified_qty qty, unit, received_at t FROM receivings
        UNION ALL SELECT '出库', req_no, item_name, quantity, unit, created_at FROM requisitions
        ORDER BY t DESC LIMIT 100""").fetchall()
    # 出入库汇总(近30天)
    in_sum = c.execute("SELECT COALESCE(SUM(qualified_qty),0) FROM receivings WHERE received_at >= datetime('now','localtime','-30 days')").fetchone()[0]
    out_sum = c.execute("SELECT COALESCE(SUM(quantity),0) FROM requisitions WHERE created_at >= datetime('now','localtime','-30 days')").fetchone()[0]
    # 付款统计
    pay_sum = c.execute("SELECT COALESCE(SUM(amount),0) FROM payment_requests WHERE status='已付款'").fetchone()[0]
    # 超期应付款: 已挂账但未付款超过30天
    overdue_pay = c.execute("""SELECT cn.credit_no, cn.supplier, cn.amount, cn.created_at FROM credit_notes cn
        WHERE cn.status='已通过' AND cn.created_at <= datetime('now','localtime','-30 days')
        AND NOT EXISTS (SELECT 1 FROM payment_requests pr WHERE pr.credit_id=cn.id AND pr.status IN ('已付款','已通过'))""").fetchall()
    # 供应商账本: 每家供应商往来汇总
    sup_ledger = c.execute("""SELECT supplier, COUNT(*) cnt, SUM(total_amount) s FROM purchase_orders
        WHERE supplier!='' GROUP BY supplier""").fetchall()
    c.close()
    return jsonify({
        'exec': [dict_row(r) for r in exec_rows],
        'by_item': [dict_row(r) for r in by_item],
        'by_sup': [dict_row(r) for r in by_sup],
        'price_track': [dict_row(r) for r in price_track],
        'stock_low': [dict_row(r) for r in stock_low],
        'inout': [dict_row(r) for r in inout],
        'in_sum': in_sum, 'out_sum': out_sum,
        'pay_sum': pay_sum,
        'overdue_pay': [dict_row(r) for r in overdue_pay],
        'sup_ledger': [dict_row(r) for r in sup_ledger],
    })

# ---- 系统设置: 选项/企业信息 ----
@app.route('/api/health')
def api_health():
    db_ok = True
    try:
        c = db(); c.execute("SELECT 1"); c.close()
    except Exception:
        db_ok = False
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'system': '正成能源智慧采购系统',
        'version': 'V5.1 专业加固版',
        'time': now(),
        'database': 'ok' if db_ok else 'error',
        'last_backup': last_backup_name(),
        'login_user': session.get('user_name', ''),
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        if not can_manage_config(): return jsonify({'error': '仅系统管理员可操作'}), 403
        d = request.json or {}
        for k in ('company_name', 'company_address', 'company_contact', 'company_phone', 'write_lock'):
            if k in d: cfg_set(k, d[k])
        changed = [k for k in ('company_name','company_address','company_contact','company_phone','write_lock') if k in d]
        log(session.get('user_name',''), '修改系统设置', '变更项: %s' % (','.join(changed) or '无'))
        return jsonify({'success': True})
    info = {k: cfg_get(k) for k in ('company_name', 'company_address', 'company_contact', 'company_phone')}
    info.update({
        'write_lock': cfg_get('write_lock', '1'),
        'version': 'V5.1 专业加固版',
        'last_backup': last_backup_name(),
        'db_status': 'ok',
    })
    return jsonify(info)

# ============================================================
# ── PAGES ──
# ============================================================
@app.route('/test-login')
def test_login():
    return render_template('test_login.html')

@app.route('/')
def login_page():
    # V8.0: 禁用页面缓存 — 每次访问强制最新版本, 避免旧JS导致按钮失灵
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    init_db()
    # V5.1 安全加固: 启动自动备份(每天首次启动备份一次)
    _startup_backup()
    # V4.1 预警调度(仅reloader子进程启动, 避免debug模式双线程重复推送)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        threading.Thread(target=scheduler_loop, daemon=True).start()
        print('  [预警调度] 已启动 (每60秒扫描, 飞书机器人推送)')
        threading.Thread(target=dt_poll_loop, daemon=True).start()
        print('  [钉钉审批同步] 已启动 (每15秒轮询即时同步)')
        threading.Thread(target=_daily_backup_loop, daemon=True).start()
        print('  [自动备份] 每日03:00自动备份已启动')
    port = int(os.environ.get('PORT', 5899))
    # V11.1 多人协作: 页面/模板改动即时生效, 无需重启(后端app.py改动由 app_watchdog.py 自动重启)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    print(f"""
╔══════════════════════════════════════════════════╗
║  正成能源采购系统 v9.0 (UI美化版)      ║
║  安全: PBKDF2密码加密 | 登录失败锁定 | CSRF防护  ║
║  启动: http://127.0.0.1:{port}                     ║
║  默认账号: admin / admin123                       ║
║  飞书回调: http://<公网地址>:{port}/api/feishu/callback  ║
╚══════════════════════════════════════════════════╝
""")
    app.run(host='0.0.0.0', port=port, debug=False)  # 0.0.0.0=局域网可直连(李总等同事可用 http://本机IP:5899)


@app.route('/api/_debug')
@login_required
def api_debug():
    """临时调试: 查看 query 解析"""
    from urllib.parse import parse_qsl
    qs_bytes = request.query_string
    q_arg = request.args.get('q', '')
    out = {
        'query_string_bytes': repr(qs_bytes),
        'args_q_repr': repr(q_arg),
        'args_q_has_ufffd': '\ufffd' in q_arg,
        'raw_qs_latin1': repr(qs_bytes.decode('latin-1', 'replace')),
    }
    try:
        parsed = []
        for k, v in parse_qsl(qs_bytes.decode('latin-1', 'replace'), encoding='latin-1'):
            parsed.append({'k': k, 'v_repr': repr(v), 'gbk': v.encode('latin-1', 'replace').decode('gbk', 'replace')})
        out['parsed_latin1'] = parsed
    except Exception as e:
        out['parse_err'] = str(e)
    return jsonify(out)

@app.route('/simple-test')
def simple_test():
    return render_template('simple_test.html')

@app.route('/test')
def test_page():
    return render_template('test.html')

