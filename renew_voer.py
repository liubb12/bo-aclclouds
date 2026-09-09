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
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            print("📨 Telegram 通知发送成功！")
        else:
            print(f"⚠️ Telegram 发送失败: {resp.text}")
    except Exception as e:
        print(f"⚠️ Telegram 发送异常: {e}")

def restore_session(driver, cookies_str: str):
    """精准挂载 Token 到 Cookie 与 LocalStorage"""
    print("🔑 执行会话注入恢复...")
    if not cookies_str:
        print("❌ 错误: VOER_COOKIES 为空！")
        sys.exit(1)

    token_match = re.search(r"token=([^;\s]+)", cookies_str)
    if token_match:
        token_val = token_match.group(1).strip()
    else:
        token_val = cookies_str.split(";")[0].strip()

    driver.get("https://voer.host/")
    time.sleep(2)

    try:
        driver.add_cookie({
            "name": "token",
            "value": token_val,
            "domain": ".voer.host",
            "path": "/"
        })
        print("📦 Cookie [token] 注入成功！")
    except Exception:
        try:
            driver.add_cookie({"name": "token", "value": token_val, "path": "/"})
        except Exception:
            pass

    try:
        driver.execute_script(f"""
            localStorage.setItem('token', '{token_val}');
            localStorage.setItem('auth_token', '{token_val}');
            sessionStorage.setItem('token', '{token_val}');
        """)
        print("💾 本地存储 (LocalStorage/SessionStorage) 同步挂载就绪！")
    except Exception as e:
        print(f"⚠️ 本地存储注入异常: {e}")

def dismiss_cookie_banner(driver):
    """自动关闭右下角的 Cookie 授权弹窗"""
    try:
        accept_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Accept')]")
        for btn in accept_btns:
            if btn.is_displayed():
                btn.click()
                print("🍪 已自动关闭 Cookie 弹窗。")
                time.sleep(1)
                break
    except Exception:
        pass

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
            print("🚨 检测到服务器离线，执行唤醒...")
            if target_btn:
                target_btn.click()
                print("✅ 已点击 Start 开机！")
                time.sleep(5)
                send_telegram("⚡ *VOER Host 自动唤醒*\n\n检测到服务器离线，已自动点击 Start 开机！")
                return True
        else:
            print("⚡ 服务器状态正常 (RUNNING)。")
    except Exception as e:
        print(f"⚠️ 唤醒检测跳过: {e}")
    return False

def wait_and_get_dashboard_info(driver):
    """精准捕获 Time Remaining 与 Extensions today 进度"""
    raw_time = "00:00:00"
    ext_prog = "未知"
    total_seconds = 0

    print("⏳ 等待控制台数据动态渲染（最多 20 秒）...")
    for i in range(20):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text

            # 1. 匹配 03:57:46 这类倒计时
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            for m in matches:
                sec = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if sec > 0:
                    total_seconds = sec
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"
                    break

            # 2. 精准匹配 Extensions today 下面的 1/4 或 4/4（规避 0/20 玩家数）
            prog_match = re.search(r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE)
            if prog_match:
                ext_prog = prog_match.group(1).replace(" ", "")
            else:
                # 兜底匹配任意除 20 以外的进度或尾部进度
                all_progs = re.findall(r"(\d+\s*/\s*[1-9]\b)", body_text)
                if all_progs:
                    ext_prog = all_progs[0].replace(" ", "")

            if total_seconds > 0 and ext_prog != "未知":
                print(f"✨ 成功捕获真实数据 (第 {i+1} 秒): {raw_time} | 额度: {ext_prog}")
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
    """广告播放并关闭"""
    print("📺 广告播放中，等待 35 秒结算奖励...")
    time.sleep(35)

    close_selectors = [
        "//button[contains(translate(text(), 'CLOSE', 'close'), 'close')]",
        "//button[contains(text(), '关闭')]",
        "//button[contains(text(), 'Claim')]",
        "//button[contains(text(), 'Done')]",
        "//*[@aria-label='Close ad']",
        "//*[@aria-label='Close']",
        "//*[contains(@class, 'btn-close') or contains(@class, 'close-btn') or contains(@class, 'close-button')]",
        "//svg[contains(@class, 'close')]/..",
        "//div[contains(@class, 'modal')]//button"
    ]

    closed = False
    for xp in close_selectors:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                if el.is_displayed() and el.is_enabled():
                    driver.execute_script("arguments[0].click();", el)
                    closed = True
                    break
            if closed:
                break
        except Exception:
            pass

    if not closed:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for f in frames:
            try:
                driver.switch_to.frame(f)
                for xp in close_selectors:
                    els = driver.find_elements(By.XPATH, xp)
                    for el in els:
                        if el.is_displayed() and el.is_enabled():
                            driver.execute_script("arguments[0].click();", el)
                            closed = True
                            break
                    if closed:
                        break
                driver.switch_to.default_content()
                if closed:
                    break
            except Exception:
                driver.switch_to.default_content()

    if not closed:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            print("⌨️ 发送 ESC 键兜底关闭弹窗")
        except Exception:
            pass
    else:
        print("🎯 广告弹窗关闭成功！")

    time.sleep(3)

