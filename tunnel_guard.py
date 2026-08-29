#!/usr/bin/env python3
"""隧道守护 v3 — 真实公网验证 + 自动重连 + 地址文件更新
改进: 抓到URL后必须 curl 200 才算成功; 连接断了(进程退出)立即重连;
      验证失败的隧道杀掉重连, 不让坏连接挂着误导。
"""
import subprocess, re, time, os, select, sys, urllib.request

BASE = '/Users/a0/Desktop/正成能源/01_系统程序/采购系统程序/purchase_system'
PUB = os.path.join(BASE, 'data/public_url.txt')
env = {'CONDA_NO_PLUGINS': '1', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/Users/a0'}

def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)

def check_url(url, timeout=12):
    """真实公网验证: 返回 True 才认为可用"""
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False

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
        # 真实验证 200 才返回
        if check_url(url):
            return url, p
        log(f'抓到 {url} 但公网不通, 杀掉重试')
        p.kill()
        return None, None
    p.kill()
    return None, None

def main():
    log('隧道守护v3启动(真实公网验证)')
    while True:
        url, proc = try_tunnel(['-R', '80:127.0.0.1:5899', 'nokey@localhost.run'], r'https://[a-z0-9]+\.lhr\.life')
        label = 'localhost.run'
        if not url:
            url, proc = try_tunnel(['-R', '80:localhost:5899', 'serveo.net'], r'https://[a-z0-9-]+-[0-9-]+\.serveousercontent\.com')
            label = 'serveo'
        if url and proc:
            with open(PUB, 'w') as f:
                f.write(url + '\n')
            log(f'✅ {label}: {url} (公网已验证)')
            # V11.146: 地址变更 → 通知系统推送飞书/钉钉新地址(用户不用等人工告知)
            try:
                req = urllib.request.Request('http://127.0.0.1:5899/api/notify-address-change',
                                             method='POST', data=b'{}',
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=10) as r:
                    log(f'地址变更通知: HTTP {r.status}')
            except Exception as e:
                log(f'地址变更通知失败: {e}')
            # 持续健康检查: 每30秒验证一次, 连续2次失败 → 杀进程强制重连
            fail_cnt = 0
            while proc.poll() is None:
                time.sleep(30)
                if check_url(url):
                    fail_cnt = 0
                else:
                    fail_cnt += 1
                    log(f'健康检查失败 {fail_cnt}/2 ({url})')
                    if fail_cnt >= 2:
                        log('连续2次失败, 强制杀进程重连')
                        proc.kill()
                        break
            log(f'{label} 断开, 立即重连...')
            time.sleep(2)
        else:
            log('两个隧道都失败, 30秒后重试')
            time.sleep(30)

if __name__ == '__main__':
    main()
