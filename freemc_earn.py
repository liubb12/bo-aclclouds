#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚积分脚本 (增强表单检测 + Cookie/密码双模版)
# ============================================================
import os
import re
import sys
import html
import time
import subprocess
import requests
import traceback
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

sys.stdout.reconfigure(line_buffering=True)

LOGIN_URL = "https://dash.freemchosting.com/login"
EARN_CREDITS_URL = "https://dash.freemchosting.com/free"

LOCAL_HTTP_PORT = 18080
FREEMC_USER = os.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os.environ.get("FREEMC_PASS", "").strip()
FREEMC_COOKIES = os.environ.get("FREEMC_COOKIES", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()

DAILY_TARGET = 5


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(
                    url,
                    data={"chat_id": TG_CHAT_ID, "caption": text, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=30,
                )
        else:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            requests.post(
                url,
                data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=30,
            )
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
    start = time.time()
    last_error = None
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
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地端口：{LOCAL_HTTP_PORT}")
    return proc


def physical_click_trusted(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        time.sleep(0.1)
    except Exception:
        pass
    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
        return
    except Exception:
        pass
    try:
        element.click()
        return
    except Exception:
        pass
    try:
        driver.execute_script("""
            const el = arguments[0];
            ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
                el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
            });
        """, element)
    except Exception:
        pass


def solve_turnstile_box(driver, max_wait_sec=30) -> bool:
    driver.switch_to.default_content()
    start = time.time()
    try:
        driver.uc_gui_click_captcha()
        time.sleep(2)
    except Exception:
        pass

    while time.time() - start < max_wait_sec:
        driver.switch_to.default_content()
        # 检查 Turnstile 隐藏 response 字段是否已经填充
        has_token = driver.execute_script("""
            const el = document.querySelector('[name="cf-turnstile-response"]');
            return el && el.value && el.value.length > 10;
        """)
        if has_token:
            print("  ✅ Turnstile 凭证 Token 获取成功", flush=True)
            return True

        body = driver.get_text("body")
        if "成功" in body or "Success" in body:
            return True

        try:
            cf_frames = driver.find_elements(By.XPATH, "//iframe[contains(@src, 'challenges.cloudflare.com') or contains(@id, 'cf-chl-widget')]")
            if cf_frames:
                driver.switch_to.frame(cf_frames[0])
                boxes = driver.find_elements(By.XPATH, "//input[@type='checkbox'] | //span[@id='challenge-stage'] | //label[contains(@class, 'ctp-checkbox-label')]")
                for box in boxes:
                    if box.is_displayed():
                        physical_click_trusted(driver, box)
                        break
                driver.switch_to.default_content()
                time.sleep(2)
        except Exception:
            driver.switch_to.default_content()

        time.sleep(1.5)

    driver.switch_to.default_content()
    return False


def login_freemc(driver):
    # 模式一：支持 Cookie 恢复登录
    if FREEMC_COOKIES:
        print("🍪 尝试通过 Cookie 恢复会话...", flush=True)
        driver.get("https://dash.freemchosting.com/robots.txt")
        time.sleep(1)
        for item in FREEMC_COOKIES.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                try:
                    driver.add_cookie({"name": k, "value": v, "domain": ".freemchosting.com", "path": "/"})
                except Exception:
                    pass
        driver.get("https://dash.freemchosting.com/")
        time.sleep(4)
        if "/login" not in driver.current_url.lower():
            print(f"  ✅ Cookie 登录生效，进入面板: {driver.current_url}")
            return
        print("  ⚠️ Cookie 已失效，回退至账号密码登录...")

    # 模式二：账号密码 + 盾
    print("🔑 访问登录页...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(4)

    if not FREEMC_USER or not FREEMC_PASS:
        raise ValueError("❌ FREEMC_USER 或 FREEMC_PASS 为空，请检查配置！")

    print("⌨️ 输入账号密码...", flush=True)
    driver.type("//input[@type='text' or @type='email' or @name='email' or @name='username']", FREEMC_USER)
    time.sleep(1)
    driver.type("//input[@type='password' or @name='password']", FREEMC_PASS)
    time.sleep(1)

    print("🛡️ 处理登录页 Turnstile...", flush=True)
    solve_turnstile_box(driver, max_wait_sec=30)
    time.sleep(2)

    print("🚀 提交登录表单...", flush=True)
    # 优先查找 submit 按钮或执行表单 submit
    signin_btns = driver.find_elements(By.XPATH, "//button[@type='submit' or contains(., 'Sign in') or contains(., 'Login')]")
    if signin_btns:
        physical_click_trusted(driver, signin_btns[0])
    else:
        driver.execute_script("document.querySelector('form').submit();")

    logged_in = False
    for _ in range(20):
        time.sleep(1)
        if "/login" not in driver.current_url.lower():
            logged_in = True
            break

    if not logged_in:
        driver.save_screenshot("login_failed.png")
        # 抓取页面报错提示
        err_texts = []
        try:
            alerts = driver.find_elements(By.XPATH, "//*[contains(@class, 'alert') or contains(@class, 'error') or contains(@role, 'alert')]")
            for a in alerts:
                if a.is_displayed() and a.text.strip():
                    err_texts.append(a.text.strip())
        except Exception:
            pass
        err_detail = " | ".join(err_texts) if err_texts else "无明确错误提示（可能 Turnstile 校验被拒或密码错误）"
        raise RuntimeError(f"登录失败，停留在: {driver.current_url}。原因: {err_detail}")

    print(f"📍 登录成功，当前 URL: {driver.current_url}", flush=True)


def parse_daily_limit_and_balance(driver) -> tuple:
    current_count = 0
    balance_str = "未知"
    try:
        body_text = driver.get_text("body")
        m = re.search(r"(\d+)\s*/\s*15", body_text)
        if m:
            current_count = int(m.group(1))
        bm = re.search(r"([0-9]+\.[0-9]+)\s*credits", body_text, re.IGNORECASE)
        if bm:
            balance_str = bm.group(1)
    except Exception:
        pass
    return current_count, balance_str


def run_single_task_loop(driver) -> bool:
    driver.get(EARN_CREDITS_URL)
    time.sleep(4)

    gen_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward') or contains(., 'Start reward')]")
    if not gen_btns:
        print("⚠️ 未找到 Generate/Start 按钮")
        return False

    main_tab = driver.current_window_handle
    physical_click_trusted(driver, gen_btns[0])
    time.sleep(3)

    start_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start reward')]")
    if start_btns and start_btns[0].is_displayed():
        physical_click_trusted(driver, start_btns[0])
        time.sleep(4)

    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    time.sleep(3)

    try:
        body_text = driver.get_text("body")
        sec_m = re.search(r"~(\d+)\s*sec", body_text)
        wait_sec = int(sec_m.group(1)) if sec_m else 50
        print(f"  ⏱️ 任务要求等待: {wait_sec} 秒", flush=True)

        task1_arrow_xpaths = [
            "(//div[contains(., 'Complete Tasks to Continue')]/following::button[.//svg])[1]",
            "(//button[.//svg or contains(@class, 'arrow')])[1]"
        ]
        for xp in task1_arrow_xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els and els[0].is_displayed():
                physical_click_trusted(driver, els[0])
                break
        time.sleep(3)

        if len(driver.window_handles) > 2:
            driver.switch_to.window(driver.window_handles[-1])
            print(f"  ⏳ 外链标签页等待 {wait_sec + 5} 秒...", flush=True)
            time.sleep(wait_sec + 5)
            driver.close()
            driver.switch_to.window(driver.window_handles[-1])
        else:
            print(f"  ⏳ 原地等待 {wait_sec + 5} 秒...", flush=True)
            time.sleep(wait_sec + 5)
    except Exception as e:
        print(f"  ⚠️ 任务 1 提示: {e}")

    time.sleep(3)
    driver.switch_to.default_content()

    try:
        print("🛡️ 处理任务人机验证...", flush=True)
        human_xpaths = [
            "//*[contains(text(), 'CONFIRM YOU ARE HUMAN') or contains(text(), 'Confirm you are human')]",
            "(//button[.//svg or contains(@class, 'arrow')])[2]"
        ]
        for xp in human_xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els and els[0].is_displayed():
                physical_click_trusted(driver, els[0])
                break
        time.sleep(3)

        solve_turnstile_box(driver, max_wait_sec=25)

        continue_xpaths = ["//button[normalize-space(.)='Continue' or text()='Continue']"]
        for _ in range(8):
            for xp in continue_xpaths:
                els = driver.find_elements(By.XPATH, xp)
                if els and els[0].is_displayed():
                    physical_click_trusted(driver, els[0])
                    print("  ✅ 已点击 Continue")
                    break
            time.sleep(2)
    except Exception as e:
        print(f"  ⚠️ 任务 2 提示: {e}")

    time.sleep(3)
    driver.switch_to.default_content()

    try:
        claim_xpaths = ["//button[contains(., 'CLAIM REWARD') or contains(., 'Claim')]"]
        for _ in range(8):
            for xp in claim_xpaths:
                els = driver.find_elements(By.XPATH, xp)
                if els and els[0].is_displayed():
                    physical_click_trusted(driver, els[0])
                    print("  🎉 已点击 CLAIM REWARD！")
                    time.sleep(6)
                    break
            time.sleep(2)
    except Exception as e:
        print(f"  ⚠️ 领奖提示: {e}")

    try:
        for handle in driver.window_handles:
            if handle != main_tab:
                driver.switch_to.window(handle)
                driver.close()
        driver.switch_to.window(main_tab)
    except Exception:
        pass

    return True


def main():
    print("=== Freemchosting 任务启动 ===", flush=True)

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
    success_runs = 0

    try:
        login_freemc(driver)
        driver.get(EARN_CREDITS_URL)
        time.sleep(4)

        current_count, balance_before = parse_daily_limit_and_balance(driver)
        print(f"📊 当前进度: {current_count}/15 | 目标: {DAILY_TARGET} | 余额: {balance_before}", flush=True)

        while current_count < DAILY_TARGET and success_runs < DAILY_TARGET:
            print(f"\n🚀 执行第 {current_count + 1} 轮...", flush=True)
            ok = run_single_task_loop(driver)
            if ok:
                success_runs += 1
                time.sleep(5)
                driver.get(EARN_CREDITS_URL)
                time.sleep(3)
                current_count, _ = parse_daily_limit_and_balance(driver)
                print(f"✅ 完成次数: {current_count}/{DAILY_TARGET}", flush=True)
            else:
                time.sleep(5)

        driver.get(EARN_CREDITS_URL)
        time.sleep(4)
        _, balance_after = parse_daily_limit_and_balance(driver)
        driver.save_screenshot("freemc_final.png")

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        tg_send(
            f"💰 <b>Freemchosting 刷分汇总</b>\n\n"
            f"🎯 <b>今日进度：</b><code>{current_count}/15</code> (本次完成 +{success_runs})\n"
            f"💳 <b>积分变动：</b><code>{html.escape(balance_before)}</code> ➜ <code><b>{html.escape(balance_after)}</b></code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="freemc_final.png",
        )
        print(f"\n🎯 达成目标，收工！")

    except Exception as e:
        err_msg = str(e)
        traceback.print_exc()
        print(f"❌ 执行异常: {err_msg}")
        try:
            driver.switch_to.default_content()
            driver.save_screenshot("freemc_error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>Freemchosting 刷分通知</b>\n\n❌ <b>异常信息</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="freemc_error.png",
        )
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
