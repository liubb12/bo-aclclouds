import os
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
LOCAL_HTTP_PORT = 18080


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
                "domain": "dash.aclclouds.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
            })
    print(f"解析到 {len(cookies)} 个 Cookie")

    page = context.new_page()

    try:
        # ── 登录 ──
        print("访问主域名...")
        page.goto("https://dash.aclclouds.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        context.add_cookies(cookies)

        print("访问项目页面...")
        page.goto("https://dash.aclclouds.com/projects", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("text='My Projects'", timeout=15000)
        except Exception:
            pass

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

        # ── 查找 Renew / Reactive 按钮 ──
        renew_btns     = page.locator("text='Renew'")
        reactivate_btns = page.locator("text='Reactive'")
        renew_count     = renew_btns.count()
        reactivate_count = reactivate_btns.count()
        print(f"Renew 按钮：{renew_count}，Reactive 按钮：{reactivate_count}")

        if renew_count == 0 and reactivate_count == 0:
            # 不在操作窗口，静默跳过
            print("ℹ️ 未检测到 Renew / Reactive 按钮，不在操作窗口，本次跳过。")
            return

        # ── 逐服务器处理（Renew 与 Reactive 互斥，只会执行其中一个）──

        results = []  # 每条: {"action": "Renew"|"Reactive", "action_ok": bool, "server_status": str}

        def handle_action_buttons(locator, action_name: str, total: int):
            """点击续期/重激活按钮，然后进入 Manage 检查状态。"""
            for i in range(total):
                is_last = (i == total - 1)
                # 每次重新定位，避免 DOM 刷新后引用失效
                btns = page.locator(f"text='{action_name}'")
                btn = btns.nth(0)  # 每次取第一个未处理的
                if not btn.is_visible():
                    print(f"  第 {i+1} 个 {action_name} 按钮不可见，跳过。")
                    results.append({"action": action_name, "action_ok": False, "server_status": "unknown"})
                    continue

                # ── 点击续期/激活按钮 ──
                btn.scroll_into_view_if_needed()
                btn.click()
                print(f"  已点击第 {i+1} 个 {action_name} 按钮，等待响应...")
                page.wait_for_timeout(4000)

                action_ok = page.locator("text='Server renewed successfully'").count() > 0
                if action_ok:
                    print(f"  ✅ {action_name} 成功")
                else:
                    print(f"  ⚠️ {action_name} 结果未确认")

                # ── 进入 Manage → Console 页面 ──
                manage_btn = page.locator("text='Manage'").first
                if manage_btn.count() == 0 or not manage_btn.is_visible():
                    print("  ⚠️ 未找到 Manage 按钮，跳过服务器状态检查。")
                    results.append({"action": action_name, "action_ok": action_ok, "server_status": "unknown"})
                    continue

                manage_btn.click()
                print("  已点击 Manage，等待 Console 页面加载...")
                try:
                    page.wait_for_selector(".mc-bar-status-text", timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)

                # ── 读取服务器状态 ──
                status_el = page.locator(".mc-bar-status-text")
                server_status = status_el.inner_text().strip() if status_el.count() > 0 else "unknown"
                print(f"  服务器状态：{server_status}")

                # ── 若 Offline 则点击 Start ──
                if server_status.lower() != "online":
                    print("  服务器 Offline，尝试点击 Start...")
                    start_btn = page.locator("button.power-btn[data-variant='start']")
                    if start_btn.count() > 0 and start_btn.is_visible():
                        start_btn.click()
                        print("  已点击 Start，监控 30s 等待上线...")
                        # 监控 30s
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

                # ── 最后一个留在 Console 页面截图；其余返回列表处理下一个 ──
                if not is_last:
                    page.goto("https://dash.aclclouds.com/projects", wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_selector("text='My Projects'", timeout=15000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)

        # Renew 与 Reactive 互斥，只会命中其中一个分支
        if renew_count > 0:
            print(f"\n── 处理 {renew_count} 个 Renew ──")
            handle_action_buttons(renew_btns, "Renew", renew_count)
        elif reactivate_count > 0:
            print(f"\n── 处理 {reactivate_count} 个 Reactive ──")
            handle_action_buttons(reactivate_btns, "Reactive", reactivate_count)

        # ── Console 页面截图（此时停留在最后一个服务器的 Console 页面）──
        page.wait_for_timeout(2000)
        page.screenshot(path="final_page.png", full_page=True)
        print("✅ Console 页面截图已保存")

        # ── 判断是否需要发送 TG 通知 ──
        # 规则：登录失败 / Renew 成功或失败 / Reactive 成功或失败 / 服务器 Offline 才通知
        need_notify = False
        lines = []

        for r in results:
            action   = r["action"]
            ok       = r["action_ok"]
            status   = r["server_status"]
            is_offline = status.lower() != "online"

            # 有 Renew/Reactive 操作就通知
            need_notify = True

            action_icon = "✅" if ok else "❌"
            action_text = "成功" if ok else "失败"

            if is_offline and status != "unknown":
                status_text = f"Offline，已执行重启"
            elif status.lower() == "online":
                status_text = "Online"
            else:
                status_text = status

            lines.append(
                f"{action_icon} <b>{action}：</b>{action_text}\n"
                f"   <b>服务器状态：</b>{status_text}"
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
