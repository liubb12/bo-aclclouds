#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# Freemchosting 自动赚取积分脚本 (每日目标 5 次版本)
# ============================================================
os_env = __import__('os')
import time
import re
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

LOGIN_URL = "https://dash.freemchosting.com/login"
DASHBOARD_URL = "https://dash.freemchosting.com/"
EARN_CREDITS_URL = "https://dash.freemchosting.com/free"

FREEMC_USER = os_env.environ.get("FREEMC_USER", "").strip()
FREEMC_PASS = os_env.environ.get("FREEMC_PASS", "").strip()
DAILY_TARGET = 5  # 每天固定刷 5 次


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


def parse_daily_limit(driver) -> int:
    """获取今天已经刷了多少次"""
    try:
        body_text = driver.get_text("body")
        # 匹配类似 3 / 15 的格式
        m = re.search(r"(\d+)\s*/\s*15", body_text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


def run_single_task_loop(driver) -> bool:
    """单轮任务循环：Generate -> Start -> 解视频/文章 -> 过Turnstile -> Claim"""
    driver.get(EARN_CREDITS_URL)
    time.sleep(4)

    # 1. 点击 Generate reward / Start reward
    gen_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Generate reward') or contains(., 'Start reward')]")
    if not gen_btns:
        print("⚠️ 未找到 Generate/Start reward 按钮")
        return False

    main_tab = driver.current_window_handle
    physical_click(driver, gen_btns[0])
    time.sleep(3)

    # 再次检查是否有 Start reward 按钮需要点第二下
    start_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start reward')]")
    if start_btns and start_btns[0].is_displayed():
        physical_click(driver, start_btns[0])
        time.sleep(4)

    # 2. 切换到弹出的 Unlockr 任务墙标签页
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    time.sleep(3)

    print("🧩 已进入 Unlockr 任务墙，开始识别子任务...", flush=True)

    # 3. 处理任务 1：动态识别视频(50s)或文章(180s)
    try:
        body_text = driver.get_text("body")
        wait_seconds = 50  # 默认兜底
        if "180" in body_text or "browse" in body_text.lower() or "文章" in body_text:
            wait_seconds = 180
            print("  📖 识别到长文章浏览任务，等待设定为 180 秒", flush=True)
        else:
            print("  🎬 识别到视频播放任务，等待设定为 50 秒", flush=True)

        # 点击任务右侧的箭头按钮进入
        arrows = driver.find_elements(By.XPATH, "//button[.//svg or contains(@class, 'arrow')] | //*[name()='svg']")
        if arrows:
            # 点击第一个未完成的任务箭头
            task_arrow = driver.find_element(By.XPATH, "(//button[.//svg])[1]")
            physical_click(driver, task_arrow)
            time.sleep(3)

            # 如果弹出了新的外链标签页，切换过去等待
            if len(driver.window_handles) > 2:
                sub_tab = driver.window_handles[-1]
                driver.switch_to.window(sub_tab)
                print(f"  ⏳ 正在外链页面挂机等待 {wait_seconds} 秒...", flush=True)
                time.sleep(wait_seconds + 5)
                driver.close() # 关闭外链页
                driver.switch_to.window(driver.window_handles[-1]) # 切回 Unlockr
            else:
                print(f"  ⏳ 原地挂机等待 {wait_seconds} 秒...", flush=True)
                time.sleep(wait_seconds + 5)
    except Exception as e:
        print(f"⚠️ 任务 1 执行异常: {e}")

    time.sleep(3)

    # 4. 处理任务 2：Cloudflare Turnstile 人机验证
    try:
        print("🛡️ 正在处理人机验证 (Turnstile)...", flush=True)
        # 点击验证任务展开
        human_task = driver.find_element(By.XPATH, "//*[contains(text(), 'HUMAN') or contains(text(), 'human')]")
        physical_click(driver, human_task)
        time.sleep(3)

        # 尝试穿透 Cloudflare 验证框
        try:
            driver.uc_gui_click_captcha()
        except Exception:
            pass

        # 等待验证通过并点击 Continue 按钮
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

    # 5. 点击底部的 CLAIM REWARD
    try:
        claim_btns = driver.find_elements(By.XPATH, "//button[contains(., 'CLAIM REWARD') or contains(., 'Claim')]")
        if claim_btns and claim_btns[0].is_enabled():
            physical_click(driver, claim_btns[0])
            print("🎉 成功领取奖励 (+1 credit)！", flush=True)
            time.sleep(5)
    except Exception as e:
        print(f"⚠️ 领取奖励异常: {e}")

    # 6. 清理多余标签页，切回主控制台
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

    try:
        login_freemc(driver)
        driver.get(EARN_CREDITS_URL)
        time.sleep(4)

        current_count = parse_daily_limit(driver)
        print(f"📊 当前今日已完成次数: {current_count} / 目标: {DAILY_TARGET} 次", flush=True)

        success_runs = 0
        while current_count < DAILY_TARGET and success_runs < DAILY_TARGET:
            print(f"\n🚀 开始执行第 {current_count + 1} 次任务循环...", flush=True)
            ok = run_single_task_loop(driver)
            if ok:
                success_runs += 1
                time.sleep(5)
                driver.get(EARN_CREDITS_URL)
                time.sleep(3)
                current_count = parse_daily_limit(driver)
                print(f"✅ 当前最新完成次数: {current_count} / {DAILY_TARGET}", flush=True)
            else:
                print("⚠️ 本轮任务未完全成功，重试...", flush=True)
                time.sleep(10)

        print(f"\n🎯 今日目标已达成！成功完成 {success_runs} 次任务，收工退出。", flush=True)
        driver.save_screenshot("earn_success.png")

    except Exception as e:
        print(f"❌ 整体任务异常: {e}")
        driver.save_screenshot("earn_error.png")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
