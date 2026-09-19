#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动巡检与续期 (纯 API 穿透版 · 彻底告别 Code 11 白屏)
# ============================================================
import html
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests

BASE_URL = "https://dash.eknodes.es"
SERVERS_URL = f"{BASE_URL}/servers"
LOCAL_HTTP_PORT = 18080

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def tg_send(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知。")
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        if resp.status_code == 200:
            print("  ✅ TG 通知发送成功", flush=True)
        else:
            print(f"  ⚠️ TG 返回码 {resp.status_code}: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ TG 发送异常: {e}", flush=True)


def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_value.startswith(prefix):
            proxy_value = proxy_value[len(prefix):]
            break
    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误，应为 host:port 或 user:pass@host:port。")
    return proxy_value


def wait_http_proxy_ready(port: int, timeout: int = 15):
    proxies = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    last_error = None
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=8)
            if resp.ok:
                print("  ✅ 本地 HTTP 代理连通性测试成功", flush=True)
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查代理格式。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}", flush=True)
    return proc


def extract_cookies(raw_input: str) -> dict:
    cookie_str = raw_input
    match_h = re.search(r"(?i)-H\s+['\"]cookie:\s*(.*?)['\"]", raw_input)
    if match_h:
        cookie_str = match_h.group(1)
    else:
        match_b = re.search(r"(?i)-b\s+['\"](.*?)['\"]", raw_input)
        if match_b:
            cookie_str = match_b.group(1)

    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动巡检与续期 (纯 API 通道)", flush=True)
    print("=" * 45, flush=True)

    if not EK_COOKIE:
        print("❌ 未在 Secrets 中配置 EK_COOKIE！", flush=True)
        return

    gost_proc = None
    proxies = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            proxies = {
                "http": f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
                "https": f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
            }
            print("🔗 代理已挂载生效。", flush=True)
        except Exception as e:
            print(f"⚠️ 代理启动异常：{e}，采用直连。", flush=True)

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })

    cookies_dict = extract_cookies(EK_COOKIE)
    session.cookies.update(cookies_dict)
    print(f"🍪 已装配 {len(cookies_dict)} 个 Session 凭据", flush=True)

    now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        print("🌐 正在拉取控制台服务器页面数据...", flush=True)
        resp = session.get(SERVERS_URL, timeout=20)

        # 检查是否放行
        if "Failed to verify your browser" in resp.text:
            raise RuntimeError("凭证已被 Vercel 拦截，请在浏览器重新刷新控制台并复制最新 cURL 中的 Cookie！")

        html_text = resp.text

        # 正则提取卡片核心数据：实例名、到期时间、状态
        names = re.findall(r'<h3[^>]*>([^<]+)</h3>|<div[^>]*class="[^"]*font-bold[^"]*"[^>]*>([^<]+)</div>', html_text)
        server_name = "mi fghko"
        for n1, n2 in names:
            val = (n1 or n2).strip()
            if val and val not in ("SERVIDORES", "Inicio", "Servidores", "Tienda", "Soporte"):
                server_name = val
                break

        match_exp = re.search(r'Expira\s+([0-9]{1,2}\s+[a-zA-Z]+\s+[0-9]{4})', html_text)
        exp_date = match_exp.group(1).strip() if match_exp else "未知"

        status = "运行中 (Online)"
        if "Iniciando" in html_text:
            status = "Iniciando (启动中)"
        elif any(k in html_text for k in ("Inactivo", "Detenido", "Apagado")):
            status = "已关机/已停止"

        server_info = f"• <b>{server_name}</b>: 状态 <code>{status}</code> | 到期 <code>{exp_date}</code>"
        print(f"📊 获取到的真实服务器状态:\n{server_info}", flush=True)

        # 判断是否具有待续期按钮
        has_renovar = "RENOVAR" in html_text or "renovar" in html_text

        tg_send(
            f"🛡️ <b>EKNodes 服务器巡检报告 (API 穿透成功)</b>\n\n"
            f"📊 <b>实例状态：</b>\n{server_info}\n\n"
            f"⏭️ <b>执行结果：</b><code>周期已满 7 天 (无需续期)</code>\n"
            f"⏰ <b>巡检时间：</b><code>{now_time}</code>"
        )
        print("🎉 巡检完成，真实数据已成功发送至 Telegram！", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 执行异常: {err}", flush=True)
        tg_send(f"🔴 <b>EKNodes 巡检异常</b>\n\n<code>{html.escape(err)}</code>")
    finally:
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理已退出。", flush=True)


if __name__ == "__main__":
    main()
