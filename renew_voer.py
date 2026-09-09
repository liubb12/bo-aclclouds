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
        print("📦 Cookie [token] (domain: .voer.host, path: /) 注入成功！")
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
                print("🍪 已自动接受并关闭 Cookie 授权弹窗。")
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
            print("🚨 检测到服务器处于离线/关机状态！")
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
    """通过整页文本捕获倒计时与进度"""
    raw_time = "00:00:00"
    ext_prog = "未知"
    total_seconds = 0

    print("⏳ 等待控制台数据动态渲染（最多 25 秒）...")
    for i in range(25):
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text

            # 匹配 03:25:02 这类时间
            matches = re.findall(r"\b(\d{2}):(\d{2}):(\d{2})\b", body_text)
            for m in matches:
                sec = int(m[0]) * 3600 + int(m[1]) * 60 + int(m[2])
                if sec > 0:
                    total_seconds = sec
                    raw_time = f"{m[0]}:{m[1]}:{m[2]}"
                    break

            # 匹配包含换行或空格的 X/20 或 X/4 额度进度，如 '0\n/20'
            prog_match = re.search(r"(\d+)\s*/\s*(\d+)", body_text)
            if prog_match:
                ext_prog = f"{prog_match.group(1)}/{prog_match.group(2)}"

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
    """广告等待并穿透关闭"""
    print("📺 广告播放中，等待 35 秒让奖励生效...")
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

    # 1. 顶层页面查找
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

    # 2. 穿透所有 iframe 内部查找
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

    # 3. 兜底策略：发送 ESC 键触发弹窗关闭
    if not closed:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            print("⌨️ 发送 ESC 兜底关闭弹窗")
        except Exception:
            pass
    else:
        print("🎯 广告弹窗已成功关闭！")

    time.sleep(3)

def write_next_run(seconds_remaining: int, ext_prog: str):
    """计算下次执行时间并写入 output.log"""
    now_utc = datetime.now(timezone.utc)
    is_exhausted = False
    if "/" in ext_prog:
        cur, total = ext_prog.split("/", 1)
        if cur.strip() == total.strip() and cur.strip() != "0":
            is_exhausted = True
    
    if is_exhausted:
        print("ℹ️ 今日额度已用尽，推算明日 UTC 00:05 重新进场...")
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
    ext_prog = "未知"

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
            msg = f"🎉 *VOER Host 续期完成*\n\n⏱️ 当前剩余时间：`{raw_time}`\n📊 额度进度：`{ext_prog}`"
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
