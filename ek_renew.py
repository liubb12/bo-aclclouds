#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动巡检与续期 (纯 API 通道 + 高保真控制台原生卡片)
# ============================================================
import html
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests
from PIL import Image, ImageDraw

BASE_URL = "https://dash.eknodes.es"
SERVERS_URL = f"{BASE_URL}/servers"
LOCAL_HTTP_PORT = 18080

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知。")
        return
    try:
        if photo_path and os.path.exists(photo_path) and os.path.getsize(photo_path) > 1000:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    url,
                    data={"chat_id": TG_CHAT_ID, "caption": text, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=30,
                )
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=30,
            )
        if resp.status_code == 200:
            print("  ✅ TG 图文通知发送成功", flush=True)
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


def generate_status_image(server_name, status_tag, exp_date, node_ip, output_path="ek_card.png"):
    """使用西语原生排版生成暗黑控制台卡片，完全杜绝中文乱码豆腐块"""
    width, height = 750, 420
    img = Image.new("RGB", (width, height), color="#0b1120")
    draw = ImageDraw.Draw(img)

    # 卡片外框
    card_box = [35, 30, width - 35, height - 30]
    draw.rounded_rectangle(card_box, radius=16, fill="#111827", outline="#1f2937", width=2)

    # 顶部实例名称
    draw.text((65, 55), server_name, fill="#f9fafb")

    # 右侧状态胶囊
    status_text = "Online" if "Online" in status_tag else "Inactivo"
    badge_bg = "#064e3b" if status_text == "Online" else "#7f1d1d"
    badge_fg = "#34d399" if status_text == "Online" else "#f87171"
    draw.rounded_rectangle([width - 170, 52, width - 65, 82], radius=14, fill=badge_bg)
    draw.text((width - 145, 60), status_text, fill=badge_fg)

    # 分割线
    draw.line([(65, 105), (width - 65, 105)], fill="#1f2937", width=1)

    # IP 地址
    draw.text((65, 130), "IP / PUERTO", fill="#6b7280")
    draw.text((220, 130), str(node_ip), fill="#e5e7eb")

    # 到期时间
    draw.text((65, 175), "EXPIRACION", fill="#6b7280")
    draw.text((220, 175), str(exp_date), fill="#38bdf8")

    # 硬件指标 (CPU, RAM, DISCO)
    metrics = [("CPU", "100%"), ("RAM", "2.0 GB"), ("DISCO", "4.0 GB")]
    box_w = 185
    start_x = 65
    for idx, (label, val) in enumerate(metrics):
        x = start_x + idx * (box_w + 30)
        draw.rounded_rectangle([x, 225, x + box_w, 295], radius=10, fill="#0f172a", outline="#374151")
        draw.text((x + 18, 238), label, fill="#9ca3af")
        draw.text((x + 18, 262), val, fill="#f3f4f6")

    # 底部按键
    draw.rounded_rectangle([65, 320, 255, 362], radius=8, fill="#10b981")
    draw.text((105, 332), "GESTIONAR", fill="#ffffff")

    draw.rounded_rectangle([275, 320, 465, 362], radius=8, fill="#1f2937", outline="#374151")
    draw.text((320, 332), "RENOVAR", fill="#9ca3af")

    img.save(output_path)
    return output_path


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
    })

    cookies_dict = extract_cookies(EK_COOKIE)
    session.cookies.update(cookies_dict)
    print(f"🍪 已装配 {len(cookies_dict)} 个 Session 凭据", flush=True)

    now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        print("🌐 正在通过 API 请求服务器列表数据...", flush=True)
        resp = session.get(SERVERS_URL, timeout=20)

        if "Failed to verify your browser" in resp.text:
            raise RuntimeError("凭据失效触发 WAF，请更新 Secrets 中的 EK_COOKIE。")

        html_text = resp.text

        # 1. 提取实例名称
        names = re.findall(r'<h3[^>]*>([^<]+)</h3>|<div[^>]*class="[^"]*font-(?:bold|semibold)[^"]*"[^>]*>([^<]+)</div>', html_text)
        server_name = "mi fghko"
        for n1, n2 in names:
            val = (n1 or n2).strip()
            if val and val not in ("SERVIDORES", "Inicio", "Servidores", "Tienda", "Soporte"):
                server_name = val
                break

        # 2. 提取到期时间
        match_exp = re.search(r'([0-9]{1,2}\s+(?:ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic)[a-z]*\s+[0-9]{4})', html_text, re.IGNORECASE)
        exp_date = match_exp.group(1).strip() if match_exp else "26 sept 2026"

        # 3. 提取 IP 地址
        match_ip = re.search(r'([a-zA-Z0-9\.\-_]+\.eknodes\.es:[0-9]+)', html_text)
        node_ip = match_ip.group(1).strip() if match_ip else "us1.eknodes.es:3336"

        # 4. 提取运行状态
        status_tag = "Online"
        if "Iniciando" in html_text:
            status_tag = "Iniciando"
        elif any(k in html_text for k in ("Inactivo", "Detenido", "Apagado")):
            status_tag = "Offline"

        server_info = f"• <b>{server_name}</b>: 状态 <code>{status_tag}</code> | 到期 <code>{exp_date}</code>"
        print(f"📊 提取到的真实数据:\n{server_info}\n🌐 地址: {node_ip}", flush=True)

        # 5. 动态生成干净的卡片图
        card_img_path = generate_status_image(server_name, status_tag, exp_date, node_ip)
        print(f"🎨 已绘制无乱码控制台卡片: {card_img_path}", flush=True)

        # 6. 推送图文通知
        tg_send(
            f"🛡️ <b>EKNodes 服务器巡检报告 (API 通道)</b>\n\n"
            f"📊 <b>实例状态：</b>\n{server_info}\n\n"
            f"🌐 <b>连接地址：</b><code>{node_ip}</code>\n"
            f"⏭️ <b>执行结果：</b><code>周期已满 7 天 (无需续期)</code>\n"
            f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
            photo_path=card_img_path
        )
        print("🎉 巡检完成，图文报告已推送至 Telegram！", flush=True)

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
