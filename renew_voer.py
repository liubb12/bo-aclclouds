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
    """精准提取并挂载包含 domain 和 path 的 Token"""
    print("🔑 执行会话注入恢复...")
    if not cookies_str:
        print("❌ 错误: VOER_COOKIES 为空！")
        sys.exit(1)

    # 1. 精准提取纯 JWT Token 字符串
    token_match = re.search(r"token=([^;\s]+)", cookies_str)
    if token_match:
        token_val = token_match.group(1).strip()
    else:
        token_val = cookies_str.split(";")[0].strip()

    # 2. 先访问根域以建立 Cookie 上下文
    driver.get("https://voer.host/")
    time.sleep(2)

    # 3. 注入带 .voer.host 根域的 Cookie
    try:
        driver.add_cookie({
            "name": "token",
            "value": token_val,
            "domain": ".voer.host",
            "path": "/"
        })
        print("📦 Cookie [token] (domain: .voer.host, path: /) 注入成功！")
    except Exception as e:
        print(f"⚠️ 指定 domain 失败，回退默认注入: {e}")
        try:
            driver.add_cookie({"name": "token", "value": token_val, "path": "/"})
        except Exception:
            pass

    # 4. 同步注入 LocalStorage 与 SessionStorage（SPA 前端框架关键鉴权点）
    try:
        driver.execute_script(f"""
            localStorage.setItem('token', '{token_val}');
            localStorage.setItem('auth_token', '{token_val}');
            sessionStorage.setItem('token', '{token_val}');
        """)
        print("💾 本地存储 (LocalStorage/SessionStorage) 同步挂载就绪！")
    except Exception as e:
        print(f"⚠️ 本地存储注入异常: {e}")

def trigger_start_if_offline(driver):
    """检测关机状态并点击 Start 唤醒"""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
        is_offline = "OFFLINE" in body_text or "STOPPED" in body_text
        start_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Start')]")
        
        target_btn = None
        for b in start_btns:
            if b.is_displayed() and b.is_enabled():
                target_btn = b
                break

        if is_offline or target_btn:
            print("🚨 检测到服务器离线或关机状态！")
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
    """通过整页文本捕获倒计时与进度，并保存排查截图"""
    raw_time = "00:00:00"
    ext_prog = "未知"
    total_seconds = 0

    print("⏳ 等待控制台数据动态渲染（最多 25 秒）...")
    for i in range(25):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text

            # 匹配 03:25:02 这类格式
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            for m in matches:
                sec = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if sec > 0:
                    total_seconds = sec
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"
                    break

            # 匹配 1/4 等字样
            prog_match = re.search(r"(\d+\s*/\s*\d+)", body_text)
            if prog_match:
                ext_prog = prog_match.group(1).replace(" ", "")

            if total_seconds > 0 and ext_prog != "未知":
                print(f"✨ 成功捕获真实数据 (第 {i+1} 秒): {raw_time} | 进度: {ext_prog}")
                break
        except Exception:
            pass

        time.sleep(1)

    try:
        driver.save_screenshot("debug_dashboard.png")
        print("📸 已保存调试截图: debug_dashboard.png")
    except Exception as e:
        print(f"⚠️ 截图保存失败: {e}")

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
    driver = Driver(browser="chrome", headless=True)
    driver.set_window_size(1920, 1080)

    rem_sec = 0
    raw_time = "00:00:00"
    ext_prog = "未知"

    try:
        # 执行增强注入
        restore_session(driver, VOER_COOKIES)

        print(f"🌐 打开控制台: {SERVER_URL} ...")
        driver.get(SERVER_URL)
        time.sleep(3)

        # 再次刷新以唤醒前端框架读取 LocalStorage/Cookie
        driver.refresh()
        time.sleep(3)

        if "/login" in driver.current_url:
            print("❌ 会话失效，请更新 VOER_COOKIES")
            send_telegram("⚠️ *VOER 告警*: 会话失效，请更新 Cookie！")
            sys.exit(1)

        print("✅ 控制台访问成功！")
        trigger_start_if_offline(driver)

        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        print(f"⏱️ 剩余时长: {raw_time} ({rem_sec}秒) | 进度: {ext_prog}")

        extend_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Extend') or contains(., 'Extend')]")
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

            rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
            msg = f"🎉 *VOER Host 续期成功*\n\n⏱️ 当前剩余时间：`{raw_time}`\n📊 额度进度：`{ext_prog}`"
            print(msg)
            send_telegram(msg)
        else:
            print("ℹ️ '+ Extend' 按钮不可用或今日额度已满。")
            send_telegram(f"📋 *VOER Host 状态巡检*\n\n⏱️ 当前剩余：`{raw_time}`\n📊 额度进度：`{ext_prog}`\n💡 当前无需加时或额度已满。")

    finally:
        driver.quit()
        write_next_run(rem_sec, ext_prog)
        print("=== 任务执行完毕 ===")

if __name__ == "__main__":
    main()
