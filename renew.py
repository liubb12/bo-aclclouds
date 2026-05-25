import os
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

print("=== renew.py started ===")

# ── Telegram 通知 ──────────────────────────────────────────────
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID', '')

def tg_send(text: str, photo_path: str = None):
    print("tg_send called")
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️  未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知。")
        return
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
            with open(photo_path, 'rb') as f:
                resp = requests.post(url, data={
                    'chat_id': TG_CHAT_ID,
                    'caption': text,
                    'parse_mode': 'HTML'
                }, files={'photo': f}, timeout=30)
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(url, data={
                'chat_id': TG_CHAT_ID,
                'text': text,
                'parse_mode': 'HTML'
            }, timeout=30)
        print(f"TG status: {resp.status_code}")
        if resp.status_code == 200:
            print("✅ TG 通知发送成功")
        else:
            print(f"⚠️  TG 通知发送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️  TG 通知异常: {e}")

LOCAL_HTTP_PORT = 18080

def normalize_socks5_proxy(socks5_proxy: str) -> str:
    print(f"raw SOCKS5_PROXY: {repr(socks5_proxy)}")
    socks5_proxy = (socks5_proxy or "").strip()
    if socks5_proxy.startswith("socks5://"):
        socks5_proxy = socks5_proxy[len("socks5://"):]
    elif socks5_proxy.startswith("socks5h://"):
        socks5_proxy = socks5_proxy[len("socks5h://"):]
    elif "://" in socks5_proxy:
        raise ValueError("SOCKS5_PROXY 只能是 socks5 代理地址，请不要传入 http/https 代理。")
    if not socks5_proxy or ":" not in socks5_proxy:
        raise ValueError("SOCKS5_PROXY 格式错误，应为 host:port 或 user:pass@host:port")
    print(f"normalized SOCKS5_PROXY: {socks5_proxy}")
    return socks5_proxy

def wait_http_proxy_ready(port: int, timeout: int = 15):
    print("waiting local http proxy ready...")
    proxies = {
        "http": f"http://127.0.0.1:{port}",
        "https": f"http://127.0.0.1:{port}",
    }
    last_error = None
    start = time.time()

    while time.time() - start < timeout:
        try:
            r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=8)
            print(f"httpbin status: {r.status_code}")
            if r.ok:
                print(f"✅ 本地 HTTP 代理连通性测试成功：{r.text}")
                return
        except Exception as e:
            last_error = e
            print(f"proxy test retry: {e}")
        time.sleep(1)

    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")

def start_gost(socks5_proxy: str) -> subprocess.Popen:
    normalized_proxy = normalize_socks5_proxy(socks5_proxy)
    cmd = [
        "gost",
        "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
        "-F", f"socks5://{normalized_proxy}"
    ]
    print(f"启动 gost：{' '.join(cmd)}")
    proc = subprocess.Popen(cmd)
    time.sleep(2)

    poll_result = proc.poll()
    print(f"gost poll result: {poll_result}")
    if poll_result is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 是否已安装。")

    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"✅ gost 已启动，本地 HTTP 代理端口：{LOCAL_HTTP_PORT}")
    return proc

def run(playwright):
    print("=== entered run(playwright) ===")

    socks5_proxy = os.environ.get('SOCKS5_PROXY', '').strip()
    gost_proc = None
    proxy_config = None

    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            proxy_config
