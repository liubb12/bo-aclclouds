#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# ACLClouds 自动登录与服务器续期脚本 (SeleniumBase UC 版)
# ============================================================
import os
import re
import html
import time
import socket
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_URL = "https://aclclouds.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
SERVER_ID = "75e19d55"
SERVER_CONSOLE_URL = f"{BASE_URL}/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
ACL_USERNAME = os.environ.get("ACL_USERNAME", "").strip()
ACL_PASSWORD = os.environ.get("ACL_PASSWORD", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


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
            print("  ✅ TG 通知发送成功")
        else:
            print(f"  ⚠️ TG 通知发送失败: {resp.text}")
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


def solve_turnstile(driver, max_wait=20):
    """检测并物理点击 Cloudflare Turnstile 验证框"""
    for i in range(max_wait):
        try:
            token = driver.execute_script("""
                const el = document.querySelector('input[name="cf-turnstile-response"]');
                return el ? el.value : null;
            """)
            if token and len(token) > 20:
                print("  🛡️ Turnstile 验证已顺利通过！", flush=True)
                return True
        except Exception:
            pass

        if i % 2 == 0:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass
        time.sleep(1)
    return False


def get_expire_info(driver) -> str:
    """提取页面到期信息"""
    expire_info = "未知"
    try:
        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
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


def main():
    if not ACL_USERNAME or not ACL_PASSWORD:
        print("❌ 未在 Secrets 中配置 ACL_USERNAME 或 ACL_PASSWORD", flush=True)
        return

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理检测正常，已启用中转。")
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，将尝试直连。")

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 登录页面
        print(f"🌐 正在打开登录页面: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=4)
        time.sleep(3)

        user_selector = "input[name='user'], input[name='username'], input[name='email'], input[type='text'], input[type='email']"
        driver.wait_for_element_visible(user_selector, timeout=25)

        user_elem = driver.find_element(By.CSS_SELECTOR, user_selector)
        user_elem.click()
        user_elem.clear()
        user_elem.send_keys(ACL_USERNAME)
        time.sleep(1)

        pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        pwd_elem.click()
        pwd_elem.clear()
        pwd_elem.send_keys(ACL_PASSWORD)
        time.sleep(1)

        print("🛡️ 正在进行登录页 Turnstile 人机验证与物理点击...", flush=True)
        solve_turnstile(driver, max_wait=20)
        time.sleep(2)

        print("🔑 正在提交登录...", flush=True)
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
            driver.execute_script("arguments[0].click();", submit_btn)
        except Exception:
            pwd_elem.send_keys(Keys.RETURN)

        for _ in range(12):
            if "/auth/login" not in driver.current_url:
                break
            time.sleep(1)

        if "/auth/login" in driver.current_url:
            driver.save_screenshot("login_failed.png")
            print("❌ 登录未成功跳转，停留在登录页", flush=True)
            tg_send(
                "🔴 <b>ACLClouds 续期通知</b>\n\n❌ <b>登录失败</b>：用户名或密码错误，或人机验证未通过。",
                photo_path="login_failed.png"
            )
            return

        print(f"✅ 登录成功！当前页面: {driver.current_url}", flush=True)

        # 2. 访问服务器控制台
        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(5)

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_info_before}", flush=True)

        # 3. 寻找并点击提示栏里的 Renew 按钮
        renew_xpath = "//button[contains(., 'Renew') or contains(., 'Renouveler')]"
        try:
            driver.wait_for_element_visible(renew_xpath, by=By.XPATH, timeout=20)
        except Exception:
            pass

        renew_elements = driver.find_elements(By.XPATH, renew_xpath)
        if not renew_elements:
            print("ℹ️ 未检测到 Renew 按钮，可能未到续期时间", flush=True)
            driver.save_screenshot("dashboard_status.png")
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_info_before)}</code>\n"
                f"📌 <b>状态：</b>无需续期或未开放",
                photo_path="dashboard_status.png"
            )
            return

        print("👉 物理真实点击 Renew 按钮...", flush=True)
        try:
            renew_elements[0].click()
        except Exception:
            driver.execute_script("arguments[0].click();", renew_elements[0])
        time.sleep(3)

        # 4. 检测并确认弹窗
        modal_clicked = driver.execute_script("""
            const modalBtns = Array.from(document.querySelectorAll('.swal2-confirm, div[role="dialog"] button, .modal button'));
            for (let b of modalBtns) {
                if (b.offsetWidth > 0 && b.offsetHeight > 0) {
                    b.click();
                    return true;
                }
            }
            return false;
        """)
        if modal_clicked:
            print("👉 已点击弹窗确认按钮！", flush=True)
            time.sleep(3)

        # 5. 刷新页面检查最新天数
        driver.refresh()
        time.sleep(4)

        expire_info_after = get_expire_info(driver)
        driver.save_screenshot("final_page.png")

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        tg_send(
            f"📋 <b>ACLClouds 自动续期汇总</b>\n\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_page.png",
        )
        print(f"\n✅ 任务执行完毕，最新状态: {expire_info_after}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.save_screenshot("error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>ACLClouds 续期通知</b>\n\n❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="error.png",
        )
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
