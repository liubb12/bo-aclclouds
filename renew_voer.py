import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import requests
from seleniumbase import Driver
from selenium.webdriver.common.by import By

# ==================== 环境变量配置 ====================
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SERVER_URL = os.environ.get(
    "VOER_SERVER_URL",
    "https://voer.host/panel/server/84a3ea1a-c2b4-4798-ba20-a6b83a4d7992"
).strip()

def send_telegram(message: str):
    """发送 Telegram 消息通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过推送")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
        print("📨 Telegram 通知发送成功！")
    except Exception as e:
        print(f"⚠️ Telegram 发送异常: {e}")

def restore_session(driver, cookies_str: str):
    """注入 Token"""
    print("🔑 执行会话注入恢复...")
    if not cookies_str:
        print("❌ 错误: VOER_COOKIES 为空！")
        sys.exit(1)

    token_match = re.search(r"token=([^;\s]+)", cookies_str)
    token_val = token_match.group(1).strip() if token_match else cookies_str.split(";")[0].strip()

    driver.get("https://voer.host/")
    time.sleep(2)

    try:
        driver.add_cookie({"name": "token", "value": token_val, "domain": ".voer.host", "path": "/"})
    except Exception:
        pass

    try:
        driver.execute_script(f"""
            localStorage.setItem('token', '{token_val}');
            localStorage.setItem('auth_token', '{token_val}');
            sessionStorage.setItem('token', '{token_val}');
        """)
        print("💾 会话凭证挂载就绪！")
    except Exception:
        pass

def wait_and_get_dashboard_info(driver):
    """获取当前剩余时间与今日额度"""
    raw_time, ext_prog, total_seconds = "00:00:00", "未知", 0
    for _ in range(15):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            if matches:
                m = matches[0]
                total_seconds = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if total_seconds > 0:
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"

            prog_match = re.search(r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE)
            if prog_match:
                ext_prog = prog_match.group(1).replace(" ", "")

            if total_seconds > 0 and ext_prog != "未知":
                break
        except Exception:
            pass
        time.sleep(1)
    return total_seconds, raw_time, ext_prog

def click_element_js(driver, xpath_list, timeout=10):
    """安全轮询并使用 JS 触发点击"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        for xp in xpath_list:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        return True
            except Exception:
                pass
        time.sleep(1)
    return False

def close_reward_ad(driver):
    """精准关闭播放完毕的视频广告（图4）"""
    print("📺 视频广告播放中，等待 35 秒倒计时...")
    time.sleep(35)

    close_selectors = [
        "//*[text()='Close']",
        "//button[contains(text(), 'Close')]",
        "//*[@id='close-button']",
        "//*[contains(@class, 'close')]"
    ]

    # 1. 顶层寻找
    if click_element_js(driver, close_selectors, timeout=5):
        print("🎯 已关闭广告（顶层）！")
        return True

    # 2. iframe 内部穿透查找
    frames = driver.find_elements(By.TAG_NAME, "iframe")
    for f in frames:
        try:
            driver.switch_to.frame(f)
            if click_element_js(driver, close_selectors, timeout=3):
                driver.switch_to.default_content()
                print("🎯 已关闭广告（iframe 内部）！")
                return True
            driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

    print("⚠️ 未抓到精准 Close，尝试点击弹窗边缘")
    return False

