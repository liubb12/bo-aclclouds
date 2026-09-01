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

SERVER_ID = "75e19d55"
SERVER_CONSOLE_URL = f"https://aclclouds.com/server/{SERVER_ID}"


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
        time_match = re.search(r'(?i)(?:Time remaining|remaining|expire)[\s:]*([0-9]+\s*[jdhm]\s*(?:[0-9]+\s*[hm])?)', body_text)
        if time_match:
            expire_info = f"剩余 {time_match.group(1).strip()}"
        else:
            time_match_simple = re.search(r'(?i)\b(\d+\s*[jd]\s*(?:\d+\s*[hm])?)\b', body_text)
            if time_match_simple:
                expire_info = f"剩余 {time_match_simple.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取天数异常: {e}")
    return expire_info


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

    browser = playwright.chromium.launch(
        headless=True,
        proxy=proxy_config,
        args=["--no-sandbox", "--disable-setuid-sandbox"]
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080}
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

    context.add_cookies(cookies)
    page = context.new_page()

    network_logs = []
    def log_response(res):
        if any(k in res.url.lower() for k in ["renew", "server", "api", "extension"]):
            try:
                network_logs.append(f"[{res.status}] {res.url.split('?')[0]}")
            except Exception:
                pass

    page.on("response", log_response)

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

        expire_info_before = get_expire_info(page)
        print(f"⏳ 续期前服务器到期状态：{expire_info_before}")

        # ── 1. 定位顶部横条中的黑色 Renew 按钮 ──
        button_info = page.evaluate("""
            () => {
                // 遍历所有可能的按钮与可点击元素
                const candidates = Array.from(document.querySelectorAll('button, a, div[role="button"], span[role="button"]'));
                
                // 寻找文本严格包含 Renew/Renouveler 且不是大容器的叶子/小组件节点
                const match = candidates.find(el => {
                    const txt = el.innerText ? el.innerText.trim() : '';
                    const isRenew = txt.toLowerCase().includes('renew') || txt.toLowerCase().includes('renouveler');
                    const isSmall = el.clientWidth < 300 && el.clientHeight < 100;
                    return isRenew && isSmall && el.id !== 'app';
                });

                if (!match) return null;
                const rect = match.getBoundingClientRect();
                return {
                    tagName: match.tagName,
                    text: match.innerText.trim(),
                    x: rect.x + rect.width / 2,
                    y: rect.y + rect.height / 2,
                    outerHTML: match.outerHTML.slice(0, 200)
                };
            }
        """)

        print(f"🔍 真实 Renew 按钮定位结果: {button_info}")

        if not button_info:
            print("⚠️ 未能在提示栏中定位到独立的 Renew 按钮")
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_info_before)}</code>\n"
                f"📌 <b>未找到可点击的 Renew 按钮</b>",
                photo_path="dashboard_status.png"
            )
            return

        # ── 2. 真实物理点击定位到的 Renew 按钮 ──
        target_x = button_info["x"]
        target_y = button_info["y"]
        print(f"👉 鼠标移动并物理点击真实按钮坐标: ({target_x}, {target_y}) ...")
        
        page.mouse.move(target_x, target_y)
        page.wait_for_timeout(300)
        page.mouse.click(target_x, target_y)
        page.wait_for_timeout(3000)

        # ── 3. 检测是否弹出二次确认框/模态框 ──
        modal_clicked = page.evaluate("""
            () => {
                const modalBtns = Array.from(document.querySelectorAll('.swal2-confirm, div[role="dialog"] button, .modal button'));
                const confirmBtn = modalBtns.find(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                if (confirmBtn) {
                    confirmBtn.click();
                    return true;
                }
                return false;
            }
        """)
        if modal_clicked:
            print("👉 已点击弹窗确认按钮！")
            page.wait_for_timeout(3000)

        # ── 4. 刷新页面验证续期结果 ──
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        expire_info_after = get_expire_info(page)
        status_el = page.locator(".mc-bar-status-text")
        server_status = status_el.inner_text().strip() if status_el.count() > 0 else "Online"

        page.screenshot(path="final_page.png", full_page=True)

        logs_summary = "\n".join(network_logs[-3:]) if network_logs else "无相关接口触发"
        print(f"📡 捕获网络日志: {logs_summary}")

        tg_send(
            f"📋 <b>ACLClouds 续期结果反馈</b>\n\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"⚡ <b>服务器状态：</b><code>{html.escape(server_status)}</code>\n"
            f"🎯 <b>点击坐标：</b><code>({target_x:.1f}, {target_y:.1f})</code>\n"
            f"📡 <b>接口响应：</b>\n<code>{html.escape(logs_summary)}</code>",
            photo_path="final_page.png",
        )
        print(f"\n任务执行完毕，最新状态: {expire_info_after}")

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
