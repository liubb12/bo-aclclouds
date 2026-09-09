#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 自动续期脚本 (UC 底层物理驱动 + 激励广告闭环版)
# ============================================================
import os
import re
import json
import html
import time
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By

BASE_URL = "https://voer.host"
SERVER_ID = "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992"
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知。")
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
            print("  ✅ TG 通知发送成功")
        else:
            print(f"  ⚠️ TG 通知发送失败: {resp.text}")
    except Exception as e:
        print(f"  ⚠️ TG 通知异常: {e}")


def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_value.startswith(prefix):
            proxy_value = proxy_value[len(prefix):]
            break
    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误，应为 host:port 或 user:pass@host:port。")
    return proxy_value


def wait_http_proxy_ready(port: int, timeout: int = 15):
    proxies = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    last_error = None
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=8)
            if resp.ok:
                print("  ✅ 本地 HTTP 代理连通性测试成功")
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}")
    return proc


def dismiss_pwa_popups(driver):
    try:
        btns = driver.find_elements(
            By.XPATH,
            "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' "
            "or contains(., 'Close') or contains(., 'Dismiss') or contains(., 'Accept')]"
        )
        for b in btns:
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                time.sleep(0.5)
    except Exception:
        pass


def restore_session_data(driver, credential_str: str, domain=".voer.host"):
    print("  📦 正在解析并恢复浏览器会话数据...", flush=True)
    cookie_str = credential_str
    storage_dict = {}
    try:
        data = json.loads(credential_str)
        if isinstance(data, dict):
            cookie_str = data.get("cookies", "")
            storage_dict = data.get("storage", {})
    except Exception:
        pass

    if cookie_str:
        parts = [c.strip() for c in cookie_str.split(";") if c.strip()]
        for part in parts:
            if "=" in part:
                name, val = part.split("=", 1)
                try:
                    driver.add_cookie({
                        "name": name.strip(),
                        "value": val.strip(),
                        "domain": domain,
                        "path": "/"
                    })
                except Exception:
                    try:
                        driver.add_cookie({"name": name.strip(), "value": val.strip(), "path": "/"})
                    except Exception:
                        pass
        print("  🍪 Cookie 注入完成", flush=True)

    if storage_dict and isinstance(storage_dict, dict):
        for k, v in storage_dict.items():
            driver.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", k, str(v))
        print("  💾 LocalStorage 恢复完成", flush=True)


def get_expire_info(driver) -> str:
    dismiss_pwa_popups(driver)
    try:
        elems = driver.find_elements(By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]")
        for elem in elems:
            txt = elem.text.strip()
            if re.match(r'^\d{1,2}:\d{2}:\d{2}$', txt):
                return f"剩余 {txt}"

        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(r'(?i)(?:Time Remaining|remaining|expire)[\s:]*([0-9]+:[0-9]+:[0-9]+)', body_text)
        if time_match:
            return f"剩余 {time_match.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取时间异常: {e}")
    return "未知"


def wait_and_click_ad_close(driver, max_wait_sec=40):
    """跨 DOM 和 iframe 物理点击广告 Close 按钮"""
    print(f"  ⏳ 等待广告展示结束并出现 Close (最长 {max_wait_sec} 秒)...", flush=True)
    start_time = time.time()
    time.sleep(10)

    while time.time() - start_time < max_wait_sec:
        # 1. 主页面层
        try:
            close_btns = driver.find_elements(
                By.XPATH,
                "//*[text()='Close' or translate(text(), 'CLOSE', 'close')='close' or contains(text(), 'Close') or @id='dismiss-button']"
            )
            for btn in close_btns:
                if btn.is_displayed():
                    print("  👉 在主 DOM 发现 Close 按钮，执行 UC 真实点击...", flush=True)
                    try:
                        driver.uc_click(btn)
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    return True
        except Exception:
            pass

        # 2. iframe 内部
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for frame in iframes:
                try:
                    driver.switch_to.frame(frame)
                    sub_close = driver.find_elements(
                        By.XPATH,
                        "//*[text()='Close' or translate(text(), 'CLOSE', 'close')='close'] | "
                        "//div[@id='dismiss-button'] | //*[@id='close-button']"
                    )
                    for btn in sub_close:
                        if btn.is_displayed():
                            print("  👉 在广告 iframe 内部发现 Close 按钮，执行点击...", flush=True)
                            try:
                                driver.uc_click(btn)
                            except Exception:
                                driver.execute_script("arguments[0].click();", btn)
                            driver.switch_to.default_content()
                            time.sleep(2)
                            return True
                    driver.switch_to.default_content()
                except Exception:
                    driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

        time.sleep(2)

    print("  ℹ️ 广告播放轮候完毕", flush=True)
    return True