def run_single_extension(driver):
    """严格执行图 1 -> 图 2 -> 图 3(循环3次) -> 图 4 的完整流程"""
    # 步骤 1: 点击面板主页面的 "+ Extend"
    print("👉 [步骤 1] 点击主面板 '+ Extend' 按钮...")
    if not click_element_js(driver, ["//button[contains(., 'Extend')]", "//button[contains(text(), 'Extend')]"]):
        print("❌ 无法点击 '+ Extend'，可能额度已满或未加载")
        return False
    time.sleep(2)

    # 步骤 2: 点击弹窗中的 "Watch Ads"（图2）
    print("👉 [步骤 2] 点击 'Extend Session' 弹窗中的 'Watch Ads'...")
    if not click_element_js(driver, ["//button[contains(., 'Watch Ads')]", "//button[contains(text(), 'Watch Ads')]"]):
        print("❌ 无法点击 'Watch Ads'")
        return False
    time.sleep(3)

    # 步骤 3 & 4: 循环看满 3 轮广告（图3、图4）
    ad_success_count = 0
    for round_idx in range(1, 4):
        print(f"🎬 [广告 {round_idx}/3] 等待并点击 'Watch ad'...")
        time.sleep(2)
        
        # 点击中间绿色的 "Watch ad"
        clicked_watch = click_element_js(
            driver,
            ["//button[contains(text(), 'Watch ad')]", "//button[contains(., 'Watch ad')]"],
            timeout=15
        )
        if not clicked_watch:
            print(f"⚠️ 第 {round_idx} 次未找到 'Watch ad' 按钮，提前退出")
            break

        # 进入图4观看广告并关闭
        close_reward_ad(driver)
        ad_success_count += 1
        time.sleep(3)

    print(f"🎉 本次续期 3 轮广告完成 (成功推进 {ad_success_count} 次)")
    time.sleep(5)
    return True

def write_next_run(seconds_remaining: int, ext_prog: str):
    """计算下次执行时间"""
    now_utc = datetime.now(timezone.utc)
    is_exhausted = False
    if "/" in ext_prog:
        cur, total = ext_prog.split("/", 1)
        if cur.strip() == total.strip() and cur.strip() != "0":
            is_exhausted = True

    if is_exhausted:
        print("ℹ️ 今日额度已满，定于明日 UTC 00:05 唤醒...")
        next_run = (now_utc + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    else:
        target_delay = max(900, min(12600, seconds_remaining - 1200 if seconds_remaining > 1200 else 10800))
        next_run = now_utc + timedelta(seconds=target_delay)

    next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
    with open("output.log", "a", encoding="utf-8") as f:
        f.write(f"NEXT_RUN_UTC={next_run_str}\n")

def main():
    driver = Driver(browser="chrome", headless=True)
    driver.set_window_size(1920, 1080)
    rem_sec, raw_time, ext_prog = 0, "00:00:00", "未知"

    try:
        restore_session(driver, VOER_COOKIES)
        print(f"🌐 打开控制台: {SERVER_URL} ...")
        driver.get(SERVER_URL)
        time.sleep(4)

        # 关掉 Cookie 遮挡
        click_element_js(driver, ["//button[contains(text(), 'Accept')]"], timeout=3)

        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        init_time = raw_time
        print(f"⏱️ 初始状态: 剩余 {init_time} | 今日额度: {ext_prog}")

        # 启动完整续期序列
        success = run_single_extension(driver)

        # 刷新页面拉取最新结算结果
        driver.refresh()
        time.sleep(5)

        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        print(f"⏱️ 结算后状态: 剩余 {raw_time} | 今日额度: {ext_prog}")

        now_beijing = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        if success:
            msg = (
                f"📋 *VOER Host 自动续期汇总*\n\n"
                f"🎬 *观看广告*：3/3 轮 (满载续期)\n"
                f"⌛ *到期变动*：剩余 `{init_time}` ➔ 剩余 `{raw_time}`\n"
                f"⏰ *执行时间*：`{now_beijing}`"
            )
        else:
            msg = (
                f"📋 *VOER Host 状态巡检*\n\n"
                f"⏱️ *当前剩余*：`{raw_time}`\n"
                f"📊 *额度进度*：`{ext_prog}`\n"
                f"💡 无需加时或已达上限。\n"
                f"⏰ *执行时间*：`{now_beijing}`"
            )

        print(msg)
        send_telegram(msg)

    finally:
        driver.quit()
        write_next_run(rem_sec, ext_prog)

if __name__ == "__main__":
    main()
