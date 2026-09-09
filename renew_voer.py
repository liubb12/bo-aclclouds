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
    "https://voer.host/panel/server/84a3ea1a-c2b4-4798-ba20-a6b834ad7992"
).strip()

def send_telegram(message: str):
    """发送 Telegram 消息通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过推送")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("📨 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 发送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram 发送异常: {e}")

def restore_session(driver, cookies_str: str):
    """注入 Cookie 会话"""
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
    """检测关机状态并点击 Start 唤醒"""
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
                send_telegram("⚡ *VOER Host 自动唤醒*\n\n检测到服务器离线，已自动点击 Start 开机！")
                return True
        else:
            print("⚡ 服务器状态正常（RUNNING）。")
    except Exception as e:
        print(f"⚠️ 唤醒检测跳过: {e}")
    return False

def wait_and_get_dashboard_info(driver):
    """显式轮询等待数据真实填充"""
    raw_time = "00:00:00"
    ext_prog = "未知"
    total_seconds = 0

    print("⏳ 等待控制台数据动态渲染（最多 25 秒）...")
    for i in range(25):
        try:
            # 优先从包含 Time Remaining 的区域查找时间文本
            time_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Time Remaining')]/following::*[contains(text(), ':')]")
            for el in time_elements:
                text = el.text.strip()
                m = re.match(r"^(\d{2}):(\d{2}):(\d{2})$", text)
                if m:
                    sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                    if sec > 0:
                        total_seconds = sec
                        raw_time = text
                        break

            # 提取 Extensions today 类似 1/4 的字样
            prog_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Extensions today')]")
            if prog_elements:
                parent_text = prog_elements[0].find_element(By.XPATH, "..").text
                m_prog = re.search(r"(\d+\s*/\s*\d+)", parent_text)
                if m_prog:
                    ext_prog = m_prog.group(1).replace(" ", "")

            # 抓取到了非零时间和进度，或者 Extend 按钮已经就绪
            extend_ready = len(driver.find_elements(By.XPATH, "//button[contains(., 'Extend')]")) > 0
            if total_seconds > 0 and ext_prog != "未知":
                print(f"✨ 控制台数据动态渲染完成！(第 {i+1} 秒)")
                break
        except Exception:
            pass

        time.sleep(1)

    return total_seconds, raw_time, ext_prog

def handle_ad_and_claim(driver):
    """穿透广告并关闭"""
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

def write_next_run(seconds_remaining: int, ext_prog: str):
    """计算下次执行时间并写入 output.log"""
    now_utc = datetime.now(timezone.utc)
    is_exhausted = "4/4" in ext_prog
    
    if is_exhausted:
        print("ℹ️ 今日 4/4 额度已用尽，推算明日 UTC 00:05 重新进场...")
        tomorrow_utc = (now_utc + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        next_run = tomorrow_utc
    else:
        # 有剩余时间则提前 20 分钟唤醒；否则按 3 小时兜底
        if seconds_remaining > 1200:
            target_delay = seconds_remaining - 1200
        else:
            target_delay = 10800  # 兜底 3 小时
            
        target_delay = max(target_delay, 900)   # 最少 15 分钟
        target_delay = min(target_delay, 12600) # 最多 3.5 小时
        next_run = now_utc + timedelta(seconds=target_delay)

    next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
    print(f"📌 计算得出下次执行时间 (UTC): {next_run_str}")
    
    with open("output.log", "a", encoding="utf-8") as f:
        f.write(f"NEXT_RUN_UTC={next_run_str}\n")

def main():
    print("=== Python 任务初始化启动 ===")
    # 强制将浏览器分辨率初始化为 1920x1080，防止按钮被折叠
    driver = Driver(browser="chrome", headless=True)
    driver.set_window_size(1920, 1080)

    rem_sec = 0
    raw_time = "00:00:00"
    ext_prog = "未知"

    try:
        restore_session(driver, VOER_COOKIES)

        print(f"🌐 打开控制台: {SERVER_URL} ...")
        driver.get(SERVER_URL)
        time.sleep(3)

        if "/login" in driver.current_url:
            print("❌ 会话失效，请更新 VOER_COOKIES")
            send_telegram("⚠️ *VOER 告警*: 会话失效，请更新 Cookie！")
            sys.exit(1)

        print("✅ 控制台访问成功！")
        trigger_start_if_offline(driver)

        # 显式等待并获取数据
        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        print(f"⏱️ 剩余时长: {raw_time} ({rem_sec}秒) | 进度: {ext_prog}")

        # 查找绿色的 + Extend 按钮
        extend_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Extend')]")
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

            # 续期成功后重新获取时间
            rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
            msg = f"🎉 *VOER Host 续期成功*\n\n⏱️ 当前剩余时间：`{raw_time}`\n📊 额度进度：`{ext_prog}`"
            print(msg)
            send_telegram(msg)
        else:
            print("ℹ️ '+ Extend' 按钮不可用或今日额度已满。")
            # 即使今日不可续期，也推送一条巡检状态让 Telegram 知晓当前情况
            send_telegram(f"📋 *VOER Host 状态巡检*\n\n⏱️ 当前剩余：`{raw_time}`\n📊 额度进度：`{ext_prog}`\n💡 当前无需加时或额度已满。")

    finally:
        driver.quit()
        write_next_run(rem_sec, ext_prog)
        print("=== 任务执行完毕 ===")

if __name__ == "__main__":
    main()