def write_next_run(seconds_remaining: int, ext_prog: str):
    """计算下次执行时间并写入 output.log"""
    now_utc = datetime.now(timezone.utc)
    
    # 判断是否满载 (例如 4/4)
    is_exhausted = False
    if "/" in ext_prog:
        cur, total = ext_prog.split("/", 1)
        if cur.strip() == total.strip() and cur.strip() != "0":
            is_exhausted = True
    
    if is_exhausted:
        print("ℹ️ 今日额度已打满，推算明日 UTC 00:05 重新进场...")
        tomorrow_utc = (now_utc + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        next_run = tomorrow_utc
    else:
        # 距离到期前 20 分钟唤醒
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
    init_time = "00:00:00"
    ext_prog = "未知"
    ad_rounds = 0

    try:
        restore_session(driver, VOER_COOKIES)

        print(f"🌐 打开服务器控制台: {SERVER_URL} ...")
        driver.get(SERVER_URL)
        time.sleep(4)

        dismiss_cookie_banner(driver)

        if "/login" in driver.current_url:
            print("❌ 会话失效，请更新 VOER_COOKIES")
            send_telegram("⚠️ *VOER 告警*: 会话失效，请更新 Cookie！")
            sys.exit(1)

        print("✅ 控制台访问成功！")
        trigger_start_if_offline(driver)

        # 初始状态捕获
        rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
        init_time = raw_time
        print(f"⏱️ 初始状态: 剩余 {init_time} | 今日进度: {ext_prog}")

        # 循环续期：只要按钮可点且今日额度没满，就连续看广告拉满
        while True:
            # 检查额度是否已达上限
            if "/" in ext_prog:
                cur, total = ext_prog.split("/", 1)
                if cur.strip() == total.strip() and cur.strip() != "0":
                    print(f"🛑 额度已达每日上限 ({ext_prog})，停止继续续期。")
                    break

            extend_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Extend') or contains(., 'Extend')]")
            target_extend = None
            for b in extend_btns:
                if b.is_displayed() and b.is_enabled():
                    target_extend = b
                    break

            if not target_extend:
                print("ℹ️ '+ Extend' 按钮不可用或进入冷却。")
                break

            ad_rounds += 1
            print(f"🚀 [第 {ad_rounds} 轮] 触发 '+ Extend'，开始观看广告...")
            target_extend.click()
            handle_ad_and_claim(driver)
            time.sleep(3)
            trigger_start_if_offline(driver)

            # 更新最新时间与进度
            rem_sec, raw_time, ext_prog = wait_and_get_dashboard_info(driver)
            print(f"⏱️ 第 {ad_rounds} 轮完成: 当前剩余 {raw_time} | 进度: {ext_prog}")

            # 安全防卡死：单次最多看 4 轮
            if ad_rounds >= 4:
                break

        # 统一汇总消息模板
        now_beijing = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        if ad_rounds > 0:
            msg = (
                f"📋 *VOER Host 自动续期汇总*\n\n"
                f"🎬 *观看广告*：{ad_rounds} 轮 (额度: {ext_prog})\n"
                f"⌛ *到期变动*：剩余 `{init_time}` ➔ 剩余 `{raw_time}`\n"
                f"⏰ *执行时间*：`{now_beijing}`"
            )
        else:
            msg = (
                f"📋 *VOER Host 状态巡检*\n\n"
                f"⏱️ *当前剩余*：`{raw_time}`\n"
                f"📊 *额度进度*：`{ext_prog}`\n"
                f"💡 当前无需加时或额度已满。\n"
                f"⏰ *执行时间*：`{now_beijing}`"
            )

        print(msg)
        send_telegram(msg)

    finally:
        driver.quit()
        write_next_run(rem_sec, ext_prog)
        print("=== 任务执行完毕 ===")

if __name__ == "__main__":
    main()
