#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# ACLClouds 自动续期脚本 (增强版：修复遮挡/二次确认/刷新判定)
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

PANEL_URL = "https://panel.aclclouds.com"
LOGIN_URL = f"{PANEL_URL}/auth/login"

LOCAL_HTTP_PORT = 18082
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

ACL_EMAIL = os.environ.get("ACL_EMAIL", "").strip()
ACL_PASSWORD = os.environ.get("ACL_PASSWORD", "").strip()
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
    except Exception as e:
        print(f"  ⚠️ TG 通知发送异常: {e}", flush=True)


def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_value.startswith(prefix):
            proxy_value = proxy_value[len(prefix):]
            break
    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误。")
    return proxy_value


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败。")
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}", flush=True)
    return proc


def human_type(driver, element, text: str):
    try:
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.2)).click().perform()
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.03, 0.08))
        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
    except Exception:
        pass


def dismiss_annoying_popups(driver):
    """清理遮挡点击的弹窗（如 PWA 安装提示、提示浮层）"""
    try:
        # 关闭形如 "Install the panel on your home screen" 等弹窗
        close_btns = driver.find_elements(By.XPATH, "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' or contains(., 'Close') or contains(., 'Dismiss') or contains(., 'Cancel')]")
        for btn in close_btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                print("  🧹 成功清理屏幕遮挡弹窗", flush=True)
                human_sleep(0.5, 1.0)
    except Exception:
        pass


def extract_remaining_time(driver) -> str:
    """提取剩余时间文本"""
    dismiss_annoying_popups(driver)
    try:
        # 匹配包含 "Time remaining:" 的区块
        elem = driver.find_element(By.XPATH, "//*[contains(text(), 'Time remaining')]/.. | //*[contains(text(), 'Time remaining')]")
        text = elem.text.strip()
        match = re.search(r'Time remaining:\s*([^\n\r]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # 备选匹配直接出现的时间模式如 1d 19h
        time_match = re.search(r'(\d+\s*d\s*\d+\s*h)', text)
        if time_match:
            return time_match.group(1)
        return text.split("\n")[0]
    except Exception:
        return "未知"


def trigger_renew(driver):
    """强力触发续期及可能伴随的模态确认框"""
    dismiss_annoying_popups(driver)

    # 1. 定位 Renew 按钮
    renew_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Renew')]")
    if not renew_btns:
        print("  ⚠️ 未在页面找到 Renew 按钮", flush=True)
        return False

    renew_btn = renew_btns[0]
    print("  👉 找到 Renew 按钮，准备触发点击...", flush=True)

    # 尝试原生移动点击 + JS 强制点击双重保险
    try:
        ActionChains(driver).move_to_element(renew_btn).pause(0.3).click().perform()
    except Exception:
        pass
    driver.execute_script("arguments[0].click();", renew_btn)
    print("  ⚡ 已向 Renew 按钮派发点击事件", flush=True)
    human_sleep(2.0, 3.0)

    # 2. 检查是否有二次确认窗口 (Modal / SweetAlert / 对话框)
    confirm_keywords = ["confirm", "yes", "确定", "renew", "continue"]
    for kw in confirm_keywords:
        try:
            modals = driver.find_elements(By.XPATH, f"//div[contains(@role, 'dialog') or contains(@class, 'modal') or contains(@class, 'swal2')]//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw}')]")
            for c_btn in modals:
                if c_btn.is_displayed():
                    print(f"  🔔 检测到二次确认框并点击: [{c_btn.text}]", flush=True)
                    driver.execute_script("arguments[0].click();", c_btn)
                    human_sleep(2.0, 3.0)
                    break
        except Exception:
            pass

    return True


def main():
    print("=== ACLClouds 自动续期任务启动 ===", flush=True)
    if not ACL_EMAIL or not ACL_PASSWORD:
        print("❌ 未配置 ACL_EMAIL 或 ACL_PASSWORD Secrets", flush=True)
        return

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
        except Exception as e:
            print(f"⚠️ 代理启动失败: {e}", flush=True)

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 打开登录页面
        print(f"🌐 访问控制面板: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        human_sleep(3.0, 5.0)

        # 2. 登录处理
        if "/auth/login" in driver.current_url:
            print("  🔑 输入登录凭据...", flush=True)
            user_input = driver.wait_for_element_visible("input[name='username'], input[type='text'], input[type='email']", timeout=20)
            human_type(driver, user_input, ACL_EMAIL)

            pwd_input = driver.wait_for_element_visible("input[name='password'], input[type='password']", timeout=10)
            human_type(driver, pwd_input, ACL_PASSWORD)

            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Login') or contains(., 'Log In')]")
            driver.execute_script("arguments[0].click();", submit_btn)
            human_sleep(4.0, 6.0)

        # 3. 进入服务器控制台界面
        dismiss_annoying_popups(driver)
        # 如果还在首页，点击第一台服务器卡片进入控制台
        if "/server/" not in driver.current_url:
            server_cards = driver.find_elements(By.XPATH, "//a[contains(@href, '/server/')]")
            if server_cards:
                print("  🖥️ 从服务器列表进入实例控制台...", flush=True)
                driver.execute_script("arguments[0].click();", server_cards[0])
                human_sleep(4.0, 6.0)

        dismiss_annoying_popups(driver)

        # 4. 读取续期前时间
        before_time = extract_remaining_time(driver)
        print(f"⏳ 当前剩余到期时间: {before_time}", flush=True)

        # 5. 执行续期点击
        clicked = trigger_renew(driver)

        # 关键修正：给服务器后端充足的落库和结算时间（等待 6 秒后强制刷新）
        print("  ⏳ 等待后端接口处理并强制刷新界面...", flush=True)
        time.sleep(6)
        driver.refresh()
        human_sleep(4.0, 6.0)
        dismiss_annoying_popups(driver)

        # 6. 读取续期后时间
        after_time = extract_remaining_time(driver)
        print(f"📊 刷新后剩余到期时间: {after_time}", flush=True)

        # 7. 保存现场并推送 Telegram
        driver.save_screenshot("acl_final.png")
        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 判断续期结果
        if before_time != after_time and "未知" not in after_time:
            status_title = "🎉 <b>ACLClouds 续期成功！</b>"
        else:
            status_title = "📋 <b>ACLClouds 自动巡检报备</b>"

        tg_send(
            f"{status_title}\n\n"
            f"⌛ <b>到期变动：</b>剩余 <code>{before_time}</code> ➜ 剩余 <code>{after_time}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="acl_final.png"
        )
        print("🎉 任务完成并已推送状态。", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 运行发生异常: {err}", flush=True)
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
