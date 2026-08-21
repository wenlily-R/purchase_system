#!/usr/bin/env python3
# 隧道守护 v2: select 超时读输出, 防止 ssh 不输出时卡死; 抓到地址写文件并保持
import subprocess, re, time, os, select

PUB = '/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system/data/public_url.txt'
env = {'CONDA_NO_PLUGINS': '1', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/Users/a0'}

def try_tunnel(host_args, url_pat, timeout=35):
    cmd = ['/usr/bin/ssh', '-T',
           '-o', 'StrictHostKeyChecking=no',
           '-o', 'ServerAliveInterval=5',
           '-o', 'ServerAliveCountMax=120',
           '-o', 'ExitOnForwardFailure=yes',
           '-o', 'TCPKeepAlive=yes',
           '-o', 'ConnectionAttempts=3',
           '-o', 'ConnectTimeout=20'] + host_args
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    deadline = time.time() + timeout
    url = None
    buf = ''
    while time.time() < deadline:
        r, _, _ = select.select([p.stdout], [], [], 1)
        if r:
            chunk = os.read(p.stdout.fileno(), 1024).decode('utf-8', 'ignore')
            if not chunk:
                if p.poll() is not None:
                    break
                continue
            buf += chunk
            m = re.search(url_pat, buf)
            if m:
                url = m.group(0)
                break
        elif p.poll() is not None:
            break
    if url:
        return url, p
    p.kill()
    return None, None

print('隧道守护v2启动', flush=True)
while True:
    url, proc = try_tunnel(['-R', '80:127.0.0.1:5899', 'nokey@localhost.run'], r'https://[a-z0-9]+\.lhr\.life')
    label = 'localhost.run'
    if not url:
        url, proc = try_tunnel(['-R', '80:localhost:5899', 'serveo.net'], r'https://[a-z0-9-]+-[0-9-]+\.serveousercontent\.com')
        label = 'serveo'
    if url and proc:
        with open(PUB, 'w') as f:
            f.write(url + '\n')
        print(f'[{time.strftime("%H:%M:%S")}] OK {label}: {url}', flush=True)
        proc.wait()
        print(f'[{time.strftime("%H:%M:%S")}] {label} 断开, 重连...', flush=True)
    else:
        print(f'[{time.strftime("%H:%M:%S")}] 都失败, 30s后重试', flush=True)
        time.sleep(30)
