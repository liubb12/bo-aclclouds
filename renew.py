#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import json
import html
import time
import random
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

BASE_PANEL_URL = "https://panel.aclclouds.com"
LOGIN_URL = f"{BASE_PANEL_URL}/auth/login"

LOCAL_HTTP_PORT = 18082
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

ACL_USERNAME = os.environ.get("ACL_USERNAME", "").strip() or os.environ.get("ACL_EMAIL", "").strip()
ACL_PASSWORD = os.environ.get("ACL_PASSWORD", "").strip()
ACL_COOKIES = os.environ.get("ACL_COOKIES", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


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
    except Exception as e:
        print(f"  ⚠️ TG 发送异常: {e}", flush=True)


def start_gost(socks_proxy: str):
    proxy_val = socks_proxy.strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_val.startswith(prefix):
            proxy_val = proxy_val[len(prefix):]
            break
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{proxy_val}"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    return proc


def clean_popups(driver):
    """清除遮罩和 PWA 弹窗"""
    try:
        btns = driver.find_elements(By.XPATH, "//button[contains(translate(., 'CLOSE', 'close'), 'close') or contains(., 'Dismiss')]")
        for b in btns:
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                time.sleep(0.5)
    except Exception:
        pass


def safe_open(driver, url, max_retries=3):
    """带网络容错与重试的页面加载"""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"🌐 尝试访问 (第 {attempt} 次): {url} ...", flush=True)
            driver.uc_open_with_reconnect(url, reconnect_time=8)
            time.sleep(4)
            # 校验是否白屏或错误页
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "ERR_CONNECTION" not in body_text and "can't be reached" not in body_text:
                return True
            print("  ⚠️ 出现网络中断/重置，准备重试...", flush=True)
        except Exception as e:
            print(f"  ⚠️ 加载异常: {e}", flush=True)
        time.sleep(3)
    return False


def main():
    print("=== ACLClouds 自动续期任务启动 ===", flush=True)
    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print(f"✅ gost 代理就绪: {LOCAL_HTTP_PORT}", flush=True)
        except Exception as e:
            print(f"⚠️ gost 启动失败: {e}", flush=True)

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 尝试打开基础面板
        if not safe_open(driver, BASE_PANEL_URL):
            raise RuntimeError("网络连接失败，无法访问 panel.aclclouds.com (ERR_CONNECTION_CLOSED)")

        # 2. 注入 Cookie（如果有）
        if ACL_COOKIES:
            print("🍪 注入 ACL_COOKIES 免登...", flush=True)
            try:
                cookies = json.loads(ACL_COOKIES)
                if isinstance(cookies, dict):
                    cookies = [{"name": k, "value": v} for k, v in cookies.items()]
                for c in cookies:
                    driver.add_cookie({"name": c["name"], "value": c["value"], "domain": c.get("domain", ".aclclouds.com")})
                driver.refresh()
                time.sleep(4)
            except Exception as e:
                print(f"  ⚠️ Cookies 注入失败: {e}", flush=True)

        # 3. 如果没登进去，尝试走账号密码
        if "/auth/login" in driver.current_url:
            print("🔑 使用账号密码登录...", flush=True)
            user_in = driver.wait_for_element_visible("input[name='username'], input[type='text']", timeout=15)
            user_in.clear()
            user_in.send_keys(ACL_USERNAME)
            pwd_in = driver.wait_for_element_visible("input[name='password'], input[type='password']", timeout=10)
            pwd_in.clear()
            pwd_in.send_keys(ACL_PASSWORD)
            sub_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Log In')]")
            driver.execute_script("arguments[0].click();", sub_btn)
            time.sleep(5)

        # 4. 进入实例控制台
        clean_popups(driver)
        if "/server/" not in driver.current_url:
            cards = driver.find_elements(By.XPATH, "//a[contains(@href, '/server/')]")
            if cards:
                print("🖥️ 点击进入实例控制台...", flush=True)
                driver.execute_script("arguments[0].click();", cards[0])
                time.sleep(5)

        clean_popups(driver)

        # 5. 读取续期前时间
        def get_time():
            try:
                el = driver.find_element(By.XPATH, "//*[contains(text(), 'Time remaining')]/..")
                m = re.search(r'(\d+\s*d\s*\d+\s*h)', el.text)
                return m.group(1) if m else el.text.split("\n")[0]
            except Exception:
                return "未知"

        before_time = get_time()
        print(f"⏳ 续期前剩余时间: {before_time}", flush=True)

        # 6. 点击 Renew 按钮
        renew_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Renew')]")
        if renew_btns:
            print("👉 找到 Renew 按钮，触发点击...", flush=True)
            driver.execute_script("arguments[0].click();", renew_btns[0])
            time.sleep(2)
            # 点击可能的弹窗确认
            for kw in ("confirm", "yes", "renew"):
                c_btns = driver.find_elements(By.XPATH, f"//div[contains(@class, 'swal2') or contains(@role, 'dialog')]//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw}')]")
                for cb in c_btns:
                    if cb.is_displayed():
                        driver.execute_script("arguments[0].click();", cb)
                        time.sleep(2)
                        break
        else:
            print("⚠️ 未发现 Renew 按钮（可能不在窗口期或未加载完成）", flush=True)

        # 7. 等待落库并刷新确认
        time.sleep(6)
        driver.refresh()
        time.sleep(4)
        clean_popups(driver)

        after_time = get_time()
        print(f"📊 刷新后剩余时间: {after_time}", flush=True)

        # 8. 保存截图并推送
        driver.save_screenshot("acl_final.png")
        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        status_text = "🎉 <b>ACLClouds 续期成功！</b>" if before_time != after_time and "未知" not in after_time else "📋 <b>ACLClouds 自动巡检报备</b>"
        tg_send(
            f"{status_text}\n\n"
            f"⌛ <b>到期变动：</b>剩余 <code>{before_time}</code> ➜ 剩余 <code>{after_time}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="acl_final.png"
        )

    except Exception as e:
        err = str(e)
        print(f"❌ 运行报错: {err}", flush=True)
        try:
            driver.save_screenshot("acl_error.png")
            tg_send(f"🔴 <b>ACLClouds 运行异常</b>\n\n<code>{html.escape(err)}</code>", photo_path="acl_error.png")
        except Exception:
            pass
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()


if __name__ == "__main__":
    main()
