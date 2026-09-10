#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚积分脚本 (自适应50s/180s Unlockr + Cookie直登版)
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

BASE_URL = "https://dash.freemchosting.com"
LOGIN_URL = f"{BASE_URL}/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
# 积分中心的真实访问地址
BILLING_FREE_URL = f"{BASE_URL}/billing?tab=free"

LOCAL_HTTP_PORT = 18080
FREEMC_USER = os.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os.environ.get("FREEMC_PASS", "").strip()
FREEMC_COOKIES = os.environ.get("FREEMC_COOKIES", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()

# 每次执行自动刷取的奖励轮数（每天最多15轮）
DAILY_TARGET = int(os.environ.get("DAILY_TARGET", "5"))


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


def inject_cookies(driver, cookie_raw: str):
    """精确注入 Cookie，严格遵守 __Host- 前缀标准"""
    print("🍪 正在解析并注入 Cookie...", flush=True)
    items = [c.strip() for c in cookie_raw.split(";") if c.strip()]
    success_count = 0
    for item in items:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()

        cookie_dict = {"name": k, "value": v, "path": "/"}
        if k.startswith("__Host-"):
            cookie_dict["secure"] = True
        else:
            cookie_dict["domain"] = "dash.freemchosting.com"

        try:
            driver.add_cookie(cookie_dict)
            success_count += 1
        except Exception:
            try:
                driver.add_cookie({"name": k, "value": v, "path": "/", "secure": True})
                success_count += 1
            except Exception as e:
                print(f"  ⚠️ Cookie [{k}] 注入失败: {e}")

    print(f"  ✅ 成功注入 {success_count} 项 Cookie 凭证", flush=True)


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
    # 模式一：优先 Cookie 恢复登录
    if FREEMC_COOKIES:
        print("🔑 尝试通过 Cookie 恢复会话...", flush=True)
        driver.uc_open_with_reconnect(f"{BASE_URL}/robots.txt", reconnect_time=4)
        time.sleep(2)

        inject_cookies(driver, FREEMC_COOKIES)
        time.sleep(1)

        print(f"🔄 导航至控制台主页: {DASHBOARD_URL} ...", flush=True)
        driver.get(DASHBOARD_URL)
        time.sleep(6)

        body = driver.get_text("body")
        if "/login" not in driver.current_url.lower() and ("Good to see you" in body or "credits" in body.lower() or "hosting" in body.lower()):
            print(f"  ✅ Cookie 登录生效，已成功进入控制台！URL: {driver.current_url}")
            return
        print("  ⚠️ Cookie 已失效，回退至账号密码登录...")

    # 模式二：账号密码 + 盾
    print("🔑 访问登录页...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(4)

    if not FREEMC_USER or not FREEMC_PASS:
        raise ValueError("❌ FREEMC_COOKIES 失效且未配置 FREEMC_USER / FREEMC_PASS！")

    print("⌨️ 输入账号密码...", flush=True)
    driver.type("//input[@type='text' or @type='email' or @name='email' or @name='username']", FREEMC_USER)
    time.sleep(1)
    driver.type("//input[@type='password' or @name='password']", FREEMC_PASS)
    time.sleep(1)

    print("🛡️ 处理登录页 Turnstile...", flush=True)
    solve_turnstile_box(driver, max_wait_sec=30)
    time.sleep(2)

    print("🚀 提交登录表单...", flush=True)
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
        err_texts = []
        try:
            alerts = driver.find_elements(By.XPATH, "//*[contains(@class, 'alert') or contains(@class, 'error') or contains(@role, 'alert')]")
            for a in alerts:
                if a.is_displayed() and a.text.strip():
                    err_texts.append(a.text.strip())
        except Exception:
            pass
        err_detail = " | ".join(err_texts) if err_texts else "无明确错误提示（密码错误或人机拦截）"
        raise RuntimeError(f"登录失败，停留在: {driver.current_url}。原因: {err_detail}")

    print(f"📍 登录成功，当前 URL: {driver.current_url}", flush=True)


def parse_daily_limit_and_balance(driver) -> tuple:
    """提取当前进度 (例如 1/15) 以及积分余额 (例如 88.15)"""
    current_count = 0
    balance_str = "未知"
    try:
        body_text = driver.get_text("body")
        # 匹配 0 / 15 或 1 / 15
        m = re.search(r"(\d+)\s*/\s*15", body_text)
        if m:
            current_count = int(m.group(1))

        bm = re.search(r"([0-9]+\.[0-9]+)\s*(?:COIN\s*)?credits", body_text, re.IGNORECASE)
        if bm:
            balance_str = bm.group(1)
        else:
            bm2 = re.search(r"([0-9]+\.[0-9]+)", body_text)
            if bm2:
                balance_str = bm2.group(1)
    except Exception as e:
        print(f"  ⚠️ 提取余额状态异常: {e}")
    return current_count, balance_str


def enter_reward_section(driver):
    """确保进入具体的 Earn credits 任务页面"""
    driver.get(BILLING_FREE_URL)
    time.sleep(4)

    # 1. 如果还在概览页，点击 Earn credits 按钮进入具体任务卡片
    earn_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Earn credits') or contains(., 'Open rewards')] | //a[contains(., 'Earn credits')]")
    if earn_btns:
        for b in earn_btns:
            if b.is_displayed():
                print("  👉 点击 [Earn credits] 进入任务中心...", flush=True)
                physical_click_trusted(driver, b)
                time.sleep(3)
                break


def run_single_task_loop(driver) -> bool:
    """执行单个 Unlockr 广告任务闭环（自适应 50s / 180s 任务时长）"""
    enter_reward_section(driver)

    main_tab = driver.current_window_handle

    # 1. 查找并点击 Generate reward
    gen_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward') or contains(., 'Start a reward')]")
    if gen_btns and gen_btns[0].is_displayed():
        print("  👉 点击 [Generate reward] 生成任务...", flush=True)
        physical_click_trusted(driver, gen_btns[0])
        time.sleep(3)

    # 2. 查找并点击 Start reward 唤醒外链
    start_btn = None
    for _ in range(15):
        s_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start reward')]")
        for sb in s_btns:
            if sb.is_displayed():
                start_btn = sb
                break
        if start_btn:
            break
        time.sleep(1)

    if not start_btn:
        print("  ⚠️ 未能出现 [Start reward] 按钮", flush=True)
        return False

    print("  👉 点击 [Start reward]，打开广告任务外链...", flush=True)
    physical_click_trusted(driver, start_btn)
    time.sleep(5)

    # 3. 切换到新弹出的外链标签页（Unlockr 任务墙）
    handles_after_start = driver.window_handles
    if len(handles_after_start) <= 1:
        print("  ⚠️ 点击后未检测到新标签页弹出", flush=True)
        return False

    unlockr_tab = handles_after_start[-1]
    driver.switch_to.window(unlockr_tab)
    time.sleep(4)
    print(f"  🌐 已切入 Unlockr 任务页: {driver.current_url}", flush=True)

    # 4. 自适应解析任务时间（支持 50 秒短视频或 180 秒长文章）
    wait_sec = 50
    try:
        body_text = driver.get_text("body")
        sec_m = re.search(r"~(\d+)\s*sec", body_text)
        if sec_m:
            wait_sec = int(sec_m.group(1))
            print(f"  ⏱️ 动态识别到任务时长：~{wait_sec} 秒", flush=True)
        else:
            print("  ℹ️ 未检测到具体秒数，采用基准等待 60 秒", flush=True)
            wait_sec = 60

        # 点击第一项（阅读文章/观看视频）右侧的箭头 ➔
        task1_arrow_xpaths = [
            "(//div[contains(., 'Complete Tasks to Continue')]/following::button[.//svg])[1]",
            "(//div[contains(., 'Complete Tasks to Continue')]//button)[1]",
            "(//button[.//svg or contains(@class, 'arrow')])[1]"
        ]
        arrow1_clicked = False
        for xp in task1_arrow_xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els and els[0].is_displayed():
                print("  👉 点击任务 1 箭头 ➔，唤起广告页面...", flush=True)
                physical_click_trusted(driver, els[0])
                arrow1_clicked = True
                break

        if not arrow1_clicked:
            print("  ⚠️ 未能点击到任务 1 箭头，尝试全局模糊搜索并点击")
            driver.execute_script("const btns = document.querySelectorAll('button'); if(btns.length>0) btns[0].click();")

        time.sleep(3)

        # 4.1 如果点击后又弹出了广告第三层标签页，切过去挂机再关闭
        if len(driver.window_handles) > 2:
            ad_tab = driver.window_handles[-1]
            driver.switch_to.window(ad_tab)
            print(f"  ⏳ 已切入广告挂机标签页，等待倒计时 {wait_sec + 15} 秒...", flush=True)
            time.sleep(wait_sec + 15)
            try:
                driver.close()
            except Exception:
                pass
            driver.switch_to.window(unlockr_tab)
        else:
            print(f"  ⏳ 原标签页就地挂机等待倒计时 {wait_sec + 15} 秒...", flush=True)
            time.sleep(wait_sec + 15)

    except Exception as e:
        print(f"  ⚠️ 处理任务 1 挂机阶段提示: {e}", flush=True)

    time.sleep(3)
    driver.switch_to.window(unlockr_tab)

    # 5. 处理任务 2：CONFIRM YOU ARE HUMAN (Turnstile 验证)
    try:
        print("🛡️ 正在寻找并点击 [CONFIRM YOU ARE HUMAN] 验证...", flush=True)
        human_xpaths = [
            "//*[contains(text(), 'CONFIRM YOU ARE HUMAN') or contains(text(), 'Confirm you are human')]",
            "(//div[contains(., 'Complete Tasks to Continue')]/following::button[.//svg])[2]",
            "(//button[.//svg or contains(@class, 'arrow')])[2]"
        ]
        for xp in human_xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els and els[0].is_displayed():
                physical_click_trusted(driver, els[0])
                break
        time.sleep(3)

        print("  🛡️ 尝试通过任务页 Turnstile 验证框...", flush=True)
        solve_turnstile_box(driver, max_wait_sec=25)
        time.sleep(2)

        # 点击确认后的 Continue 按钮
        continue_xpaths = [
            "//button[normalize-space(.)='Continue' or text()='Continue']",
            "//button[contains(., 'Continue')]"
        ]
        for _ in range(8):
            clicked_c = False
            for xp in continue_xpaths:
                els = driver.find_elements(By.XPATH, xp)
                if els and els[0].is_displayed():
                    physical_click_trusted(driver, els[0])
                    print("  ✅ 已成功点击 [Continue] 按钮", flush=True)
                    clicked_c = True
                    break
            if clicked_c:
                break
            time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️ 处理任务 2 人机验证提示: {e}", flush=True)

    time.sleep(3)
    driver.switch_to.window(unlockr_tab)

    # 6. 最终点击 CLAIM REWARD
    claim_success = False
    try:
        print("🎁 等待 [CLAIM REWARD] 按钮解锁生效...", flush=True)
        claim_xpaths = [
            "//button[contains(., 'CLAIM REWARD') or contains(., 'Claim reward') or contains(., 'Claim')]"
        ]
        for _ in range(15):
            for xp in claim_xpaths:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    # 检查是否处于已启用状态（非 disabled）
                    if el.is_displayed() and not el.get_attribute("disabled"):
                        physical_click_trusted(driver, el)
                        print("  🎉 成功击发 [CLAIM REWARD]！", flush=True)
                        claim_success = True
                        time.sleep(6)
                        break
                if claim_success:
                    break
            if claim_success:
                break
            time.sleep(2)
    except Exception as e:
        print(f"  ⚠️ 点击领取奖励异常: {e}", flush=True)

    # 7. 清理关闭除主面板外的所有附属标签页
    try:
        for handle in driver.window_handles:
            if handle != main_tab:
                driver.switch_to.window(handle)
                driver.close()
        driver.switch_to.window(main_tab)
    except Exception:
        pass

    return claim_success


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
        enter_reward_section(driver)
        time.sleep(4)

        current_count, balance_before = parse_daily_limit_and_balance(driver)
        print(f"📊 当前进度: {current_count}/15 | 本次目标轮数: {DAILY_TARGET} | 初始余额: {balance_before}", flush=True)

        while current_count < 15 and success_runs < DAILY_TARGET:
            print(f"\n🚀 === 执行第 {current_count + 1} 轮赚积分任务 ===", flush=True)
            ok = run_single_task_loop(driver)
            if ok:
                success_runs += 1
                print(f"  ✅ 第 {current_count + 1} 轮闭环完成！", flush=True)
            else:
                print(f"  ⚠️ 第 {current_count + 1} 轮未能正常领奖，稍作冷却后重试...", flush=True)

            time.sleep(5)
            enter_reward_section(driver)
            time.sleep(3)
            current_count, _ = parse_daily_limit_and_balance(driver)
            print(f"  📈 最新当日累计进度: {current_count}/15", flush=True)

        enter_reward_section(driver)
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
        print("\n🎯 任务执行完毕，收工！")

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
