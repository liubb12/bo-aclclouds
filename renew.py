import os
import time
import subprocess
import requests
from playwright.sync_api import sync_playwright

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
LOCAL_HTTP_PORT = 18080


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
                    data={
                        "chat_id": TG_CHAT_ID,
                        "caption": text,
                        "parse_mode": "HTML",
                    },
                    files={"photo": f},
                    timeout=30,
                )
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                url,
                data={
                    "chat_id": TG_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML",
                },
                timeout=30,
            )

        if resp.status_code == 200:
            print("✅ TG 通知发送成功")
        else:
            print(f"⚠️ TG 通知发送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ TG 通知异常: {e}")


def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()

    if proxy_value.startswith("socks5://"):
        proxy_value = proxy_value[len("socks5://"):]
    elif proxy_value.startswith("socks://"):
        proxy_value = proxy_value[len("socks://"):]
    elif proxy_value.startswith("socks4://"):
        raise ValueError("当前脚本仅支持 SOCKS5，不支持 SOCKS4。")
    elif proxy_value.startswith("http://") or proxy_value.startswith("https://"):
        raise ValueError("SOCKS5_PROXY 不能是 http/https 代理，请使用 socks/socks5 代理。")
    elif "://" in proxy_value:
        raise ValueError("SOCKS5_PROXY 协议头不受支持，请使用 socks://、socks5:// 或直接 host:port。")

    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误，应为 host:port 或 user:pass@host:port。")

    return proxy_value


def wait_http_proxy_ready(port: int, timeout: int = 15):
    proxies = {
        "http": f"http://127.0.0.1:{port}",
        "https": f"http://127.0.0.1:{port}",
    }
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
    normalized_proxy = normalize_socks5_proxy(socks_proxy)
    cmd = [
        "gost",
        "-L",
        f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
        "-F",
        f"socks5://{normalized_proxy}",
    ]

    print("启动 gost 代理...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式、账号密码和 gost 安装。")

    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"✅ gost 已启动，本地 HTTP 代理端口：{LOCAL_HTTP_PORT}")
    return proc


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

    browser = playwright.chromium.launch(
        headless=True,
        proxy=proxy_config,
    )

    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )

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
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": "dash.aclclouds.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                }
            )

    print(f"解析到 {len(cookies)} 个 Cookie")
    page = context.new_page()

    try:
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
            print("❌ Cookie 未生效，被重定向到登录页。")
            page.screenshot(path="final_page.png", full_page=True)
            tg_send(
                "🔴 <b>ACLClouds 续期通知</b>\n\n"
                "❌ <b>登录失败</b>：Cookie 已过期，请重新获取并更新 Secret。",
                photo_path="final_page.png",
            )
            return

        print(f"✅ 登录成功：{page.url}")

        renew_buttons = page.locator("text='Renew'")
        count = renew_buttons.count()
        print(f"Renew 按钮数量：{count}")

        if count == 0:
            page.screenshot(path="final_page.png", full_page=True)
            tg_send(
                "🟡 <b>ACLClouds 续期通知</b>\n\n"
                "ℹ️ 当前未检测到 Renew 按钮，可能暂不在续期窗口内。",
                photo_path="final_page.png",
            )
            return

        success_count = 0
        fail_count = 0

        for i in range(count):
            btn = renew_buttons.nth(i)
            if not btn.is_visible():
                continue

            btn.scroll_into_view_if_needed()
            btn.click()
            print(f"已点击第 {i + 1} 个 Renew 按钮")
            page.wait_for_timeout(4000)

            if page.locator("text='Server renewed successfully'").count() > 0:
                success_count += 1
                print(f"✅ 第 {i + 1} 个服务器续期成功")
            else:
                fail_count += 1
                print(f"⚠️ 第 {i + 1} 个服务器续期结果未知")

        page.screenshot(path="final_page.png", full_page=True)

        if success_count > 0 and fail_count == 0:
            status_icon = "🟢"
            status_text = f"续期成功（共 {success_count} 个服务器）"
        elif success_count > 0 and fail_count > 0:
            status_icon = "🟡"
            status_text = f"部分成功（成功 {success_count} 个 / 失败 {fail_count} 个）"
        else:
            status_icon = "🔴"
            status_text = f"续期失败（{fail_count} 个服务器未确认成功）"

        tg_send(
            f"{status_icon} <b>ACLClouds 续期通知</b>\n\n"
            f"<b>结果：</b>{status_text}",
            photo_path="final_page.png",
        )

        print("任务执行完毕。")

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
