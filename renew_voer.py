import os
import re
import sys
import time
import requests
from playwright.sync_api import sync_playwright

# ==================== 环境变量 ====================
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
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"⚠️ Telegram 发送失败: {e}")

def restore_session(context, cookies_str: str):
    """注入 Cookie 会话"""
    print("🔑 执行会话注入恢复...")
    if not cookies_str:
        print("❌ 错误: VOER_COOKIES 为空！")
        sys.exit(1)

    cookies_list = []
    items = [item.strip() for item in cookies_str.split(";") if item.strip()]
    for item in items:
        if "=" in item:
            name, value = item.split("=", 1)
            name, value = name.strip(), value.strip()
            if name.lower() in ["domain", "path", "expires", "samesite", "secure", "httponly"]:
                continue
            cookies_list.append({
                "name": name,
                "value": value,
                "domain": ".voer.host",
                "path": "/"
            })

    if cookies_list:
        context.add_cookies(cookies_list)
        print(f"📦 Cookie 注入完成 ({len(cookies_list)} 项)")

def get_dashboard_info(page):
    """读取面板倒计时和今日续期进度"""
    time_remaining = "00:00:00"
    extensions_today = "未知"
    try:
        content = page.content()
        time_match = re.search(r"\b(\d{2}:\d{2}:\d{2})\b", content)
        if time_match:
            time_remaining = time_match.group(1)

        ext_match = re.search(r"Extensions today\s*(\d+/\d+)", content, re.IGNORECASE)
        if ext_match:
            extensions_today = ext_match.group(1)
    except Exception:
        pass
    return time_remaining, extensions_today

def handle_ad_and_claim(page):
    """穿透多层 iframe 观看广告并点击 Close 关闭"""
    print("📺 检测到广告触发，等待广告完全加载...")
    page.wait_for_timeout(4000)

    try:
        mute_btn = page.query_selector("button:has-text('Unmute'), button:has-text('Mute')")
        if mute_btn and mute_btn.is_visible():
            mute_btn.click()
    except Exception:
        pass

    print("⏳ 等待广告播放完毕（35 秒）...")
    time.sleep(35)

    close_selectors = [
        "button:has-text('Close')",
        "button:has-text('关闭')",
        "div[aria-label='Close ad']",
        "#close-button",
        ".close-button"
    ]

    closed = False
    for sel in close_selectors:
        btn = page.query_selector(sel)
        if btn and btn.is_visible():
            btn.click()
            closed = True
            break

    if not closed:
        for frame in page.frames:
            for sel in close_selectors:
                try:
                    f_btn = frame.query_selector(sel)
                    if f_btn and f_btn.is_visible():
                        f_btn.click()
                        closed = True
                        break
                except Exception:
                    continue
            if closed:
                break

    if closed:
        print("🎯 广告已成功关闭！")
        page.wait_for_timeout(3000)
    else:
        print("⚠️ 未找到显式 Close 按钮，按 ESC 键兜底")
        page.keyboard.press("Escape")
        page.wait_for_timeout(2000)

def trigger_start_if_offline(page):
    """核心唤醒逻辑：判断是否关机（OFFLINE 或 Start 可点），并执行拉起"""
    try:
        # 1. 检查页面是否有 OFFLINE 标识，或者 Start 按钮是否已被激活（enabled）
        is_offline_badge = page.query_selector("text='OFFLINE'") or page.query_selector("text='STOPPED'")
        start_btn = page.query_selector("button:has-text('Start')")

        can_click_start = start_btn and start_btn.is_visible() and start_btn.is_enabled()

        if is_offline_badge or can_click_start:
            print("🚨 检测到服务器当前处于停机状态（自然耗尽或已关闭）！")
            if start_btn and start_btn.is_enabled():
                print("⚡ 正在点击顶部 Start 按钮执行唤醒...")
                start_btn.click()
                page.wait_for_timeout(3000)

                # 判断点击 Start 后是否直接弹出了广告要求先加时
                if page.query_selector("iframe") or page.query_selector("div[role='dialog']"):
                    print("💡 点击 Start 后弹出了广告/续期弹窗，正在自动完成观看...")
                    handle_ad_and_claim(page)

                print("✅ 唤醒指令已下发！等待状态刷新...")
                page.wait_for_timeout(5000)
                send_telegram("⚡ *VOER Host 自动唤醒*\n\n检测到服务器处于关机状态，已成功触发 Start 开机唤醒！")
                return True
        else:
            print("⚡ 服务器状态正常（RUNNING 状态，Start 处于禁用置灰态）。")
            return False
    except Exception as e:
        print(f"⚠️ 唤醒检测异常（跳过）: {e}")
        return False

def main():
    print("=== Python 任务初始化启动 ===")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        )

        restore_session(context, VOER_COOKIES)
        page = context.new_page()

        print(f"🌐 正在打开控制台: {SERVER_URL} ...")
        page.goto(SERVER_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        if "/login" in page.url:
            print("❌ 会话失效，请更新 VOER_COOKIES")
            send_telegram("⚠️ *VOER 告警*: 会话已失效，请更新 Cookie！")
            browser.close()
            sys.exit(1)

        print("✅ 控制台访问成功！")

        # ---------------- 步骤 1：停机即刻唤醒 ----------------
        # 若之前时间自然耗尽关机了，进门先尝试拉起
        trigger_start_if_offline(page)

        # ---------------- 步骤 2：检查面板信息与加时 ----------------
        t_rem, ext_prog = get_dashboard_info(page)
        print(f"⏱️ 剩余时长: {t_rem} | 今日进度: {ext_prog}")

        # 检查绿色的 '+ Extend' 按钮
        extend_btn = page.query_selector("button:has-text('Extend'), button:has-text('+ Extend')")

        if extend_btn and extend_btn.is_visible() and extend_btn.is_enabled():
            print("🚀 检测到 '+ Extend' 按钮可点击，开始观看广告续期 4 小时...")
            extend_btn.click()
            handle_ad_and_claim(page)

            # 加完时间后，再次确认一下服务器是否顺带开机了；若还是停机，再点一次 Start
            page.wait_for_timeout(3000)
            trigger_start_if_offline(page)

            new_t, new_ext = get_dashboard_info(page)
            msg = f"🎉 *VOER Host 续期成功*\n\n⏱️ 当前剩余时间：`{new_t}`\n📊 额度进度：`{new_ext}`\n💡 服务器电源状态已复核拉起。"
            print(msg)
            send_telegram(msg)
        else:
            print("ℹ️ 当前无可用的 '+ Extend' 按钮（可能今日 4/4 已满或冷却中）。")

        browser.close()
        print("=== 任务执行完毕 ===")

if __name__ == "__main__":
    main()
