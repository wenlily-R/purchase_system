#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购系统代码自动重载守护 — 2026-08-17
多人协作: 谁改动了 app.py / templates/ 下的代码, 本脚本检测到变化后自动重启系统,
其他人刷新同一个网址即可看到最新效果(不用手动重启)。

用法:  .venv/bin/python app_watchdog.py   (常驻运行)
"""
import os, sys, time, signal, subprocess, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(BASE, 'app.py')
VENV_PY = os.path.join(BASE, '.venv', 'bin', 'python')
WATCH_DIRS = [BASE, os.path.join(BASE, 'templates')]
WATCH_EXTS = ('.py', '.html', '.js', '.css')

proc = None
running = True

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
    """启动(或重启)系统进程"""
    global proc
    if proc and proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    print('[%s] 启动/重启系统...' % time.strftime('%H:%M:%S'))
    proc = subprocess.Popen([VENV_PY, APP], cwd=BASE,
                            stdout=open(os.path.join(BASE, 'data', 'app_watchdog.log'), 'a'),
                            stderr=subprocess.STDOUT)
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
    # 系统进程意外退出 → 拉起
    if proc and proc.poll() is not None:
        print('[%s] 系统进程退出 code=%s, 自动拉起' % (time.strftime('%H:%M:%S'), proc.returncode))
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
