import os
import re
import sys
import time
import requests
from seleniumbase import Driver
from selenium.webdriver.common.by import By

# ==================== 环境变量 ====================
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SERVER_URL = os.environ.get(
    "VOER_SERVER_URL",
    "https://voer.host/panel/server/84a3ea1a-c2b4-4798-ba20-a6b834ad7992"
).strip()

def send_telegram(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"⚠️ Telegram 发送失败: {e}")

def restore_session(driver, cookies_str: str):
    print("🔑 执行会话注入恢复...")
    if not cookies_str:
        print("❌ 错误: VOER_COOKIES 为空！")
        sys.exit(1)

    driver.get("https://voer.host/404")
    time.sleep(2)

    items = [item.strip() for item in cookies_str.split(";") if item.strip()]
    count = 0
    for item in items:
        if "=" in item:
            name, value = item.split("=", 1)
            name, value = name.strip(), value.strip()
            if name.lower() in ["domain", "path", "expires", "samesite", "secure", "httponly"]:
                continue
            try:
                driver.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": ".voer.host",
                    "path": "/"
                })
                count += 1
            except Exception:
                pass
    print(f"📦 Cookie 注入完成 ({count} 项)")

def trigger_start_if_offline(driver):
    try:
        page_text = driver.page_source
        is_offline = "OFFLINE" in page_text or "STOPPED" in page_text
        start_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Start')]")
        
        can_click_start = False
        target_btn = None
        for b in start_btns:
            if b.is_displayed() and b.is_enabled():
                can_click_start = True
                target_btn = b
                break

        if is_offline or can_click_start:
            print("🚨 检测到服务器离线或处于关机状态！")
            if target_btn:
                target_btn.click()
                print("✅ 已点击 Start 启动开机！")
                time.sleep(5)
                send_telegram("⚡ *VOER Host 自动唤醒*\n\n检测到服务器离线，已点击 Start 按钮开机！")
                return True
        else:
            print("⚡ 服务器状态正常（RUNNING）。")
    except Exception as e:
        print(f"⚠️ 状态检测跳过: {e}")
    return False

def get_dashboard_info(driver):
    time_remaining, extensions_today = "00:00:00", "未知"
    try:
        content = driver.page_source
        t_match = re.search(r"\b(\d{2}:\d{2}:\d{2})\b", content)
        if t_match:
            time_remaining = t_match.group(1)
        ext_match = re.search(r"Extensions today\s*(\d+/\d+)", content, re.IGNORECASE)
        if ext_match:
            extensions_today = ext_match.group(1)
    except Exception:
        pass
    return time_remaining, extensions_today

def handle_ad_and_claim(driver):
    print("📺 广告加载中，等待 35 秒倒计时...")
    time.sleep(35)

    close_xpaths = [
        "//button[contains(text(), 'Close')]",
        "//button[contains(text(), '关闭')]",
        "//*[@aria-label='Close ad']",
        "//*[@id='close-button']",
        "//*[contains(@class, 'close-button')]"
    ]

    closed = False
    for xp in close_xpaths:
        els = driver.find_elements(By.XPATH, xp)
        for el in els:
            if el.is_displayed():
                el.click()
                closed = True
                break
        if closed:
            break

    if not closed:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for f in frames:
            try:
                driver.switch_to.frame(f)
                for xp in close_xpaths:
                    els = driver.find_elements(By.XPATH, xp)
                    for el in els:
                        if el.is_displayed():
                            el.click()
                            closed = True
                            break
                    if closed:
                        break
                driver.switch_to.default_content()
                if closed:
                    break
            except Exception:
                driver.switch_to.default_content()

    if closed:
        print("🎯 广告已成功关闭！")
    else:
        print("⚠️ 未找到关闭按钮，跳过。")
    time.sleep(3)

def main():
    print("=== Python 任务初始化启动 ===")
    driver = Driver(browser="chrome", headless=True)

    try:
        restore_session(driver, VOER_COOKIES)

        print(f"🌐 打开控制台: {SERVER_URL} ...")
        driver.get(SERVER_URL)
        time.sleep(4)

        if "/login" in driver.current_url:
            print("❌ 会话失效，请更新 VOER_COOKIES")
            send_telegram("⚠️ *VOER 告警*: 会话失效，请更新 Cookie！")
            sys.exit(1)

        print("✅ 控制台访问成功！")
        trigger_start_if_offline(driver)

        t_rem, ext_prog = get_dashboard_info(driver)
        print(f"⏱️ 剩余时长: {t_rem} | 进度: {ext_prog}")

        extend_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Extend')]")
        target_extend = None
        for b in extend_btns:
            if b.is_displayed() and b.is_enabled():
                target_extend = b
                break

        if target_extend:
            print("🚀 检测到 '+ Extend' 可点击，开始观看广告续期...")
            target_extend.click()
            handle_ad_and_claim(driver)
            time.sleep(3)
            trigger_start_if_offline(driver)

            new_t, new_ext = get_dashboard_info(driver)
            msg = f"🎉 *VOER Host 续期成功*\n\n⏱️ 剩余时间：`{new_t}`\n📊 额度进度：`{new_ext}`"
            print(msg)
            send_telegram(msg)
        else:
            print("ℹ️ '+ Extend' 按钮不可用或额度已满。")

    finally:
        driver.quit()
        print("=== 任务正常执行完成 ===")

if __name__ == "__main__":
    main()
