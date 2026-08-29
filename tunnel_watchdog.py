#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购系统公网隧道保活(watchdog) — 2026-08-14
localhost.run 免费隧道会远端断开, 本脚本每30秒检测当前地址, 失效则自动重启隧道并更新 public_url.txt
用法: nohup python3 tunnel_watchdog.py &  (或 hermes terminal background)
"""
import subprocess, time, re, os, sys, signal

BASE = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(BASE, 'data', 'public_url.txt')
LOCAL = 'http://127.0.0.1:5899'
PROXY = '127.0.0.1:7897'

SSH_CMD = [
    '/usr/bin/ssh', '-T',
    '-o', 'StrictHostKeyChecking=no',
    '-o', 'ServerAliveInterval=5',
    '-o', 'ServerAliveCountMax=120',
    '-o', 'ExitOnForwardFailure=yes',
    '-o', 'TCPKeepAlive=yes',
    '-o', 'ConnectionAttempts=3',
    '-o', 'ConnectTimeout=15',
    '-R', '80:127.0.0.1:5899',
    'nokey@localhost.run',
]

proc = None
running = True

def curl_ok(url, timeout=12):
    try:
        r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                            '--max-time', str(timeout), url], capture_output=True, text=True, timeout=timeout+5)
        return r.stdout.strip() == '200'
    except Exception:
        return False

def read_cur():
    try:
        return open(PUB).read().strip()
    except Exception:
        return ''

def write_url(u):
    with open(PUB, 'w') as f:
        f.write(u + '\n')

def start_tunnel():
    global proc
    print('[%s] 启动隧道...' % time.strftime('%H:%M:%S'))
    # V11.27b: 隔离 conda 环境(ssh 被 conda 插件劫持 → code 255)
    env = dict(os.environ)
    env['CONDA_NO_PLUGINS'] = '1'
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin:' + env.get('PATH', '')
    proc = subprocess.Popen(SSH_CMD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    # 从输出抓 https://*.lhr.life (约10-25秒)
    deadline = time.time() + 60
    url = ''
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                print('  隧道进程退出 code=%s, 3秒后重试' % proc.returncode)
                time.sleep(3)
                return False
            time.sleep(0.5)
            continue
        m = re.search(r'https://[a-z0-9]+\.lhr\.life', line)
        if m:
            url = m.group(0)
            break
    if url:
        old = read_cur()
        write_url(url)
        print('  新地址: %s' % url)
        # V11.28b: 地址变化 → 飞书通知用户(免费隧道地址会变, 主动告知防失效)
        if url != old:
            notify_feishu(url)
        return True
    else:
        print('  60秒内未抓到地址')
        if proc.poll() is None:
            proc.kill()
        return False

def notify_feishu(url):
    """向用户飞书推送新地址(独立实现, 不依赖 app.py)"""
    try:
        import urllib.request, json as _json
        # 读取飞书配置
        import sqlite3
        _c = sqlite3.connect(os.path.join(BASE, 'data', 'purchase.db'), timeout=10)
        _cfg = {}
        for k, v in _c.execute("SELECT key, value FROM sys_config WHERE key IN ('feishu_app_id','feishu_app_secret')").fetchall():
            _cfg[k] = v
        _c.close()
        app_id = _cfg.get('feishu_app_id', '')
        app_secret = _cfg.get('feishu_app_secret', '')
        if not app_id or not app_secret:
            print('  飞书未配置, 跳过通知')
            return
        # 拿 tenant_access_token
        req = urllib.request.Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                                     data=_json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode(),
                                     headers={'Content-Type': 'application/json'})
        tok = _json.loads(urllib.request.urlopen(req, timeout=15).read()).get('tenant_access_token', '')
        if not tok:
            return
        # 发消息给用户 open_id
        USER_OPEN_ID = 'ou_8dff5598fa8288769546f51c113b8288'
        text = '🔗 采购系统地址已更新（免费隧道自动重连）\n\n最新地址：%s\n\n打开即用；旧地址已失效请忽略。' % url
        req2 = urllib.request.Request(
            'https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
            data=_json.dumps({'receive_id': USER_OPEN_ID, 'msg_type': 'text',
                              'content': _json.dumps({'text': text}, ensure_ascii=False)}).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok})
        urllib.request.urlopen(req2, timeout=15).read()
        print('  已通知飞书: %s' % url)
    except Exception as e:
        print('  飞书通知失败: %s' % e)

def stop():
    global running, proc
    running = False
    if proc and proc.poll() is None:
        proc.kill()

signal.signal(signal.SIGTERM, lambda *a: stop())
signal.signal(signal.SIGINT, lambda *a: stop())

print('隧道保活启动. 当前地址: %s' % (read_cur() or '(无)'))
while running:
    cur = read_cur()
    local_ok = curl_ok(LOCAL)
    if not local_ok:
        print('[%s] 本机 5899 未响应(系统可能没起), 30秒后再查' % time.strftime('%H:%M:%S'))
        time.sleep(30)
        continue
    if cur and curl_ok(cur):
        time.sleep(30)
        continue
    # 地址失效 → 重启隧道
    print('[%s] 当前地址失效(%s), 重启隧道' % (time.strftime('%H:%M:%S'), cur or '空'))
    if proc and proc.poll() is None:
        proc.kill()
        time.sleep(2)
    ok = start_tunnel()
    if not ok:
        print('  启动失败, 30秒后重试')
    time.sleep(30)
print('保活已停止')
