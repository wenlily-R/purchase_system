#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购系统代码自动重载守护 — 2026-08-17
多人协作: 谁改动了 app.py / templates/ 下的代码, 本脚本检测到变化后自动重启系统,
其他人刷新同一个网址即可看到最新效果(不用手动重启)。

用法:  .venv/bin/python app_watchdog.py   (常驻运行)
"""
import os, sys, time, signal, subprocess, hashlib

# Windows 下 stdout 重定向到文件默认 GBK, emoji(⛔/⚠️)打印即崩 → 强制 UTF-8 + 替换兜底 + 实时落盘
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace', line_buffering=True, write_through=True)
    except Exception:
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, 'app.py')
# 路径自适应: Windows=.venv/Scripts/python.exe, Mac/Linux=.venv/bin/python (一份脚本双端通用)
if os.path.exists(os.path.join(BASE, '.venv', 'Scripts', 'python.exe')):
    VENV_PY = os.path.join(BASE, '.venv', 'Scripts', 'python.exe')
else:
    VENV_PY = os.path.join(BASE, '.venv', 'bin', 'python')
WATCH_DIRS = [BASE, os.path.join(BASE, 'templates')]
WATCH_EXTS = ('.py', '.html', '.js', '.css')

proc = None
running = True
_last_try = 0  # V11.272: 自检被拒/启动失败后的重试节流时间戳

def snapshot():
    """返回 {相对路径: 内容md5} 用于检测变化"""
    s = {}
    for d in WATCH_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in ('.venv', '.git', '__pycache__', 'backup', 'data', 'static')]
            for f in files:
                if not f.endswith(WATCH_EXTS):
                    continue
                if f.startswith('app_watchdog'):
                    continue
                p = os.path.join(root, f)
                try:
                    s[p] = hashlib.md5(open(p, 'rb').read()).hexdigest()
                except Exception:
                    pass
    return s

def start_app():
    """启动(或重启)系统进程 — V11.17: 重启前先跑自检, 有错误则拒绝重启(保持旧版继续服务)"""
    global proc
    # 自检: 代码有低级错误(语法/裸百分号/session后台线程等)时不重启
    try:
        # V11.272: 子进程强制 UTF-8(否则 Windows 管道默认 GBK, 自检打印 ✅ 即崩→误判"自检未通过");
        #          同时回显 stderr, 便于定位真实失败原因
        _env = dict(os.environ)
        _env['PYTHONUTF8'] = '1'
        _env['PYTHONIOENCODING'] = 'utf-8'
        r = subprocess.run([VENV_PY, os.path.join(BASE, 'check_code.py')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=60, env=_env)
        if r.returncode != 0:
            print('[%s] ⛔ 自检未通过, 拒绝重启(保持当前版本运行)!' % time.strftime('%H:%M:%S'))
            print((r.stdout or '')[-800:])
            print((r.stderr or '')[-800:])
            return
    except Exception as e:
        print('[%s] ⚠️ 自检执行异常(放行): %s' % (time.strftime('%H:%M:%S'), e))
    if proc and proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    print('[%s] 启动/重启系统...' % time.strftime('%H:%M:%S'))
    # V11.272: stdin=DEVNULL + 新进程组, 否则 app.py 会因继承无效 stdin/控制台信号以 0xC000013A(STATUS_CONTROL_C_EXIT) 反复崩
    _kw = {}
    if os.name == 'nt':
        _kw['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([VENV_PY, APP], cwd=BASE,
                            stdout=open(os.path.join(BASE, 'data', 'app_watchdog.log'), 'a'),
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **_kw)
    return proc

def stop():
    global running, proc
    running = False
    if proc and proc.poll() is None:
        proc.kill()

signal.signal(signal.SIGTERM, lambda *a: stop())
signal.signal(signal.SIGINT, lambda *a: stop())

print('代码自动重载守护启动. 监控: app.py + templates/')
last = snapshot()
start_app()
time.sleep(1)

while running:
    time.sleep(2)
    # 系统进程未运行 → 拉起。
    # V11.272修复: 原实现只在 proc 非空时检查, 一旦自检未通过(proc 保持 None)就永远不会再启动 → 本机服务长期挂死。
    if proc is None or proc.poll() is not None:
        if proc is not None:
            print('[%s] 系统进程退出 code=%s, 自动拉起' % (time.strftime('%H:%M:%S'), proc.returncode))
        if time.time() - _last_try >= 60:   # 60 秒一次重试(自检未通过时不过度刷屏)
            _last_try = time.time()
            start_app()
            last = snapshot()
        time.sleep(1)
        continue
    # 代码变化 → 重启
    cur = snapshot()
    if cur != last:
        changed = [p for p in cur if cur.get(p) != last.get(p)] + [p for p in last if last.get(p) != cur.get(p)]
        print('[%s] 检测到代码变化: %s → 自动重启' % (time.strftime('%H:%M:%S'), '; '.join(os.path.relpath(p, BASE) for p in changed[:5])))
        start_app()
        last = snapshot()
        time.sleep(1)
print('守护已停止')
