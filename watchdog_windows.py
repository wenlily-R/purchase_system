#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购系统 Windows 常驻守护 v2 — 先复用现有隧道, 死了才重建(防限流)
关键经验(2026-08-29):
  - Cloudflare 免费 quick tunnel 会限流: 频繁新建(几分钟内多条)会让所有隧道返回 HTTP 530 (Retry-After:120)
  - 规律: 冷启动后第一条隧道可用; 短时间内第二条+必 530。所以【复用现有, 死了才重建, 重建间隔≥5分钟】
  - 本机 Clash Verge 的 mihomo 核心后台运行会干扰 QUIC 隧道(即使系统代理显示关闭), 隧道前先杀 Clash
用法: .venv\\Scripts\\pythonw.exe watchdog_windows.py  (无窗口常驻)
"""
import os, time, subprocess, hashlib, re, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, 'app.py')
VENV_PY = os.path.join(BASE, '.venv', 'Scripts', 'python.exe')
CF = os.path.join(BASE, 'data', 'cloudflared.exe')
PUB = os.path.join(BASE, 'data', 'public_url.txt')
LOGDIR = os.path.join(BASE, 'data')
WATCH_DIRS = [BASE, os.path.join(BASE, 'templates')]
WATCH_EXTS = ('.py', '.html', '.js', '.css')
PORT = 5899
SKIP = ('.venv', '.git', '__pycache__', 'backup', 'data', 'static', 'uploads', 'docs', 'scripts测试备份', 'references')

def log(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    try:
        with open(os.path.join(LOGDIR, 'watchdog_windows.log'), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def check_url(url, timeout=12):
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.status == 200), r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception:
        return False, 0

def snapshot():
    s = {}
    for d in WATCH_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in SKIP]
            for f in files:
                if not f.endswith(WATCH_EXTS):
                    continue
                p = os.path.join(root, f)
                try:
                    s[p] = hashlib.md5(open(p, 'rb').read()).hexdigest()
                except Exception:
                    pass
    return s

# ---------- app ----------
app_proc = None
def start_app():
    global app_proc
    try:
        r = subprocess.run([VENV_PY, os.path.join(BASE, 'check_code.py')],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            log('⛔ 自检未通过, 拒绝重启')
            return False
    except Exception as e:
        log('⚠️ 自检异常(放行): %s' % e)
    if app_proc and app_proc.poll() is None:
        try:
            app_proc.kill(); app_proc.wait(timeout=5)
        except Exception:
            pass
    log('启动/重启 app.py ...')
    app_proc = subprocess.Popen([VENV_PY, APP], cwd=BASE,
        stdout=open(os.path.join(LOGDIR, 'app_watchdog.log'), 'a', encoding='utf-8'),
        stderr=subprocess.STDOUT)
    return True

# ---------- 隧道 ----------
tun_proc = None
def read_pub():
    try:
        return open(PUB, encoding='utf-8').read().strip()
    except Exception:
        return ''

def adopt_existing():
    """优先复用 public_url.txt 里还活着的地址, 不新建隧道(防限流)"""
    u = read_pub()
    if u.startswith('https://') and 'trycloudflare.com' in u:
        ok, code = check_url(u)
        if ok:
            log('✅ 复用现有公网地址: %s' % u)
            return u
        log('现有地址已失效(HTTP %s), 需重建' % code)
    return None

def start_tunnel():
    """新建一条隧道, 验证200后写 public_url.txt。每次用独立日志避免抓旧URL"""
    global tun_proc
    if tun_proc and tun_proc.poll() is None:
        try:
            tun_proc.kill()
        except Exception:
            pass
    log('新建 cloudflared 隧道...')
    ts = time.strftime('%H%M%S')
    tf = open(os.path.join(LOGDIR, 'tunnel_%s.log' % ts), 'w', encoding='utf-8', errors='ignore')
    tun_proc = subprocess.Popen(
        [CF, 'tunnel', '--protocol', 'quic', '--url', 'http://127.0.0.1:%d' % PORT, '--no-autoupdate'],
        cwd=os.path.join(BASE, 'data'), stdout=tf, stderr=subprocess.STDOUT)
    url = None
    for _ in range(45):
        if tun_proc.poll() is not None:
            break
        txt = open(os.path.join(LOGDIR, 'tunnel_%s.log' % ts), encoding='utf-8', errors='ignore').read()
        m = re.findall(r'https://[a-z0-9-]+\.trycloudflare\.com', txt)
        if m:
            url = m[-1]
            break
        time.sleep(1)
    if not url:
        log('⚠️ 45秒未抓到URL')
        try: tun_proc.kill()
        except Exception: pass
        return None
    last_code = 0
    for _ in range(9):
        ok, code = check_url(url)
        last_code = code
        if ok:
            try:
                with open(PUB, 'w') as f: f.write(url + '\n')
            except Exception: pass
            log('✅ 新公网地址(已验证): %s' % url)
            return url
        time.sleep(10)
    log('⚠️ 隧道 %s 90秒不可达(HTTP %s)' % (url, last_code))
    try: tun_proc.kill()
    except Exception: pass
    return None

def main():
    log('====== 守护 v2 启动 ======')
    start_app()
    last = snapshot()
    time.sleep(2)
    # 先复用现有隧道, 没有才新建
    url = adopt_existing()
    if not url:
        url = start_tunnel()
    backoff = 300          # 隧道重建最小间隔 5 分钟
    fail_cnt = 0
    while True:
        if app_proc and app_proc.poll() is not None:
            log('app.py 退出 code=%s, 拉起' % app_proc.returncode)
            start_app(); last = snapshot(); time.sleep(1); continue
        cur = snapshot()
        if cur != last:
            changed = [p for p in cur if cur.get(p) != last.get(p)] + [p for p in last if last.get(p) != cur.get(p)]
            log('代码变化: %s → 重启' % '; '.join(os.path.relpath(p, BASE) for p in changed[:4]))
            start_app(); last = snapshot(); time.sleep(1); continue
        # 隧道进程死了 → 5分钟后重建
        if tun_proc and tun_proc.poll() is not None:
            log('隧道进程退出, %d 秒后重建...' % backoff)
            time.sleep(backoff)
            url = start_tunnel()
            backoff = max(backoff, 300)
            fail_cnt = 0
            continue
        # 隧道健康检查
        if url:
            ok, code = check_url(url)
            if ok:
                fail_cnt = 0
            else:
                fail_cnt += 1
                log('健康检查失败 %d/3 (HTTP %s)' % (fail_cnt, code))
                if fail_cnt >= 3:
                    log('连续失败, 强制重建(等 %d 秒)' % backoff)
                    try: tun_proc.kill()
                    except Exception: pass
                    time.sleep(backoff)
                    url = start_tunnel()
                    backoff = max(backoff, 300)
                    fail_cnt = 0
        time.sleep(30)

if __name__ == '__main__':
    main()
