#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚取积分脚本 (VOER 强穿透同构版)
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
from selenium.webdriver.common.action_chains import ActionChains

LOGIN_URL = "https://dash.freemchosting.com/login"
DASHBOARD_URL = "https://dash.freemchosting.com/"
EARN_CREDITS_URL = "https://dash.freemchosting.com/free"

LOCAL_HTTP_PORT = 18081
FREEMC_USER = os.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os.environ.get("FREEMC_PASS", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()

DAILY_TARGET = 5  # 每日完成 5 次


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


def physical_click_trusted(driver, element):
    """派发真正带 isTrusted 的物理指针点击"""
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


def recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=4) -> bool:
    """递归深入嵌套 iframe 查找并物理点击"""
    for xpath in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xpath)
            for el in elems:
                if el.is_displayed():
                    print(f"  👉 在深度 {current_depth} 物理击中目标: {xpath}...", flush=True)
                    physical_click_trusted(driver, el)
                    return True
        except Exception:
            pass

    if current_depth >= max_depth:
        return False

    try:
        sub_frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        sub_frames = []

    for idx in range(len(sub_frames)):
        try:
            frames = driver.find_elements(By.TAG_NAME, "iframe")
            if idx >= len(frames):
                break
            driver.switch_to.frame(frames[idx])
            found = recursive_find_and_click(driver, xpaths, current_depth + 1, max_depth)
            driver.switch_to.parent_frame()
            if found:
                return True
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass

    return False


def solve_turnstile(driver, max_wait_sec=25) -> bool:
    """穿透多层 iframe 处理 Cloudflare Turnstile 验证框"""
    print("  🛡️ 深度检索并处理 Cloudflare Turnstile 验证...", flush=True)
    driver.switch_to.default_content()

    # 尝试自带的 GUI 点击
    try:
        driver.uc_gui_click_captcha()
        time.sleep(2)
    except Exception:
        pass

    cf_checkbox_xpaths = [
        "//input[@type='checkbox']",
        "//label[contains(@class, 'ctp-checkbox-label')]",
        "//div[contains(@id, 'challenge-stage')]",
        "//span[contains(@class, 'mark')]",
        "//*[contains(@class, 'cb-lb')]",
        "//div[@class='cb-c']"
    ]

    start_time = time.time()
    while time.time() - start_time < max_wait_sec:
        driver.switch_to.default_content()
        body_text = driver.get_text("body")
        if "成功" in body_text or "Success" in body_text:
            print("  ✅ Turnstile 已处于通过状态！", flush=True)
            return True

        if recursive_find_and_click(driver, cf_checkbox_xpaths, current_depth=0, max_depth=3):
            time.sleep(3)
            driver.switch_to.default_content()
            return True

        time.sleep(1.5)

    driver.switch_to.default_content()
    return False


