#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚积分脚本 (Cookie 修复版 & 5轮目标版)
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
REWARDS_URL = f"{BASE_URL}/rewards"

LOCAL_HTTP_PORT = 18080
FREEMC_USER = os.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os.environ.get("FREEMC_PASS", "").strip()
FREEMC_COOKIES = os.environ.get("FREEMC_COOKIES", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()

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


def physical_click_trusted(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        time.sleep(0.2)
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
    if FREEMC_COOKIES:
        print("🔑 尝试通过 Cookie 恢复会话...", flush=True)
        # 修复点：先打开主域名建立域上下文，再注入 Cookie 避免 invalid cookie domain 报错
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
        time.sleep(3)

        inject_cookies(driver, FREEMC_COOKIES)
        time.sleep(1)

        print(f"🔄 导航至任务中心: {REWARDS_URL} ...", flush=True)
        driver.get(REWARDS_URL)
        time.sleep(6)

        body = driver.get_text("body")
        if "/login" not in driver.current_url.lower() and ("Generate reward" in body or "rewards" in driver.current_url):
            print(f"  ✅ Cookie 登录生效，直达任务中心！URL: {driver.current_url}")
            return
        print("  ⚠️ Cookie 已失效，回退至账号密码登录...")

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
    current_count = 0
    balance_str = "未知"
    try:
        body_text = driver.get_text("body")
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


def wait_for_lootlabs_ready(driver, timeout=30) -> bool:
    """智能检测 LootLabs 页面，若遇到 Packet blocked 则自动点击 HERE 刷新"""
    print("  ⏳ 等待 LootLabs 页面就绪...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        body = driver.get_text("body")

        if "Packet blocked" in body or "HERE" in body:
            print("  🚨 检测到 Packet blocked 软拦截，自动点击 [HERE] 刷新...", flush=True)
            try:
                here_els = driver.find_elements(By.XPATH, "//a[normalize-space(.)='HERE' or contains(., 'HERE')] | //u[contains(., 'HERE')] | //*[text()='HERE']")
                if here_els:
                    for h in here_els:
                        if h.is_displayed():
                            physical_click_trusted(driver, h)
                            break
                else:
                    driver.execute_script("const a = Array.from(document.querySelectorAll('a, span, u')).find(el => el.textContent.trim() === 'HERE'); if(a) a.click();")
            except Exception as e:
                print(f"  ⚠️ 点击 HERE 异常: {e}")
            time.sleep(3)
            continue

        if "Complete Tasks to Continue" in body or "CONFIRM YOU ARE HUMAN" in body:
            print("  ✅ LootLabs 任务容器已成功加载！", flush=True)
            return True

        time.sleep(1.5)
    return False


def run_single_task_loop(driver, round_num: int) -> bool:
    main_tab = driver.current_window_handle

    if driver.current_url != REWARDS_URL:
        driver.get(REWARDS_URL)
        time.sleep(4)

    # 1. 查找 Generate reward
    gen_btns = driver.find_elements(By.XPATH, "//button[normalize-space(.)='Generate reward' or contains(., 'Generate reward')]")
    if gen_btns and gen_btns[0].is_displayed():
        print(f"  👉 点击 [Generate reward] 生成第 {round_num} 轮任务...", flush=True)
        physical_click_trusted(driver, gen_btns[0])
        time.sleep(3)

    # 2. 等待 Start reward 出现
    start_btn = None
    print("  ⏳ 等待异步接口返回 [Start reward]...", flush=True)
    for wait_i in range(25):
        s_btns = driver.find_elements(
            By.XPATH,
            "//button[normalize-space(.)='Start reward' or contains(., 'Start reward')] | //a[contains(., 'Start reward')]"
        )
        for sb in s_btns:
            if sb.is_displayed():
                start_btn = sb
                break
        if start_btn:
            print(f"  ✅ 在第 {wait_i + 1} 秒检测到 [Start reward] 亮起！", flush=True)
            break

        if wait_i == 6:
            re_gen = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward')]")
            if re_gen and re_gen[0].is_displayed():
                print("  🔄 补点一次 [Generate reward]...", flush=True)
                physical_click_trusted(driver, re_gen[0])

        time.sleep(1)

    if not start_btn:
        print(f"  ⚠️ 第 {round_num} 轮超时未能出现 [Start reward] 按钮，保存截图...", flush=True)
        driver.save_screenshot(f"fail_no_start_r{round_num}.png")
        return False

    old_handles = set(driver.window_handles)
    print("  👉 击发 [Start reward]，唤起 LootLabs 任务外链...", flush=True)
    physical_click_trusted(driver, start_btn)
    time.sleep(5)

    # 3. 切换到 LootLabs 标签页
    new_handles = set(driver.window_handles) - old_handles
    if not new_handles:
        if len(driver.window_handles) > 1:
            lootlabs_tab = driver.window_handles[-1]
        else:
            print("  ⚠️ 点击后未检测到新标签页弹出，保存截图...", flush=True)
            driver.save_screenshot(f"fail_no_tab_r{round_num}.png")
            return False
    else:
        lootlabs_tab = list(new_handles)[0]

    driver.switch_to.window(lootlabs_tab)
    time.sleep(4)
    print(f"  🌐 已切入 LootLabs: {driver.current_url}", flush=True)

    if not wait_for_lootlabs_ready(driver, timeout=30):
        print("  ⚠️ LootLabs 任务卡片未能就绪，记录现场...", flush=True)
        driver.save_screenshot(f"fail_lootlabs_loading_r{round_num}.png")
        return False

    # 4. 任务 1：自适应挂机与倒计时
    wait_sec = 50
    try:
        body_text = driver.get_text("body")
        sec_m = re.search(r"~(\d+)\s*sec", body_text)
        if sec_m:
            wait_sec = int(sec_m.group(1))
            print(f"  ⏱️ 动态识别到任务 1 需求时长：~{wait_sec} 秒", flush=True)
        else:
            print("  ℹ️ 未检测到具体秒数，采用基准等待 60 秒", flush=True)
            wait_sec = 60

        task1_selectors = [
            "//*[contains(text(), 'Complete Tasks to Continue')]/following::div[contains(@class, 'cursor-pointer') or .//button][1]",
            "(//*[contains(text(), 'Complete Tasks to Continue')]/following::*[contains(@class, 'rounded') and (.//button or .//svg)])[1]",
            "//*[contains(text(), 'Complete Tasks to Continue')]/following::button[1]",
            "(//button[.//svg])[last()-1]"
        ]

        arrow1_clicked = False
        for sel in task1_selectors:
            els = driver.find_elements(By.XPATH, sel)
            if els and els[0].is_displayed():
                print(f"  👉 锁定并点击任务 1 交互项...", flush=True)
                physical_click_trusted(driver, els[0])
                arrow1_clicked = True
                break

        if not arrow1_clicked:
            driver.execute_script("""
                const header = Array.from(document.querySelectorAll('*')).find(el => el.textContent.includes('Complete Tasks to Continue'));
                if (header) {
                    const task = header.parentElement.querySelector('button, [class*="cursor-pointer"]');
                    if (task) task.click();
                }
            """)

        time.sleep(3)

        if len(driver.window_handles) > 2:
            ad_tab = driver.window_handles[-1]
            driver.switch_to.window(ad_tab)
            print(f"  ⏳ 切入外链标签页，挂机等待 {wait_sec + 15} 秒...", flush=True)
            time.sleep(wait_sec + 15)
            try:
                driver.close()
            except Exception:
                pass
            driver.switch_to.window(lootlabs_tab)
        else:
            print(f"  ⏳ 就地挂机等待倒计时 {wait_sec + 15} 秒...", flush=True)
            time.sleep(wait_sec + 15)

    except Exception as e:
        print(f"  ⚠️ 处理任务 1 阶段提示: {e}", flush=True)

    time.sleep(3)
    driver.switch_to.window(lootlabs_tab)

    # 5. 任务 2：人机验证与 Continue
    try:
        print("🛡️ 正在寻找并点击 [CONFIRM YOU ARE HUMAN] 验证项...", flush=True)
        human_selectors = [
            "//*[contains(text(), 'CONFIRM YOU ARE HUMAN')]/ancestor::div[contains(@class, 'cursor-pointer') or .//button][1]",
            "//*[contains(text(), 'CONFIRM YOU ARE HUMAN')]",
            "//*[contains(text(), 'Complete Tasks to Continue')]/following::button[2]"
        ]
        for sel in human_selectors:
            els = driver.find_elements(By.XPATH, sel)
            if els and els[0].is_displayed():
                physical_click_trusted(driver, els[0])
                print("  👉 已点击展开人机验证卡片！", flush=True)
                break
        time.sleep(3)

        print("  🛡️ 尝试通过任务页 Turnstile 验证框...", flush=True)
        solve_turnstile_box(driver, max_wait_sec=25)
        time.sleep(2)

        continue_selectors = [
            "//button[normalize-space(.)='Continue' or text()='Continue']",
            "//button[contains(., 'Continue')]"
        ]
        for _ in range(8):
            clicked_c = False
            for sel in continue_selectors:
                els = driver.find_elements(By.XPATH, sel)
                for el in els:
                    if el.is_displayed():
                        physical_click_trusted(driver, el)
                        print("  ✅ 已成功点击 [Continue] 确认按钮！", flush=True)
                        clicked_c = True
                        break
                if clicked_c:
                    break
            if clicked_c:
                break
            time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️ 处理任务 2 人机验证提示: {e}", flush=True)

    time.sleep(3)
    driver.switch_to.window(lootlabs_tab)

    # 6. 领奖：CLAIM REWARD
    claim_success = False
    try:
        print("🎁 等待打勾并击发 [CLAIM REWARD]...", flush=True)
        claim_selectors = [
            "//button[contains(., 'CLAIM REWARD') or contains(., 'Claim reward') or contains(., 'Claim')]",
            "//*[contains(text(), 'CLAIM REWARD')]/ancestor::button",
            "//*[contains(text(), 'MISSION COMPLETE')]/preceding-sibling::button"
        ]
        for attempt in range(25):
            for sel in claim_selectors:
                els = driver.find_elements(By.XPATH, sel)
                for el in els:
                    is_disabled = el.get_attribute("disabled")
                    if el.is_displayed() and not is_disabled:
                        physical_click_trusted(driver, el)
                        print("  🎉 成功击发 [CLAIM REWARD] 领取积分！", flush=True)
                        claim_success = True
                        time.sleep(6)
                        break
                if claim_success:
                    break
            if claim_success:
                break
            time.sleep(1.5)
    except Exception as e:
        print(f"  ⚠️ 点击领取奖励异常: {e}", flush=True)

    # 7. 清理多余外链标签页
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
            uc_proxy = f"http://{SOCKS5_PROXY}"
            print(f"🔗 已启用代理中转: {uc_proxy}")
        except Exception as e:
            print(f"⚠️ 代理配置异常：{e}，将尝试直连。")

    chromium_args = [
        "--window-size=1920,1080",
        "--lang=zh-CN,zh"
    ]
    driver = Driver(uc=True, headless=False, proxy=uc_proxy, chromium_arg=" ".join(chromium_args))
    success_runs = 0

    try:
        login_freemc(driver)
        driver.get(REWARDS_URL)
        time.sleep(5)

        current_count, balance_before = parse_daily_limit_and_balance(driver)
        print(f"📊 初始进度: {current_count}/15 | 计划轮数: {DAILY_TARGET} | 初始余额: {balance_before}", flush=True)

        # 核心：只根据 DAILY_TARGET 循环，雷打不动跑满 5 个任务
        while success_runs < DAILY_TARGET:
            target_round = current_count + 1
            print(f"\n🚀 === 执行第 {target_round} 轮赚积分任务 ===", flush=True)
            ok = run_single_task_loop(driver, target_round)
            if ok:
                success_runs += 1
                print(f"  ✅ 第 {target_round} 轮闭环完成！", flush=True)
            else:
                print(f"  ⚠️ 第 {target_round} 轮未能领奖，稍作冷却后重试...", flush=True)

            time.sleep(6)
            driver.get(REWARDS_URL)
            time.sleep(4)
            current_count, _ = parse_daily_limit_and_balance(driver)
            print(f"  📈 最新当日累计进度: {current_count}/15", flush=True)

        driver.get(REWARDS_URL)
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


if __name__ == "__main__":
    main()
