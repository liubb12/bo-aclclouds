#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# ACLClouds 自动续期脚本 (WARP 通道 + 人机穿透 + 遮罩防御版)
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

WARP_SOCKS5_PORT = 10808
LOCAL_HTTP_PORT = 18082

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
ACL_USERNAME = os.environ.get("ACL_USERNAME", "").strip()
ACL_PASSWORD = os.environ.get("ACL_PASSWORD", "").strip()


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


def start_warp_gost() -> subprocess.Popen:
    """启动 gost 将 WARP 的 SOCKS5 (10808) 转为 Chrome 支持的 HTTP (18082)"""
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://127.0.0.1:{WARP_SOCKS5_PORT}"]
    print("  🚀 启动 gost 桥接本地 WARP 通道...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败。")
    print(f"  ✅ WARP HTTP 代理就绪: http://127.0.0.1:{LOCAL_HTTP_PORT}", flush=True)
    return proc


def click_turnstile_checkbox(driver, timeout=20):
    """穿透点击 Cloudflare Turnstile 验证框"""
    print("  🛡️ 检测 Cloudflare 验证盾并尝试点击...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
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
                        print("  🎯 成功切入 iframe 物理点击 Turnstile 复选框！", flush=True)
                    driver.switch_to.default_content()
                    return True
        except Exception:
            driver.switch_to.default_content()

        try:
            driver.uc_gui_click_cf()
            return True
        except Exception:
            pass

        time.sleep(2)
    return False


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
    """清理遮挡点击的弹窗（如 PWA 安装提示）"""
    try:
        close_btns = driver.find_elements(
            By.XPATH,
            "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' "
            "or contains(., 'Close') or contains(., 'Dismiss') or contains(., 'Cancel')]"
        )
        for btn in close_btns:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
                print("  🧹 成功清理屏幕遮挡弹窗", flush=True)
                human_sleep(0.5, 1.0)
    except Exception:
        pass


def extract_remaining_time(driver) -> str:
    """提取剩余到期时间"""
    dismiss_annoying_popups(driver)
    try:
        elem = driver.find_element(
            By.XPATH,
            "//*[contains(text(), 'Time remaining')]/.. | //*[contains(text(), 'Time remaining')]"
        )
        text = elem.text.strip()
        match = re.search(r'Time remaining:\s*([^\n\r]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        time_match = re.search(r'(\d+\s*d\s*\d+\s*h)', text)
        if time_match:
            return time_match.group(1)
        return text.split("\n")[0]
    except Exception:
        return "未知"


def trigger_renew(driver):
    """强力触发 Renew 按钮及二次确认模态框"""
    dismiss_annoying_popups(driver)

    renew_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Renew')]")
    if not renew_btns:
        print("  ⚠️ 未在页面找到 Renew 按钮", flush=True)
        return False

    renew_btn = renew_btns[0]
    print("  👉 找到 Renew 按钮，准备触发点击...", flush=True)

    try:
        ActionChains(driver).move_to_element(renew_btn).pause(0.3).click().perform()
    except Exception:
        pass
    driver.execute_script("arguments[0].click();", renew_btn)
    print("  ⚡ 已向 Renew 按钮派发点击事件", flush=True)
    human_sleep(2.0, 3.0)

    # 捕获并确认二次确认弹窗
    confirm_keywords = ["confirm", "yes", "确定", "renew", "continue"]
    for kw in confirm_keywords:
        try:
            modals = driver.find_elements(
                By.XPATH,
                f"//div[contains(@role, 'dialog') or contains(@class, 'modal') or contains(@class, 'swal2')]//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw}')]"
            )
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
    print("=== ACLClouds 自动续期任务启动 (WARP 增强版) ===", flush=True)
    if not ACL_USERNAME or not ACL_PASSWORD:
        print("❌ 未检测到登录凭据！请检查 ACL_USERNAME 与 ACL_PASSWORD", flush=True)
        return

    gost_proc = None
    try:
        gost_proc = start_warp_gost()
    except Exception as e:
        print(f"⚠️ gost 桥接失败: {e}", flush=True)

    uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 打开登录页面
        print(f"🌐 访问控制面板: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=8)
        human_sleep(3.0, 5.0)

        # 检查是否遇到 Cloudflare 质询盾
        click_turnstile_checkbox(driver, timeout=15)
        human_sleep(2.0, 3.0)

        # 2. 账号密码登录
        if "/auth/login" in driver.current_url:
            print("🔑 输入账号密码进行登录...", flush=True)
            user_input = driver.wait_for_element_visible(
                "input[name='username'], input[type='text'], input[type='email']", timeout=20
            )
            human_type(driver, user_input, ACL_USERNAME)

            pwd_input = driver.wait_for_element_visible(
                "input[name='password'], input[type='password']", timeout=10
            )
            human_type(driver, pwd_input, ACL_PASSWORD)

            submit_btn = driver.find_element(
                By.XPATH, "//button[@type='submit' or contains(., 'Login') or contains(., 'Log In')]"
            )
            driver.execute_script("arguments[0].click();", submit_btn)
            print("  🚀 已点击登录，等待跳转...", flush=True)
            human_sleep(5.0, 7.0)

            # 再次检查登录后可能弹出的 CF 验证盾
            click_turnstile_checkbox(driver, timeout=10)

        # 3. 进入服务器实例控制台
        dismiss_annoying_popups(driver)
        if "/server/" not in driver.current_url:
            server_cards = driver.find_elements(By.XPATH, "//a[contains(@href, '/server/')]")
            if server_cards:
                print("🖥️ 从列表进入服务器实例控制台...", flush=True)
                driver.execute_script("arguments[0].click();", server_cards[0])
                human_sleep(5.0, 7.0)

        dismiss_annoying_popups(driver)

        # 4. 获取续期前剩余时间
        before_time = extract_remaining_time(driver)
        print(f"⏳ 续期前剩余时间: {before_time}", flush=True)

        # 5. 执行续期点击
        trigger_renew(driver)

        # 6. 等待 6 秒后端结算，然后强制刷新页面
        print("⏳ 等待 6 秒后端结算，准备刷新界面...", flush=True)
        time.sleep(6)
        driver.refresh()
        human_sleep(4.0, 6.0)
        dismiss_annoying_popups(driver)

        # 7. 获取续期后剩余时间
        after_time = extract_remaining_time(driver)
        print(f"📊 刷新后剩余时间: {after_time}", flush=True)

        # 8. 保存控制台截图并推送到 Telegram
        driver.save_screenshot("acl_final.png")
        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

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
        print("🎉 任务完成并已推送状态到 Telegram。", flush=True)

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
