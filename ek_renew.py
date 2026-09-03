#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动登录与服务器续期脚本 (全状态 TG 通知 + 深度渲染等待版)
# ============================================================
import os
import re
import html
import time
import random
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

BASE_URL = "https://dash.eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_EMAIL = os.environ.get("EK_EMAIL", "").strip()
EK_USERNAME = os.environ.get("EK_USERNAME", "").strip()
EK_PASSWORD = os.environ.get("EK_PASSWORD", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def human_sleep(min_s=1.0, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知。")
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


def human_type(driver, element, text: str):
    try:
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.2)).click().perform()
        human_sleep(0.1, 0.3)
        element.send_keys(Keys.CONTROL, "a")
        human_sleep(0.1, 0.2)
        element.send_keys(Keys.BACKSPACE)
        human_sleep(0.1, 0.2)

        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.12))

        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
        human_sleep(0.2, 0.4)
    except Exception:
        pass


def human_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.2, 0.4)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def click_turnstile_checkbox(driver, timeout=30):
    """穿透点击 Turnstile 复选框"""
    print("  🛡️ 检测 Cloudflare 验证框并尝试点击...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        if "/servers" in driver.current_url:
            print("  🟢 页面已成功放行！", flush=True)
            return True

        # 方法 1：切入 iframe 物理点击
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
                        print("  🎯 成功切入 iframe 物理点击复选框！", flush=True)
                    driver.switch_to.default_content()
                    break
        except Exception:
            driver.switch_to.default_content()

        # 方法 2：SeleniumBase 原生兜底
        try:
            driver.uc_gui_click_cf()
        except Exception:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass

        time.sleep(3)
        if "/login" not in driver.current_url:
            print("  🟢 验证通过，已离开登录页！", flush=True)
            return True

    return False


def get_servers_info(driver):
    """提取页面上的服务器卡片信息"""
    info = []
    try:
        # 兼容多种卡片结构
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and (.//button[contains(., 'RENOVAR')] or .//button[contains(., 'GESTIONAR')])]")
        if not cards:
            cards = driver.find_elements(By.XPATH, "//div[contains(., 'Expira') and contains(@class, 'rounded')]")

        for c in cards:
            text = c.text
            match = re.search(r'Expira\s+([0-9A-Za-z\s]+)', text)
            exp_date = match.group(1).strip() if match else "未知"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            name = lines[0] if lines else "Server"
            info.append(f"• <b>{name}</b>: 到期时间 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "服务器运行正常"


def main():
    print("=== EKNodes 自动续期任务启动 ===", flush=True)
    login_account = EK_EMAIL if EK_EMAIL else EK_USERNAME
    if not login_account or not EK_PASSWORD:
        print("❌ 未在 Secrets 中配置登录邮箱 (EK_EMAIL) 或密码 (EK_PASSWORD)", flush=True)
        return

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理已启动。", flush=True)
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，尝试直连。", flush=True)

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 访问登录页
        print(f"🌐 正在访问登录页: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        human_sleep(3.0, 4.5)

        if "/servers" not in driver.current_url:
            email_elem = driver.wait_for_element_visible("input[type='email'], input[type='text']", timeout=20)
            masked_acc = login_account[:3] + "***" if len(login_account) > 3 else "***"
            print(f"  📝 [第一步] 填入登录邮箱账号: {masked_acc}", flush=True)
            human_type(driver, email_elem, login_account)
            human_sleep(0.5, 0.8)

            pwd_elem = driver.wait_for_element_visible("input[type='password']", timeout=10)
            print("  📝 [第一步] 填入密码...", flush=True)
            human_type(driver, pwd_elem, EK_PASSWORD)
            human_sleep(0.6, 1.2)

            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'INICIAR SESIÓN') or contains(., 'Iniciar')]")
            print("🔑 [第一步] 点击 INICIAR SESIÓN 按钮提交...", flush=True)
            human_click(driver, submit_btn)

            # 等待 Turnstile 弹窗并完成验证
            human_sleep(2.0, 3.0)
            print("🛡️ [第二步] 正在处理弹出的 Cloudflare 人机验证...", flush=True)
            click_turnstile_checkbox(driver, timeout=35)

            for _ in range(15):
                if "/login" not in driver.current_url:
                    break
                time.sleep(1)

            if "/login" in driver.current_url:
                driver.save_screenshot("ek_login_fail.png")
                raise RuntimeError("登录未跳转，请检查账号密码或验证码是否点击成功")

            print(f"✅ 登录成功！当前 URL: {driver.current_url}", flush=True)

        # 2. 无论停在哪个页面，强制访问 /servers 并等待 DOM 加载完毕
        print(f"🚀 正在进入服务器管理页面: {SERVERS_URL} ...", flush=True)
        driver.get(SERVERS_URL)
        human_sleep(5.0, 7.0)

        # 显式等待：等待页面出现 "SERVIDORES" 标题或任何卡片
        try:
            driver.wait_for_element_present("//h1[contains(., 'SERVIDORES')] | //button[contains(., 'GESTIONAR')]", timeout=20)
            print("  🎯 检测到服务器管理页面主要内容已加载完成！", flush=True)
        except Exception:
            print("  ⚠️ 等待主元素超时，继续尝试检索卡片...", flush=True)

        status_before = get_servers_info(driver)
        print(f"📊 当前服务器状态:\n{status_before}", flush=True)

        # 3. 抓取所有可点击的 RENOVAR 按钮
        renovar_btn_xpath = "//button[contains(., 'RENOVAR') or contains(., 'Renovar')]"
        renovar_buttons = driver.find_elements(By.XPATH, renovar_btn_xpath)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 如果没有找到 RENOVAR 按钮（说明当前已是满额 7 天，暂无需续期）
        if not renovar_buttons:
            print("ℹ️ 当前页面未检测到待续期按钮（服务器周期已是满额 7 天）。", flush=True)
            driver.save_screenshot("ek_current_status.png")
            # 必须发送巡检正常通知
            tg_send(
                f"🛡️ <b>EKNodes 自动巡检正常</b>\n\n"
                f"当前服务器到期时间充足（无需续期）：\n{status_before}\n\n"
                f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
                photo_path="ek_current_status.png"
            )
            print("✅ 状态正常通知已推送到 Telegram。", flush=True)
            return

        # 4. 如果有 RENOVAR 按钮，逐一点击续期
        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 正在点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(3.0, 4.5)

            # 弹窗内验证码
            print("  🛡️ 检查并处理续期弹窗内 Turnstile 验证码...", flush=True)
            click_turnstile_checkbox(driver, timeout=20)
            human_sleep(1.5, 2.5)

            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btns = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🚀 拟真点击 [CONFIRMAR RENOVACIÓN] 确认续期！", flush=True)
                human_click(driver, confirm_btns[0])
                human_sleep(4.0, 6.0)
            else:
                print("  ⚠️ 未找到确认续期按钮或按钮未激活", flush=True)

        # 5. 续期完成后刷新并推送成功通知
        driver.refresh()
        human_sleep(4.0, 6.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        tg_send(
            f"🎉 <b>EKNodes 服务器自动续期成功</b>\n\n"
            f"<b>续期前：</b>\n{status_before}\n\n"
            f"<b>续期后：</b>\n{status_after}\n\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部操作已顺利完成，已推送到 Telegram！", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 执行异常: {err}", flush=True)
        try:
            driver.save_screenshot("ek_error.png")
            tg_send(f"🔴 <b>EKNodes 续期异常</b>\n\n<code>{html.escape(err)}</code>", photo_path="ek_error.png")
        except Exception:
            pass
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理已退出。", flush=True)


if __name__ == "__main__":
    main()