def login_freemc(driver):
    """登录流程：填入凭证 -> 物理穿透 Turnstile -> 提交"""
    print("🔑 打开登录页...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(4)

    print("⌨️ 填入账号密码...", flush=True)
    driver.type("input[type='text'], input[type='email'], input[name='username']", FREEMC_USER)
    time.sleep(1)
    driver.type("input[type='password'], input[name='password']", FREEMC_PASS)
    time.sleep(1)

    # 解决登录页 Turnstile
    solve_turnstile(driver, max_wait_sec=20)
    time.sleep(2)

    print("🚀 点击 Sign in 提交登录...", flush=True)
    signin_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Sign in') or @type='submit']")
    if signin_btns:
        physical_click_trusted(driver, signin_btns[0])

    # 验证是否成功跳转出登录页
    logged_in = False
    for sec in range(25):
        if "/login" not in driver.current_url.lower():
            logged_in = True
            break
        time.sleep(1)

    if not logged_in:
        driver.save_screenshot("login_failed.png")
        raise RuntimeError(f"登录失败，仍然停留在: {driver.current_url}")

    print(f"📍 登录成功，当前 URL: {driver.current_url}", flush=True)


def parse_daily_limit_and_balance(driver) -> tuple:
    """提取已完成次数与积分余额"""
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
    """完成单次任务循环"""
    driver.get(EARN_CREDITS_URL)
    time.sleep(4)

    # 1. 点击 Generate reward
    gen_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward') or contains(., 'Start reward')]")
    if not gen_btns:
        print("⚠️ 未找到 Generate/Start 按钮，页面可能仍在加载")
        return False

    main_handle = driver.current_window_handle
    physical_click_trusted(driver, gen_btns[0])
    time.sleep(3)

    # 2. 点击 Start reward
    start_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start reward')]")
    if start_btns and start_btns[0].is_displayed():
        physical_click_trusted(driver, start_btns[0])
        time.sleep(4)

    # 切换至 Unlockr 标签页
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    time.sleep(3)

    print("🧩 进入 Unlockr 页面，正在分析子任务...", flush=True)

    # 3. 解析任务 1 等待时长并执行
    try:
        body_text = driver.get_text("body")
        sec_match = re.search(r"~(\d+)\s*sec", body_text)
        if sec_match:
            wait_sec = int(sec_match.group(1))
        else:
            sec_match2 = re.search(r"(\d+)\s*sec", body_text)
            wait_sec = int(sec_match2.group(1)) if sec_match2 else 50

        print(f"  ⏱️ 识别到任务要求时长: {wait_sec} 秒", flush=True)

        task1_arrow_xpaths = [
            "(//div[contains(., 'Complete Tasks to Continue')]/following::button[.//svg])[1]",
            "(//button[.//svg or contains(@class, 'arrow')])[1]"
        ]
        recursive_find_and_click(driver, task1_arrow_xpaths, current_depth=0, max_depth=2)
        time.sleep(3)

        # 若弹出外链标签页，切换过去等待
        if len(driver.window_handles) > 2:
            ad_handle = driver.window_handles[-1]
            driver.switch_to.window(ad_handle)
            print(f"  ⏳ 在外链标签页等待 {wait_sec + 5} 秒...", flush=True)
            time.sleep(wait_sec + 5)
            driver.close()
            driver.switch_to.window(driver.window_handles[-1])
        else:
            print(f"  ⏳ 原地等待 {wait_sec + 5} 秒...", flush=True)
            time.sleep(wait_sec + 5)

    except Exception as e:
        print(f"  ⚠️ 任务 1 执行警告: {e}")

    time.sleep(3)
    driver.switch_to.default_content()

    # 4. 执行任务 2：人机验证 (Turnstile)
    try:
        print("🛡️ 点击展开 [CONFIRM YOU ARE HUMAN]...", flush=True)
        human_xpaths = [
            "//*[contains(text(), 'CONFIRM YOU ARE HUMAN') or contains(text(), 'Confirm you are human')]",
            "(//button[.//svg or contains(@class, 'arrow')])[2]"
        ]
        recursive_find_and_click(driver, human_xpaths, current_depth=0, max_depth=2)
        time.sleep(3)

        solve_turnstile(driver, max_wait_sec=25)

        # 点击 Continue
        continue_xpaths = ["//button[normalize-space(.)='Continue' or text()='Continue']"]
        for _ in range(10):
            if recursive_find_and_click(driver, continue_xpaths, current_depth=0, max_depth=3):
                print("  ✅ 成功点击 Continue 按钮！", flush=True)
                break
            time.sleep(2)

    except Exception as e:
        print(f"  ⚠️ 任务 2 验证警告: {e}")

    time.sleep(3)
    driver.switch_to.default_content()

    # 5. 点击 CLAIM REWARD
    try:
        claim_xpaths = [
            "//button[contains(., 'CLAIM REWARD')]",
            "//button[contains(translate(., 'claim', 'CLAIM'), 'CLAIM REWARD')]"
        ]
        for _ in range(10):
            if recursive_find_and_click(driver, claim_xpaths, current_depth=0, max_depth=2):
                print("  🎉 成功点击 [CLAIM REWARD]！", flush=True)
                time.sleep(6)
                break
            time.sleep(2)
    except Exception as e:
        print(f"  ⚠️ Claim 点击异常: {e}")

    # 清理多余标签页切回控制台
    try:
        for handle in driver.window_handles:
            if handle != main_handle:
                driver.switch_to.window(handle)
                driver.close()
        driver.switch_to.window(main_handle)
    except Exception:
        pass

    return True


def main():
    print("=== Freemchosting 自动化刷分任务启动 ===", flush=True)

    gost_proc = None
    uc_proxy = None

    # 如果配置了 SOCKS5 代理，启动 gost 本地中转（与 VOER 机制一致）
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
        print(f"📊 当前今日进度: {current_count}/15 | 目标: {DAILY_TARGET} 次 | 当前余额: {balance_before}", flush=True)

        while current_count < DAILY_TARGET and success_runs < DAILY_TARGET:
            print(f"\n🚀 开始执行第 {current_count + 1} 次任务循环...", flush=True)
            ok = run_single_task_loop(driver)
            if ok:
                success_runs += 1
                time.sleep(5)
                driver.get(EARN_CREDITS_URL)
                time.sleep(3)
                current_count, _ = parse_daily_limit_and_balance(driver)
                print(f"✅ 最新进度: {current_count}/{DAILY_TARGET}", flush=True)
            else:
                time.sleep(6)

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
        print(f"\n🎯 任务达成，完成 {success_runs} 次！")

    except Exception as e:
        err_msg = str(e)
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
