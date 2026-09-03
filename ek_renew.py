#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动登录与服务器续期脚本 (邮箱优先 + 强穿透版)
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


def human_sleep(min_s=1.0, max_s=2.5):
    time.sleep(random.uniform(min_s, max_s))


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(
                    url,
                    data={"chat_id": TG_CHAT_ID, "caption": text, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=30,
                )
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=30,
            )
        print("  ✅ TG 通知发送成功")
    except Exception as e:
        print(f"  ⚠️ TG 通知异常: {e}")


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
                print("  ✅ 本地 HTTP 代理连通性测试成功")
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}")
    return proc


def human_type(driver, element, text: str):
    try:
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
        human_sleep(0.2, 0.4)
        element.send_keys(Keys.CONTROL, "a")
        human_sleep(0.1, 0.2)
        element.send_keys(Keys.BACKSPACE)
        human_sleep(0.1, 0.3)

        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.14))

        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
        human_sleep(0.3, 0.6)
    except Exception:
        pass


def human_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.3, 0.6)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.15, 0.35)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def solve_turnstile_shield(driver, context_name="前置验证盾", max_wait=35):
    """
    专门穿透全屏 BIENVENIDO / Verify you are human 挑战
    只有当盾牌完全消失、出现真实的表单/页面时才返回
    """
    print(f"🛡️ 开始处理 [{context_name}] Cloudflare Turnstile 挑战...", flush=True)
    start = time.time()
    
    while time.time() - start < max_wait:
        body_text = driver.get_text("body")
        has_login_input = driver.execute_script(
            "return !!document.querySelector('input[type=\"password\"], input[type=\"email\"], input[name=\"email\"]');"
        )
        # 如果已经没有验证提示，且出现了真正的输入框或已经在控制台，说明穿透成功
        if not ("Verify you are human" in body_text or "BIENVENIDO" in body_text) and has_login_input:
            print(f"  🟢 [{context_name}] 成功穿透，已加载正式表单！", flush=True)
            return True

        if "/servers" in driver.current_url:
            print("  🟢 已直接进入服务器列表页面！", flush=True)
            return True

        print(f"  👉 正在尝试穿透 [{context_name}] 复选框...", flush=True)

        # 1. 尝试调用 SeleniumBase 原生穿透
        try:
            driver.uc_gui_click_cf()
            human_sleep(2.0, 3.0)
        except Exception:
            try:
                driver.uc_gui_click_captcha()
                human_sleep(2.0, 3.0)
            except Exception:
                pass

        # 2. 模拟物理点击 iframe 中的复选框
        try:
            driver.execute_script("""
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (let f of iframes) {
                    const src = f.getAttribute('src') || '';
                    if (src.includes('cloudflare') || src.includes('turnstile') || src.includes('challenges')) {
                        f.scrollIntoView({block: 'center'});
                        const rect = f.getBoundingClientRect();
                        const evt = new MouseEvent('click', {
                            bubbles: true,
                            cancelable: true,
                            clientX: rect.left + 28,
                            clientY: rect.top + (rect.height / 2),
                            view: window
                        });
                        f.dispatchEvent(evt);
                        break;
                    }
                }
            """)
        except Exception:
            pass

        time.sleep(2.5)

    return False


def get_servers_info(driver):
    info = []
    try:
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and .//button[contains(., 'RENOVAR')]]")
        for c in cards:
            text = c.text
            match = re.search(r'Expira\s+([0-9A-Za-z\s]+)', text)
            exp_date = match.group(1).strip() if match else "未知"
            lines = text.split("\n")
            name = lines[0].strip() if lines else "Server"
            info.append(f"• <b>{name}</b>: 到期时间 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "状态获取成功"


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
            print("🔗 代理已启动。")
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，尝试直连。")

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        print(f"🌐 正在访问登录页: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        human_sleep(3.0, 5.0)

        # 检查并穿透全屏盾牌
        body_text = driver.get_text("body")
        if "Verify you are human" in body_text or "BIENVENIDO" in body_text:
            solve_turnstile_shield(driver, context_name="前置登录盾")
            human_sleep(2.5, 4.0)

        # 登录流程
        if "/servers" not in driver.current_url:
            text_inputs = driver.find_elements(By.CSS_SELECTOR, "input:not([type='password']):not([type='checkbox']):not([type='hidden'])")
            
            if not text_inputs:
                driver.save_screenshot("ek_shield_blocked.png")
                raise RuntimeError("未能加载登录表单，依然被 Cloudflare 盾牌阻挡！")

            masked_acc = login_account[:3] + "***" if len(login_account) > 3 else "***"
            print(f"  📝 填入登录邮箱账号: {masked_acc}", flush=True)
            human_type(driver, text_inputs[0], login_account)
            human_sleep(0.5, 1.0)

            pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            print("  📝 填入密码...", flush=True)
            human_type(driver, pwd_elem, EK_PASSWORD)
            human_sleep(0.8, 1.5)

            # 点击登录提交
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Iniciar') or contains(., 'Login') or contains(., 'Entrar')]")
            print("🔑 点击登录按钮...", flush=True)
            human_click(driver, submit_btn)

            for _ in range(15):
                if "/login" not in driver.current_url:
                    break
                time.sleep(1)

            if "/login" in driver.current_url:
                driver.save_screenshot("ek_login_fail.png")
                raise RuntimeError("登录未跳转，请检查邮箱和密码是否正确，或触发了二次拦截")

            print(f"✅ 登录成功！当前 URL: {driver.current_url}", flush=True)

        # 访问服务器页面
        if "/servers" not in driver.current_url:
            human_sleep(1.5, 2.5)
            driver.get(SERVERS_URL)
            human_sleep(3.0, 4.0)

        status_before = get_servers_info(driver)
        print(f"📊 续期前服务器状态:\n{status_before}", flush=True)

        # 寻找 RENOVAR 按钮
        renovar_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'RENOVAR')]")
        if not renovar_buttons:
            print("ℹ️ 当前未找到 RENOVAR 按钮或尚未到期。", flush=True)
            driver.save_screenshot("ek_no_button.png")
            return

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(3.0, 4.5)

            # 续期弹窗内部的 Turnstile
            print(f"  🛡️ 等待服务器#{idx+1} 弹窗验证码完成...", flush=True)
            try:
                driver.uc_gui_click_cf()
            except Exception:
                pass
            human_sleep(2.0, 3.5)

            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btn = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btn and confirm_btn[0].is_displayed():
                print("  🚀 确认点击 [CONFIRMAR RENOVACIÓN]...", flush=True)
                human_click(driver, confirm_btn[0])
                human_sleep(3.5, 5.0)

        # 刷新检查
        driver.refresh()
        human_sleep(4.0, 5.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        tg_send(
            f"📋 <b>EKNodes 服务器自动续期汇总</b>\n\n"
            f"<b>续期前：</b>\n{status_before}\n\n"
            f"<b>续期后：</b>\n{status_after}\n\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部操作已完成！", flush=True)

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
            print("gost 代理已退出。")


if __name__ == "__main__":
    main()
