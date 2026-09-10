#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚取积分脚本 (每日目标 5 次 + TG 通知版)
# ============================================================
import os
import time
import re
import html
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

LOGIN_URL = "https://dash.freemchosting.com/login"
DASHBOARD_URL = "https://dash.freemchosting.com/"
EARN_CREDITS_URL = "https://dash.freemchosting.com/free"

FREEMC_USER = os.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os.environ.get("FREEMC_PASS", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
DAILY_TARGET = 5  # 每天固定刷 5 次


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


def physical_click(driver, element):
    """带 isTrusted 物理指针特性的安全点击"""
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
    driver.execute_script("""
        const el = arguments[0];
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
            el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
        });
    """, element)


def login_freemc(driver):
    print("🔑 正在登录 Freemchosting...", flush=True)
    driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
    time.sleep(3)

    try:
        driver.uc_gui_click_captcha()
        time.sleep(3)
    except Exception:
        pass

    driver.type("input[type='text'], input[type='email']", FREEMC_USER)
    time.sleep(1)
    driver.type("input[type='password']", FREEMC_PASS)
    time.sleep(1)

    signin_btn = driver.find_element(By.XPATH, "//button[contains(., 'Sign in') or @type='submit']")
    physical_click(driver, signin_btn)
    time.sleep(8)
    print(f"📍 登录后当前页面: {driver.current_url}", flush=True)


def parse_daily_limit_and_balance(driver) -> tuple:
    """获取今天已完成次数和当前余额"""
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
    """单轮任务循环：Generate -> Start -> 解视频/文章 -> 过Turnstile -> Claim"""
    driver.get(EARN_CREDITS_URL)
    time.sleep(4)

    gen_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward') or contains(., 'Start reward')]")
    if not gen_btns:
        print("⚠️ 未找到 Generate/Start reward 按钮")
        return False

    main_tab = driver.current_window_handle
    physical_click(driver, gen_btns[0])
    time.sleep(3)

    start_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start reward')]")
    if start_btns and start_btns[0].is_displayed():
        physical_click(driver, start_btns[0])
        time.sleep(4)

    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    time.sleep(3)

    print("🧩 已进入 Unlockr 任务墙，开始识别子任务...", flush=True)

    # 1. 任务 1 动态识别
    try:
        body_text = driver.get_text("body")
        wait_seconds = 50
        if "180" in body_text or "browse" in body_text.lower() or "文章" in body_text:
            wait_seconds = 180
            print("  📖 识别到长文章浏览任务，等待设定为 180 秒", flush=True)
        else:
            print("  🎬 识别到视频播放任务，等待设定为 50 秒", flush=True)

        arrows = driver.find_elements(By.XPATH, "//button[.//svg or contains(@class, 'arrow')] | //*[name()='svg']")
        if arrows:
            task_arrow = driver.find_element(By.XPATH, "(//button[.//svg])[1]")
            physical_click(driver, task_arrow)
            time.sleep(3)

            if len(driver.window_handles) > 2:
                sub_tab = driver.window_handles[-1]
                driver.switch_to.window(sub_tab)
                print(f"  ⏳ 正在外链页面挂机等待 {wait_seconds} 秒...", flush=True)
                time.sleep(wait_seconds + 5)
                driver.close()
                driver.switch_to.window(driver.window_handles[-1])
            else:
                print(f"  ⏳ 原地挂机等待 {wait_seconds} 秒...", flush=True)
                time.sleep(wait_seconds + 5)
    except Exception as e:
        print(f"⚠️ 任务 1 执行异常: {e}")

    time.sleep(3)

    # 2. 任务 2 Cloudflare Turnstile
    try:
        print("🛡️ 正在处理人机验证 (Turnstile)...", flush=True)
        human_task = driver.find_element(By.XPATH, "//*[contains(text(), 'HUMAN') or contains(text(), 'human')]")
        physical_click(driver, human_task)
        time.sleep(3)

        try:
            driver.uc_gui_click_captcha()
        except Exception:
            pass

        for _ in range(10):
            continue_btns = driver.find_elements(By.XPATH, "//button[normalize-space(.)='Continue' or text()='Continue']")
            if continue_btns and continue_btns[0].is_displayed():
                physical_click(driver, continue_btns[0])
                print("  ✅ 验证通过，已点击 Continue", flush=True)
                break
            time.sleep(2)
    except Exception as e:
        print(f"⚠️ 人机验证处理异常: {e}")

    time.sleep(3)

    # 3. 领奖
    try:
        claim_btns = driver.find_elements(By.XPATH, "//button[contains(., 'CLAIM REWARD') or contains(., 'Claim')]")
        if claim_btns and claim_btns[0].is_enabled():
            physical_click(driver, claim_btns[0])
            print("🎉 成功领取奖励 (+1 credit)！", flush=True)
            time.sleep(5)
    except Exception as e:
        print(f"⚠️ 领取奖励异常: {e}")

    try:
        for handle in driver.window_handles[1:]:
            driver.switch_to.window(handle)
            driver.close()
        driver.switch_to.window(main_tab)
    except Exception:
        pass

    return True


def main():
    print("=== Freemchosting 自动化刷分任务启动 ===", flush=True)
    driver = Driver(uc=True, headless=False, window_size="1920,1080")
    success_runs = 0

    try:
        login_freemc(driver)
        driver.get(EARN_CREDITS_URL)
        time.sleep(4)

        current_count, balance_before = parse_daily_limit_and_balance(driver)
        print(f"📊 当前今日已完成次数: {current_count} / 目标: {DAILY_TARGET} 次 | 当前余额: {balance_before}", flush=True)

        if current_count >= DAILY_TARGET:
            print("ℹ️ 今日刷分目标已提前达成，无需重复执行。")
            return

        while current_count < DAILY_TARGET and success_runs < DAILY_TARGET:
            print(f"\n🚀 开始执行第 {current_count + 1} 次任务循环...", flush=True)
            ok = run_single_task_loop(driver)
            if ok:
                success_runs += 1
                time.sleep(5)
                driver.get(EARN_CREDITS_URL)
                time.sleep(3)
                current_count, _ = parse_daily_limit_and_balance(driver)
                print(f"✅ 当前最新完成次数: {current_count} / {DAILY_TARGET}", flush=True)
            else:
                print("⚠️ 本轮任务未完全成功，重试...", flush=True)
                time.sleep(10)

        # 最终状态截图与汇总通知
        driver.get(EARN_CREDITS_URL)
        time.sleep(4)
        _, balance_after = parse_daily_limit_and_balance(driver)
        driver.save_screenshot("freemc_success.png")

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        tg_send(
            f"💰 <b>Freemchosting 刷分汇总</b>\n\n"
            f"🎯 <b>达成目标：</b><code>{success_runs}</code> 次 (今日总计 {current_count}/15)\n"
            f"💳 <b>积分余额：</b><code>{html.escape(balance_before)}</code> ➜ <code><b>{html.escape(balance_after)}</b></code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="freemc_success.png",
        )
        print(f"\n🎯 今日目标已达成！成功完成 {success_runs} 次任务，TG 通知已发送。", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 整体任务异常: {err_msg}")
        try:
            driver.switch_to.default_content()
            driver.save_screenshot("freemc_error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>Freemchosting 刷分通知</b>\n\n❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="freemc_error.png",
        )
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
