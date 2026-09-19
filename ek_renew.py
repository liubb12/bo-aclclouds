#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动巡检与续期 (子域名精准注入与卡片识别增强版)
# ============================================================
import html
import os
import random
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests
from seleniumbase import Driver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_URL = "https://dash.eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def human_sleep(min_s=1.0, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


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
            print("  ✅ TG 通知发送成功", flush=True)
        else:
            print(f"  ⚠️ TG 通知发送返回码 {resp.status_code}: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ TG 通知发送异常: {e}", flush=True)


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
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}", flush=True)
    return proc


def human_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.2, 0.4)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def solve_turnstile_challenge(driver, timeout=35):
    """处理续期弹窗中的 Turnstile 验证框"""
    print("  🛡️ 正在检测并处理续期人机验证框...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        confirm = driver.find_elements(By.XPATH, "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]")
        if confirm and confirm[0].is_enabled():
            print("  🟢 确认续期按钮已激活！", flush=True)
            return True

        try:
            driver.uc_gui_click_cf()
        except Exception:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass

        try:
            driver.switch_to.default_content()
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for f in iframes:
                src = f.get_attribute("src") or ""
                if any(k in src for k in ("cloudflare", "turnstile", "challenges")):
                    driver.switch_to.frame(f)
                    time.sleep(0.3)
                    boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], #checkbox, .ctp-checkbox-label")
                    if boxes:
                        ActionChains(driver).move_to_element(boxes[0]).pause(0.2).click().perform()
                        print("  🎯 成功物理点击 Turnstile 复选框！", flush=True)
                    driver.switch_to.default_content()
                    break
        except Exception:
            driver.switch_to.default_content()

        time.sleep(2)

    return False


def smart_inject_cookies(driver, raw_input: str):
    """精准向 dash.eknodes.es 与 .eknodes.es 注入各段鉴权凭证"""
    if not raw_input:
        return 0

    cookie_str = raw_input
    match_h = re.search(r"(?i)-H\s+['\"]cookie:\s*(.*?)['\"]", raw_input)
    if match_h:
        cookie_str = match_h.group(1)
    else:
        match_b = re.search(r"(?i)-b\s+['\"](.*?)['\"]", raw_input)
        if match_b:
            cookie_str = match_b.group(1)

    cookies_list = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        cookies_list.append((k.strip(), v.strip()))

    injected = 0
    for name, value in cookies_list:
        for domain in ["dash.eknodes.es", ".eknodes.es"]:
            try:
                driver.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "sameSite": "Lax"
                })
                injected += 1
                break
            except Exception:
                try:
                    driver.add_cookie({"name": name, "value": value, "path": "/"})
                    injected += 1
                    break
                except Exception:
                    pass
    return injected


