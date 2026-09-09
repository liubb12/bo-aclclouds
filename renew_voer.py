#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 自动登录与看广告续期脚本 (ACLClouds 同构架构完整版)
# ============================================================
import os
import re
import html
import time
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_URL = "https://voer.host"
LOGIN_URL = f"{BASE_URL}/login"
SERVER_ID = "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992"
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
VOER_USERNAME = os.environ.get("VOER_USERNAME", "").strip()
VOER_PASSWORD = os.environ.get("VOER_PASSWORD", "").strip()
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
    """清理遮挡界面的弹窗和 Cookie 提示"""
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


def get_expire_info(driver) -> str:
    dismiss_pwa_popups(driver)
    expire_info = "未知"
    try:
        elems = driver.find_elements(By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]")
        for elem in elems:
            txt = elem.text.strip()
            if re.match(r'^\d{1,2}:\d{2}:\d{2}$', txt):
                return f"剩余 {txt}"

        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(r'(?i)(?:Time Remaining|remaining|expire)[\s:]*([0-9]+:[0-9]+:[0-9]+)', body_text)
        if time_match:
            expire_info = f"剩余 {time_match.group(1).strip()}"
        else:
            time_match_simple = re.search(r'\b(\d{1,2}:\d{2}:\d{2})\b', body_text)
            if time_match_simple:
                expire_info = f"剩余 {time_match_simple.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取时间异常: {e}")
    return expire_info


def safe_input(driver, element, value: str):
    """真实模拟点击、清空并逐字输入"""
    try:
        element.click()
        time.sleep(0.3)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
        time.sleep(0.1)
        for ch in value:
            element.send_keys(ch)
            time.sleep(0.02)
        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
    except Exception:
        pass


def solve_cf_turnstile(driver):
    """处理 Cloudflare Turnstile 验证框"""
    print("  🛡️ 正在检测并处理 Cloudflare Turnstile 验证码...", flush=True)
    time.sleep(2)
    try:
        driver.uc_gui_click_captcha()
        print("  👉 已调用 UC 专用接口点击验证框", flush=True)
    except Exception as e:
        print(f"  ℹ️ UC 接口点击回退: {e}")
        try:
            cf_frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='cloudflare'], iframe[src*='challenges']")
            for frame in cf_frames:
                driver.switch_to.frame(frame)
                box = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], span.mark, .ctp-checkbox-label")
                if box:
                    driver.execute_script("arguments[0].click();", box[0])
                    print("  👉 已穿透 iframe 勾选 Turnstile 复选框", flush=True)
                driver.switch_to.default_content()
        except Exception:
            driver.switch_to.default_content()

    for _ in range(12):
        has_token = driver.execute_script("""
            const input = document.querySelector('input[name="cf-turnstile-response"]');
            return input && input.value.length > 10;
        """)
        if has_token:
            print("  🟢 Cloudflare Turnstile 验证通过，已成功生成 Response Token！", flush=True)
            return True
        time.sleep(1)
    return False


def wait_and_click_ad_close(driver, max_wait_sec=45):
    """穿透查找并点击广告右上角的 Close 按钮"""
    print(f"  ⏳ 正在等待广告播放结束出现 Close (最长 {max_wait_sec} 秒)...", flush=True)
    start_time = time.time()

    while time.time() - start_time < max_wait_sec:
        body_str = driver.get_text("body")
        if "removed a resource-heavy ad" in body_str or "Finding ad" in body_str:
            print("  ⚠️ 检测到重度广告被拦截重置，返回等待新广告...", flush=True)
            return False

        try:
            close_buttons = driver.find_elements(
                By.XPATH,
                "//*[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' "
                "or contains(., 'Close')]"
            )
            for btn in close_buttons:
                if btn.is_displayed():
                    print("  👉 在主页面发现 Close 按钮，正在执行点击...", flush=True)
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    return True
        except Exception:
            pass

        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for frame in iframes:
                try:
                    driver.switch_to.frame(frame)
                    sub_close = driver.find_elements(
                        By.XPATH,
                        "//*[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' "
                        "or contains(., 'Close')]"
                    )
                    for btn in sub_close:
                        if btn.is_displayed():
                            print("  👉 在广告 iframe 内部发现 Close 按钮，正在点击...", flush=True)
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

    print("  ⚠️ 本轮广告关闭等待超时", flush=True)
    return False