def main():
    print("=== Python 任务初始化启动 ===", flush=True)

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理检测正常，已启用中转。")
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，将尝试直连。")

    chromium_args = [
        "--disable-heavy-ad-intervention",
        "--disable-features=HeavyAdIntervention,HeavyAdInterventionWarning",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1920,1080"
    ]
    driver = Driver(uc=True, headless=False, proxy=uc_proxy, chromium_arg=" ".join(chromium_args))

    try:
        # 1. 会话恢复直登
        print("🔑 执行会话注入恢复...", flush=True)
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
        time.sleep(2)
        restore_session_data(driver, VOER_COOKIES)
        time.sleep(1)

        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(6)
        dismiss_pwa_popups(driver)

        if "/login" in driver.current_url.lower():
            print("❌ 会话已失效，请重新更新 VOER_COOKIES", flush=True)
            return

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_info_before}", flush=True)

        # 2. 点击 [+ Extend] 按钮
        extend_selector = "//button[contains(., 'Extend')]"
        driver.wait_for_element_visible(extend_selector, timeout=20)
        print("👉 使用 UC 物理引擎真实点击 [+ Extend] 按钮...", flush=True)
        driver.uc_click(extend_selector)
        time.sleep(2.5)

        # 3. 点击第一个小弹窗里的确认按钮 [Watch Ads]
        confirm_selector = "//button[contains(., 'Watch Ads') or contains(., 'Watch ad')]"
        driver.wait_for_element_visible(confirm_selector, timeout=15)
        print("👉 使用 UC 物理引擎点击 [Watch Ads] 进入看广告模态框...", flush=True)
        driver.uc_click(confirm_selector)
        time.sleep(4)

        # 截图验证是否成功进入了黑色全屏模态框
        driver.save_screenshot("entered_ads_modal.png")

        # 4. 模态框广告流循环 (1/3 -> 2/3 -> 3/3)
        completed = 0
        for current_ad in range(1, 4):
            print(f"\n🎬 === 正在准备第 {current_ad}/3 个广告 ===", flush=True)

            watch_btn_selector = None
            # 持续等待绿色卡片里的 [Watch ad] 挂载就绪（最长等 30 秒）
            for sec in range(30):
                # 优先匹配卡片内的 Watch ad 按钮
                candidates = driver.find_elements(
                    By.XPATH,
                    "//div[contains(., 'Rewarded ad')]//button[contains(., 'Watch')] | "
                    "//button[normalize-space(.)='Watch ad' or text()='Watch ad']"
                )
                for btn in candidates:
                    if btn.is_displayed():
                        watch_btn_selector = btn
                        break
                if watch_btn_selector:
                    print(f"  🎯 第 {sec + 1} 秒捕获到物理就绪的 [Watch ad] 按钮！", flush=True)
                    break
                time.sleep(1)

            if not watch_btn_selector:
                driver.save_screenshot(f"missing_btn_round_{current_ad}.png")
                print(f"  ⚠️ 未能在模态框内等到第 {current_ad} 轮的 [Watch ad] 按钮", flush=True)
                break

            print(f"  👉 使用 UC 物理点击第 {current_ad} 轮的 [Watch ad] 按钮...", flush=True)
            driver.uc_click(watch_btn_selector)
            time.sleep(3)

            # 等待广告播放完毕并点击 Close
            wait_and_click_ad_close(driver, max_wait_sec=40)
            completed += 1
            print(f"  ✅ 第 {current_ad} 个广告观看完成！", flush=True)
            time.sleep(4)

        # 5. 等待落库并刷新验证
        print("\n⏳ 广告流完毕，等待 8 秒后端落库后刷新页面...", flush=True)
        time.sleep(8)
        driver.refresh()
        time.sleep(5)
        dismiss_pwa_popups(driver)

        expire_info_after = get_expire_info(driver)
        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        driver.save_screenshot("final_page.png")

        tg_send(
            f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
            f"🎬 <b>观看广告：</b><code>{completed}/3</code> 轮\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_page.png",
        )
        print(f"\n✅ 任务执行完毕，最新状态: {expire_info_after}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.save_screenshot("error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>VOER Host 续期通知</b>\n\n❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="error.png",
        )
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
