import os
import re
import html
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
LOCAL_HTTP_PORT = 18080

SERVER_CONSOLE_URL = "https://aclclouds.com/server/75e19d55"


# ── Telegram 通知 ──────────────────────────────────────────────
def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知。")
        return
    try:
        if photo_path and os.path.exists(photo_path):
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
            print("✅ TG 通知发送成功")
        else:
            print(f"⚠️ TG 通知发送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ TG 通知异常: {e}")


# ── gost 代理 ──────────────────────────────────────────────────
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
                print("✅ 本地 HTTP 代理连通性测试成功")
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("启动 gost 代理...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"✅ gost 已启动，本地 HTTP 代理端口：{LOCAL_HTTP_PORT}")
    return proc


def get_expire_info(page) -> str:
    """提取页面到期信息"""
    expire_info = "未知"
    try:
        body_text = page.inner_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(r'(?i)\b(\d+\s*[jd]\s*(?:\d+\s*[hm])?)\b', body_text)
        if time_match:
            expire_info = f"剩余 {time_match.group(1).strip()}"
        else:
            prefix_match = re.search(r'(?i)(?:EXPIRE\s*DANS|Expire\s*in|Échéance|Echeance)[\s:]*([^\n\r]+)', body_text)
            if prefix_match:
                expire_info = prefix_match.group(0).strip()
    except Exception as e:
        print(f"⚠️ 提取天数异常: {e}")
    return expire_info


def safe_start_server(page):
    """安全尝试点击 Start 按钮，若 disabled 则跳过"""
    try:
        start_btn = page.locator("button.power-btn[data-variant='start'], button:has-text('Démarrer'), button:has-text('Start')").first
        if start_btn.count() > 0 and start_btn.is_visible() and start_btn.is_enabled():
            start_btn.click(timeout=3000)
            print("  ⚡ 已点击 Start 按钮启动服务器...")
            page.wait_for_timeout(4000)
        else:
            print("  ℹ️ 服务器无需启动或 Start 按钮不可用")
    except Exception as e:
        print(f"  ⚠️ 触发启动跳过: {e}")


# ── 主逻辑 ─────────────────────────────────────────────────────
def run(playwright):
    socks5_proxy = os.environ.get("SOCKS5_PROXY", "").strip()
    gost_proc = None
    proxy_config = None

    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            proxy_config = {"server": f"http://127.0.0.1:{LOCAL_HTTP_PORT}"}
            print("✅ 浏览器将通过代理访问。")
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，将直接连接。")
    else:
        print("ℹ️ 未配置 SOCKS5_PROXY，直接连接。")

    browser = playwright.chromium.launch(headless=True, proxy=proxy_config)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )

    # ── 解析 Cookie ──
    raw_cookies = os.environ.get("ACL_COOKIES", "").strip()
    if not raw_cookies:
        print("❌ 未找到 ACL_COOKIES 环境变量。")
        tg_send("🔴 <b>ACLClouds 续期通知</b>\n\n❌ 未找到 ACL_COOKIES 环境变量。")
        browser.close()
        if gost_proc:
            gost_proc.terminate()
        return

    normalized = raw_cookies.replace("\n", ";").replace("\r", "")
    cookies = []
    for item in normalized.split(";"):
        item = item.strip()
        if "=" in item:
            name, value = item.split("=", 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "url": "https://aclclouds.com",
            })
    print(f"解析到 {len(cookies)} 个 Cookie")

    context.add_cookies(cookies)
    page = context.new_page()

    try:
        print(f"访问服务器控制台：{SERVER_CONSOLE_URL}")
        page.goto(SERVER_CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        if "login" in page.url or "signin" in page.url:
            print("❌ Cookie 未生效，重定向到登录页。")
            page.screenshot(path="dashboard_status.png", full_page=True)
            tg_send(
                "🔴 <b>ACLClouds 续期通知</b>\n\n"
                "❌ <b>登录失败</b>：Cookie 已过期，请重新获取并更新 Secret。",
                photo_path="dashboard_status.png",
            )
            return

        print(f"✅ 成功进入页面：{page.url}")

        expire_info = get_expire_info(page)
        print(f"⏳ 当前服务器到期状态：{expire_info}")

        # ── 查找 Renew / Reactivate 按钮 ──
        renew_btns = page.locator("button:has-text('Renew'), button:has-text('Renouveler'), a:has-text('Renew'), a:has-text('Renouveler')")
        reactivate_btns = page.locator("button:has-text('Reactivate'), button:has-text('Réactiver'), a:has-text('Reactivate'), a:has-text('Réactiver')")
        renew_count = renew_btns.count()
        reactivate_count = reactivate_btns.count()
        print(f"Renew 按钮：{renew_count}，Reactivate 按钮：{reactivate_count}")

        status_el = page.locator(".mc-bar-status-text")
        server_status = status_el.inner_text().strip() if status_el.count() > 0 else "Online"
        print(f"⚡ 服务器当前运行状态：{server_status}")

        # ── 未到续期窗口 ──
        if renew_count == 0 and reactivate_count == 0:
            if server_status.lower() != "online":
                safe_start_server(page)

            page.screenshot(path="dashboard_status.png", full_page=True)
            print(f"ℹ️ 未检测到 Renew / Reactivate 按钮（{expire_info}），发送状态通知并跳过。")
            
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_info)}</code>\n"
                f"⚡ <b>运行状态：</b><code>{html.escape(server_status)}</code>\n"
                f"📌 <b>续期状态：</b>未到操作窗口（到期前 2 天开放）",
                photo_path="dashboard_status.png"
            )
            return

        # ── 存在续期/激活按钮时进行操作 ──
        action_name = "Renew" if renew_count > 0 else "Reactivate"
        target_btn = renew_btns.first if renew_count > 0 else reactivate_btns.first
        
        target_btn.scroll_into_view_if_needed()
        target_btn.click()
        print(f"已点击 {action_name} 按钮，等待响应...")
        page.wait_for_timeout(4000)

        # 刷新页面检查续期后状态与天数
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        expire_info_after = get_expire_info(page)
        status_el = page.locator(".mc-bar-status-text")
        server_status = status_el.inner_text().strip() if status_el.count() > 0 else "unknown"

        if server_status.lower() != "online":
            safe_start_server(page)

        page.screenshot(path="final_page.png", full_page=True)
        
        tg_send(
            f"📋 <b>ACLClouds 续期通知</b>\n\n"
            f"✅ <b>{action_name}：</b>操作已执行\n"
            f"⏳ <b>最新到期状态：</b><code>{html.escape(expire_info_after)}</code>\n"
            f"⚡ <b>服务器状态：</b><code>{html.escape(server_status)}</code>",
            photo_path="final_page.png",
        )
        print("\n任务执行完毕。")

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行过程中发生错误: {err_msg}")
        try:
            page.screenshot(path="dashboard_status.png", full_page=True)
        except Exception:
            pass
        tg_send(
            f"🔴 <b>ACLClouds 续期通知</b>\n\n"
            f"❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="dashboard_status.png",
        )
    finally:
        browser.close()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


with sync_playwright() as playwright:
    run(playwright)