def main():
    print("=== Python 任务初始化启动 ===", flush=True)
    if not VOER_USERNAME or not VOER_PASSWORD:
        print("❌ 未在 Secrets 中配置 VOER_USERNAME 或 VOER_PASSWORD", flush=True)
        return

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
        "--autoplay-policy=no-user-gesture-required"
    ]
    driver = Driver(uc=True, headless=False, proxy=uc_proxy, chromium_arg=" ".join(chromium_args))

    try:
        # 1. 打开登录页面并输入信息
        print(f"🌐 正在打开登录页面: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)
        time.sleep(5)
        dismiss_pwa_popups(driver)

        user_selector = "input[type='email'], input[name='email'], input[name='username'], input[type='text']"
        driver.wait_for_element_visible(user_selector, timeout=25)

        user_elem = driver.find_element(By.CSS_SELECTOR, user_selector)
        safe_input(driver, user_elem, VOER_USERNAME)
        print(f"  📝 已填入账号: {VOER_USERNAME[:3]}***", flush=True)
        time.sleep(1)

        pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        safe_input(driver, pwd_elem, VOER_PASSWORD)
        print("  📝 已填入密码", flush=True)
        time.sleep(1.5)

        # 2. 处理 Cloudflare Turnstile 验证码
        solve_cf_turnstile(driver)
        time.sleep(2)

        print("🔑 正在点击 [Sign in] 提交登录...", flush=True)
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Sign in') or contains(., 'Login')]")
        try:
            submit_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", submit_btn)

        for _ in range(15):
            if "/login" not in driver.current_url.lower():
                break
            time.sleep(1)

        if "/login" in driver.current_url.lower():
            driver.save_screenshot("login_failed.png")
            print("❌ 登录未成功跳转", flush=True)
            tg_send("🔴 <b>VOER Host 登录失败</b>", photo_path="login_failed.png")
            return

        print(f"✅ 登录成功！当前页面: {driver.current_url}", flush=True)

        # 3. 直达服务器控制台
        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(6)
        dismiss_pwa_popups(driver)

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_info_before}", flush=True)

        # 4. 寻找并点击 Extend 按钮
        extend_xpath = "//button[contains(., 'Extend')]"
        extend_elements = driver.find_elements(By.XPATH, extend_xpath)
        
        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        if not extend_elements:
            print("ℹ️ 当前未发现 Extend 按钮（可能今日次数已达上限或未开放）", flush=True)
            driver.save_screenshot("dashboard_status.png")
            tg_send(
                f"ℹ️ <b>VOER Host 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_info_before)}</code>\n"
                f"📌 <b>续期状态：</b>未发现 Extend 按钮\n"
                f"⏰ <b>巡检时间：</b><code>{now}</code>",
                photo_path="dashboard_status.png"
            )
            return

        print("👉 物理真实点击 [+ Extend] 按钮...", flush=True)
        try:
            extend_elements[0].click()
        except Exception:
            driver.execute_script("arguments[0].click();", extend_elements[0])
        time.sleep(2)

        # 5. 点击会话弹窗中的 'Watch Ads' 确认按钮
        watch_ads_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Watch Ads')]")
        if watch_ads_btns:
            print("👉 点击会话弹窗中的 [Watch Ads] 确认按钮...", flush=True)
            driver.execute_script("arguments[0].click();", watch_ads_btns[0])
            time.sleep(3)

        # 6. 核心循环：连续处理 3 轮激励广告 (0/3 ➜ 3/3)
        completed_rounds = 0
        attempts = 0

        while completed_rounds < 3 and attempts < 8:
            attempts += 1
            print(f"\n🎬 === 当前进度已完成: {completed_rounds}/3 (尝试轮次: {attempts}) ===", flush=True)

            watch_btn = None
            for _ in range(15):
                candidates = driver.find_elements(
                    By.XPATH,
                    "//button[contains(translate(., 'AD', 'ad'), 'watch ad') or contains(., 'Watch ad')]"
                )
                for c in candidates:
                    if c.is_displayed():
                        watch_btn = c
                        break
                if watch_btn:
                    break
                time.sleep(1)

            if not watch_btn:
                if "Finding ad" in driver.get_text("body"):
                    print("  ⏳ 正在加载广告 (Finding ad...)，等待 5 秒...", flush=True)
                    time.sleep(5)
                    continue
                else:
                    print("  ⚠️ 未发现 Watch ad 按钮，退出广告流", flush=True)
                    break

            print(f"  👉 点击 [Watch ad] 启动第 {completed_rounds + 1} 个广告...", flush=True)
            driver.execute_script("arguments[0].click();", watch_btn)
            time.sleep(3)

            closed = wait_and_click_ad_close(driver, max_wait_sec=40)
            if closed:
                completed_rounds += 1
                print(f"  ✅ 成功看完并关闭第 {completed_rounds} 个广告！", flush=True)
            else:
                print("  ℹ️ 本轮广告未能正常关闭，进入下轮重试...", flush=True)

            time.sleep(3)

        # 7. 等待后端落库并刷新验证
        print("\n⏳ 广告流程完毕，等待 6 秒后端写入并刷新验证...", flush=True)
        time.sleep(6)
        driver.refresh()
        time.sleep(4)
        dismiss_pwa_popups(driver)

        expire_info_after = get_expire_info(driver)
        driver.save_screenshot("final_page.png")

        tg_send(
            f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
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
