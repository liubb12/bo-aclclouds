import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
import requests
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

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
    """精准挂载 Token"""
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
    """精准捕获倒计时与真实额度进度"""
    raw_time, ext_prog, total_seconds = "00:00:00", "未知", 0

    print("⏳ 等待控制台数据渲染...")
    for _ in range(20):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            if matches:
                m = matches[0]
                total_seconds = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if total_seconds > 0:
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"

            # 抓取真实续期进度
            prog_match = re.search(r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE)
            if prog_match:
                ext_prog = prog_match.group(1).replace(" ", "")

            if total_seconds > 0 and ext_prog != "未知":
                break
        except Exception:
            pass
        time.sleep(1)

    try:
        driver.save_screenshot("debug_dashboard.png")
    except Exception:
        pass

    return total_seconds, raw_time, ext_prog

def handle_ad_and_claim(driver):
    """穿透广告层并强力关闭"""
    print("📺 广告播放中，等待 35 秒结算奖励...")
    time.sleep(35)

    close_selectors = [
        "//button[contains(translate(text(), 'CLOSE', 'close'), 'close')]",
        "//button[contains(text(), '关闭')]",
        "//button[contains(text(), 'Claim')]",
        "//*[contains(@class, 'close')]"
    ]
    
    # 强制清理前端已知遮罩
    try:
        driver.execute_script("""
            var els = document.querySelectorAll('.bg-black\\\\/80, .backdrop-blur-sm, [class*="fixed inset-0"]');
            els.forEach(e => e.remove());
        """)
    except Exception:
        pass

    closed = False
    for xp in close_selectors:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    closed = True
                    break
        except Exception:
            pass
        if closed: break

    if not closed:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        except Exception:
            pass

def write_next_run(seconds_remaining: int, ext_prog: str):
    """动态 Cron 计算"""
    now_utc = datetime.now(timezone.utc)
    is_exhausted = False
    if "/" in ext_prog:
        cur, total = ext_prog.split("/", 1)
        if cur.strip() == total.strip() and cur.strip() != "0":
            is_exhausted = True
    
    if is_exhausted:
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
    rem_sec, raw_time, ext_prog, ad_rounds = 0, "00:00:00", "未知", 0

    try:
        restore_session(driver, VOER_COOKIES)
        driver.get(SERVER_URL)
        time.sleep(4)
        
        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        init_time = raw_time
        print(f"⏱️ 初始状态: 剩余 {init_time} | 进度: {ext_prog}")

        while True:
            if "/" in ext_prog:
                cur, total = ext_prog.split("/", 1)
                if cur.strip() == total.strip() and cur.strip() != "0":
                    print("🛑 额度已达每日上限，停止续期。")
                    break

            extend_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Extend') or contains(., 'Extend')]")
            if not extend_btns:
                break
                
            ad_rounds += 1
            print(f"🚀 [第 {ad_rounds} 轮] 强力触发 '+ Extend'")
            
            # 使用 JS 强制点击，无视任何遮罩拦截
            driver.execute_script("arguments[0].click();", extend_btns[0])
            
            handle_ad_and_claim(driver)
            
            # 杀手锏：每轮强制刷新页面，彻底清除广告 iframe 和残留黑屏遮罩
            print("🔄 刷新页面同步最新时间...")
            driver.refresh()
            time.sleep(5)

            rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
            print(f"⏱️ 第 {ad_rounds} 轮结束: 剩余 {raw_time} | 进度: {ext_prog}")

            if ad_rounds >= 4:
                break

        now_beijing = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        if ad_rounds > 0:
            msg = f"📋 *VOER Host 自动续期汇总*\n\n🎬 *观看广告*：{ad_rounds} 轮\n⌛ *到期变动*：`{init_time}` ➔ `{raw_time}`\n⏰ *执行时间*：`{now_beijing}`"
        else:
            msg = f"📋 *VOER Host 状态巡检*\n\n⏱️ *当前剩余*：`{raw_time}`\n📊 *额度进度*：`{ext_prog}`\n⏰ *执行时间*：`{now_beijing}`"

        print(msg)
        send_telegram(msg)

    finally:
        driver.quit()
        write_next_run(rem_sec, ext_prog)

if __name__ == "__main__":
    main()