def get_servers_info(driver):
    info = []
    try:
        # 定位卡片容器
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and (.//button[contains(., 'RENOVAR')] or .//button[contains(., 'GESTIONAR')])]")
        if not cards:
            cards = driver.find_elements(By.XPATH, "//div[contains(., 'Expira') and contains(@class, 'rounded')]")

        for c in cards:
            text = c.text
            match = re.search(r'Expira\s+([0-9]{1,2}\s+[a-zA-Z]+\s+[0-9]{4})', text)
            exp_date = match.group(1).strip() if match else "未知"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            name = lines[0] if lines else "Server"

            status = "ONLINE"
            if "Iniciando" in text:
                status = "Iniciando (启动中)"
            elif "Instalando" in text:
                status = "Instalando (安装中)"
            elif any(k in text for k in ("Inactivo", "Detenido", "Apagado")):
                status = "STOPPED (已停止)"
            elif "Activo" in text:
                status = "Activo (运行中)"

            info.append(f"• <b>{name}</b>: 状态 <code>{status}</code> | 到期 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "服务器正常运行"


def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动续期与巡检任务启动", flush=True)
    print("=" * 45, flush=True)

    if not EK_COOKIE:
        print("❌ 未在 Secrets 中配置 EK_COOKIE，无法继续执行！", flush=True)
        return

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理已启动并生效。", flush=True)
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，尝试直连模式。", flush=True)

    driver = Driver(uc=True, headless=False, proxy=uc_proxy, uc_subprocess=True)

    try:
        # 1. 直接访问目标子域 dash.eknodes.es
        print(f"🌐 [步骤 1] 打开控制台子域名建立 Session 环境: {BASE_URL} ...", flush=True)
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
        human_sleep(2.0, 3.5)

        # 2. 注入从真实浏览器提取的 Cookie 凭据
        print("🍪 [步骤 2] 精准注入已授权的 Cookie 凭证...", flush=True)
        injected_count = smart_inject_cookies(driver, EK_COOKIE)
        print(f"  ✅ 成功向浏览器注入 {injected_count} 个关键 Session 凭据！", flush=True)

        # 3. 导航至服务器管理列表
        print(f"🚀 [步骤 3] 直达服务器管理页: {SERVERS_URL} ...", flush=True)
        driver.get(SERVERS_URL)
        human_sleep(4.0, 6.0)

        # 4. 等待卡片与数据动态渲染完成
        print("⏳ [步骤 4] 等待服务器列表动态渲染...", flush=True)
        for _ in range(12):
            text = driver.get_text("body")
            if "RENOVAR" in text or "GESTIONAR" in text or "Expira" in text:
                print("  🎯 检测到服务器卡片已完成渲染！", flush=True)
                break
            time.sleep(1)

        driver.save_screenshot("ek_dashboard.png")
        status_before = get_servers_info(driver)
        print(f"📊 当前服务器状态:\n{status_before}", flush=True)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 5. 检索待续期按钮
        renovar_btn_xpath = "//button[contains(., 'RENOVAR') or contains(., 'Renovar')]"
        renovar_buttons = driver.find_elements(By.XPATH, renovar_btn_xpath)

        # 6. 执行续期动作
        if not renovar_buttons:
            print("ℹ️ 当前页面未检测到待续期按钮（周期已处于上限，无需续期）。", flush=True)
            tg_send(
                f"🛡️ <b>EKNodes 自动巡检正常</b>\n\n"
                f"📊 <b>实例状态：</b>\n{status_before}\n\n"
                f"⏭️ <b>执行结果：</b><code>周期充足，无需续期</code>\n"
                f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
                photo_path="ek_dashboard.png"
            )
            print("✅ 状态报告已推送到 Telegram。", flush=True)
            return

        renew_success = False
        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 正在点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(2.5, 3.5)

            # 处理弹窗内 Turnstile 人机验证
            solve_turnstile_challenge(driver, timeout=25)
            human_sleep(1.0, 2.0)

            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btns = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🚀 拟真点击 [CONFIRMAR RENOVACIÓN] 确认续期！", flush=True)
                human_click(driver, confirm_btns[0])
                renew_success = True
                human_sleep(4.0, 6.0)
            else:
                print("  ⚠️ 未找到确认续期按钮或按钮未激活", flush=True)

        # 7. 刷新获取续期后最新状态
        driver.refresh()
        human_sleep(4.0, 6.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        result_tag = "✅ 续期完成 (+7天)" if renew_success else "⚠️ 续期操作已触发"
        tg_send(
            f"🎉 <b>EKNodes 服务器续期报告</b>\n\n"
            f"⏳ <b>续期前状态：</b>\n{status_before}\n\n"
            f"⌛ <b>续期后状态：</b>\n{status_after}\n\n"
            f"📊 <b>执行结果：</b><code>{result_tag}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部流程执行完毕，已推送到 Telegram！", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 执行异常: {err}", flush=True)
        try:
            driver.save_screenshot("ek_error.png")
            tg_send(f"🔴 <b>EKNodes 异常</b>\n\n<code>{html.escape(err)}</code>", photo_path="ek_error.png")
        except Exception:
            pass
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理中转已退出。", flush=True)


if __name__ == "__main__":
    main()
