import os
import re
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
LOCAL_HTTP_PORT = 18080

# 你的专属控制台路径
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
                "domain": "aclclouds.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            })
    print(f"解析到 {len(cookies)} 个 Cookie")

    page = context.new_page()

    try:
        # ── 登录与导航 ──
        print("访问主域名...")
        page.goto("https://aclclouds.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        context.add_cookies(cookies)

        print("访问服务列表页面...")
        page.goto("https://aclclouds.com/services", wait_until="domcontentloaded", timeout=60000)
        
        # 确保服务卡片渲染
        try:
            page.wait_for_selector("text=/Mes Services|ÉCHÉANCE|Free|Mon VPS/i", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

        if "login" in page.url or "signin" in page.url:
            print("❌ Cookie 未生效，重定向到登录页。")
            page.screenshot(path="final_page.png", full_page=True)
            tg_send(
                "🔴 <b>ACLClouds 续期通知</b>\n\n"
                "❌ <b>登录失败</b>：Cookie 已过期，请重新获取并更新 Secret。",
                photo_path="final_page.png",
            )
            return

        print(f"✅ 登录成功：{page.url}")

        # ── 提取页面剩余天数与到期时间 ──
        expire_info = "未知"
        try:
            body_text = page.inner_text("body").replace("\u00a0", " ").replace("\u202f", " ")

            # 匹配 3j 23h 等
            time_match = re.search(r'(?i)\b(\d+\s*[jd]\s*(?:\d+\s*[hm])?)\b', body_text)
            if time_match:
                expire_info = f"剩余 {time_match.group(1).strip()}"
            else:
                prefix_match = re.search(r'(?i)(?:EXPIRE\s*DANS|Expire\s*in)[\s:]*([^\n\r]+)', body_text)
                if prefix_match:
                    expire_info = prefix_match.group(0).strip()
                else:
                    notice_match = re.search(r'(?i)(Le renouvellement sera disponible[^\n\r]+)', body_text)
                    if notice_match:
                        expire_info = notice_match.group(1).strip()
        except Exception as e:
            print(f"⚠️ 提取天数异常: {e}")

        print(f"⏳ 当前服务器到期状态：{expire_info}")

        # ── 查找 Renew / Reactivate 按钮 ──
        renew_btns       = page.locator("button:has-text('Renew'), button:has-text('Renouveler'), a:has-text('Renew'), a:has-text('Renouveler')")
        reactivate_btns  = page.locator("button:has-text('Reactivate'), button:has-text('Réactiver'), a:has-text('Reactivate'), a:has-text('Réactiver')")
        renew_count      = renew_btns.count()
        reactivate_count = reactivate_btns.count()
        print(f"Renew 按钮：{renew_count}，Reactivate 按钮：{reactivate_count}")

        # ── 未到续期窗口：截图 + TG 推送 ──
        if renew_count == 0 and reactivate_count == 0:
            page.screenshot(path="dashboard_status.png", full_page=True)
            print(f"ℹ️ 未检测到 Renew / Reactivate 按钮（{expire_info}），已保存截图并推送到 Telegram。")
            
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{expire_info}</code>\n"
                f"📌 <b>续期状态：</b>未到操作窗口（到期前 2 天开放）",
                photo_path="dashboard_status.png"
            )
            return

        # ── 逐服务器处理（Renew 与 Reactivate 互斥）──
        results = []

        def handle_action_buttons(locator, action_name: str, total: int):
            for i in range(total):
                btns = page.locator(f"button:has-text('{action_name}'), a:has-text('{action_name}')")
                btn = btns.nth(0)
                if not btn.is_visible():
                    print(f"  第 {i+1} 个 {action_name} 按钮不可见，跳过。")
                    results.append({"action": action_name, "action_ok": False, "server_status": "unknown"})
                    continue

                btn.scroll_into_view_if_needed()
                btn.click()
                print(f"  已点击第 {i+1} 个 {action_name} 按钮，等待响应...")
                page.wait_for_timeout(4000)

                action_ok = page.locator("text=/Server renewed successfully|renouvelé avec succès/i").count() > 0
                if action_ok:
                    print(f"  ✅ {action_name} 成功")
                else:
                    print(f"  ⚠️ {action_name} 结果未确认")

                # ── 直达 Console 控制台 ──
                print(f"  正在进入控制台：{SERVER_CONSOLE_URL}")
                page.goto(SERVER_CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                # ── 读取服务器状态 ──
                status_el = page.locator(".mc-bar-status-text")
                server_status = status_el.inner_text().strip() if status_el.count() > 0 else "unknown"
                print(f"  服务器状态：{server_status}")

                # ── 若 Offline 则点击 Start ──
                if server_status.lower() != "online":
                    print("  服务器 Offline，尝试点击 Start...")
                    start_btn = page.locator("button.power-btn[data-variant='start'], button:has-text('Démarrer')")
                    if start_btn.count() > 0 and start_btn.is_visible():
                        start_btn.click()
                        print("  已点击 Start，监控 30s 等待上线...")
                        deadline = time.time() + 30
                        while time.time() < deadline:
                            page.wait_for_timeout(3000)
                            status_el = page.locator(".mc-bar-status-text")
                            server_status = status_el.inner_text().strip() if status_el.count() > 0 else "unknown"
                            print(f"    当前状态：{server_status}")
                            if server_status.lower() == "online":
                                print("  ✅ 服务器已上线。")
                                break
                        else:
                            print("  ⚠️ 30s 内服务器未上线。")
                    else:
                        print("  ⚠️ 未找到 Start 按钮。")

                results.append({"action": action_name, "action_ok": action_ok, "server_status": server_status})

        if renew_count > 0:
            print(f"\n── 处理 {renew_count} 个 Renew ──")
            handle_action_buttons(renew_btns, "Renew", renew_count)
        elif reactivate_count > 0:
            print(f"\n── 处理 {reactivate_count} 个 Reactivate ──")
            handle_action_buttons(reactivate_btns, "Reactivate", reactivate_count)

        page.wait_for_timeout(2000)
        page.screenshot(path="final_page.png", full_page=True)
        print("✅ Console 页面截图已保存")

        need_notify = False
        lines = []

        for r in results:
            action     = r["action"]
            ok         = r["action_ok"]
            status     = r["server_status"]
            is_offline = status.lower() != "online"

            need_notify = True

            action_icon = "✅" if ok else "❌"
            action_text = "成功" if ok else "失败"

            if is_offline and status != "unknown":
                status_text = "Offline，已执行重启"
            elif status.lower() == "online":
                status_text = "Online"
            else:
                status_text = status

            lines.append(
                f"{action_icon} <b>{action}：</b>{action_text}\n"
                f"   ⏳ <b>到期状态：</b><code>{expire_info}</code>\n"
                f"   ⚡ <b>服务器状态：</b>{status_text}"
            )

        if need_notify:
            tg_send(
                "📋 <b>ACLClouds 续期通知</b>\n\n" + "\n\n".join(lines),
                photo_path="final_page.png",
            )

        print("\n任务执行完毕。")

    except Exception as e:
        print(f"❌ 执行过程中发生错误: {e}")
        try:
            page.screenshot(path="final_page.png", full_page=True)
        except Exception:
            pass
        tg_send(
            f"🔴 <b>ACLClouds 续期通知</b>\n\n"
            f"❌ <b>脚本执行异常</b>：\n<code>{e}</code>",
            photo_path="final_page.png",
        )
    finally:
        browser.close()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


with sync_playwright() as playwright:
    run(playwright)
