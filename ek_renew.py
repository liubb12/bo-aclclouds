#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动登录与服务器续期脚本 (高拟真真人行为模拟版)
# ============================================================
import os
import re
import html
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

BASE_URL = "https://dash.eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_EMAIL = os.environ.get("EK_EMAIL", "").strip()
EK_USERNAME = os.environ.get("EK_USERNAME", "").strip()
EK_PASSWORD = os.environ.get("EK_PASSWORD", "").strip()


def human_sleep(min_s=1.0, max_s=2.5):
    """真人操作间的随机呼吸停顿"""
    time.sleep(random.uniform(min_s, max_s))


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
        print("  ✅ TG 通知发送成功")
    except Exception as e:
        print(f"  ⚠️ TG 通知异常: {e}")


def human_type(driver, element, text: str):
    """模拟真人打字：物理聚焦、随机击键延时、分发输入事件"""
    try:
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
        human_sleep(0.2, 0.4)
        element.send_keys(Keys.CONTROL, "a")
        human_sleep(0.1, 0.2)
        element.send_keys(Keys.BACKSPACE)
        human_sleep(0.1, 0.3)

        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.06, 0.16))

        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
        human_sleep(0.3, 0.6)
    except Exception:
        pass


def human_click(driver, element):
    """模拟真人鼠标移动到目标元素并点击"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.3, 0.6)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.15, 0.35)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def random_mouse_wander(driver):
    """在页面空白区域随机滑动，模拟用户视线观察"""
    try:
        driver.execute_script(f"window.scrollBy({{top: {random.randint(-120, 180)}, behavior: 'smooth'}});")
        human_sleep(0.5, 1.2)
    except Exception:
        pass


def solve_turnstile(driver, context_name="未知环节", max_wait=15):
    """模拟真人应对 Cloudflare Turnstile 验证框"""
    print(f"  🛡️ 检查 [{context_name}] 是否存在 Cloudflare Turnstile 验证...", flush=True)
    human_sleep(1.5, 2.5)

    # 1. 优先调用 SeleniumBase 原生 CAPTCHA 穿透
    try:
        driver.uc_gui_click_captcha()
        print(f"  👉 [{context_name}] 已调用 SeleniumBase 原生 CAPTCHA 穿透", flush=True)
        human_sleep(2.0, 3.5)
    except Exception:
        pass

    # 2. 模拟真人鼠标对 iframe 复选框的物理点击
    try:
        driver.execute_script("""
            const iframes = document.querySelectorAll('iframe[src*="cloudflare"], iframe[src*="turnstile"], iframe[src*="challenges"]');
            iframes.forEach(f => {
                f.scrollIntoView({behavior: 'smooth', block: 'center'});
                const rect = f.getBoundingClientRect();
                const evt = new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    clientX: rect.left + 25 + Math.floor(Math.random() * 8),
                    clientY: rect.top + 25 + Math.floor(Math.random() * 8)
                });
                f.dispatchEvent(evt);
            });
        """)
    except Exception:
        pass

    # 3. 轮询检测是否生成有效 Token
    start = time.time()
    while time.time() - start < max_wait:
        has_token = driver.execute_script("""
            const input = document.querySelector('input[name="cf-turnstile-response"], [name="cf_challenge_response"]');
            return !!(input && input.value);
        """)
        if has_token:
            print(f"  🟢 [{context_name}] Turnstile 验证已成功通过！", flush=True)
            human_sleep(1.0, 1.8)
            return True
        time.sleep(1)

    print(f"  ℹ️ [{context_name}] 未检测到显式阻止或已静默放行", flush=True)
    return False


def get_servers_info(driver):
    """提取页面所有服务器卡片的信息"""
    info = []
    try:
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and .//button[contains(., 'RENOVAR')]]")
        for c in cards:
            text = c.text
            match = re.search(r'Expira\s+([0-9A-Za-z\s]+)', text)
            exp_date = match.group(1).strip() if match else "未知"
            lines = text.split("\n")
            name = lines[0].strip() if lines else "Server"
            info.append(f"• <b>{name}</b>: 到期时间 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "状态获取成功"


def main():
    print("=== EKNodes 自动续期任务启动 (真人拟真版) ===", flush=True)
    if not (EK_EMAIL or EK_USERNAME) or not EK_PASSWORD:
        print("❌ 未在 Secrets 中配置账号或密码 (EK_EMAIL / EK_USERNAME / EK_PASSWORD)", flush=True)
        return

    driver = Driver(uc=True, headless=False)

    try:
        # 1. 模拟真人访问登录页
        print(f"🌐 正在访问登录页: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
        human_sleep(3.0, 4.5)
        random_mouse_wander(driver)

        if "/servers" not in driver.current_url:
            text_inputs = driver.find_elements(By.CSS_SELECTOR, "input:not([type='password']):not([type='checkbox']):not([type='hidden'])")
            
            if len(text_inputs) >= 2 and EK_EMAIL and EK_USERNAME:
                print("  📝 检测到双输入框，拟真依次填入邮箱与用户名...", flush=True)
                human_type(driver, text_inputs[0], EK_EMAIL)
                human_sleep(0.6, 1.2)
                human_type(driver, text_inputs[1], EK_USERNAME)
            elif len(text_inputs) >= 1:
                account_val = EK_EMAIL if EK_EMAIL else EK_USERNAME
                masked_acc = account_val[:3] + "***" if len(account_val) > 3 else "***"
                print(f"  📝 拟真填入登录账号: {masked_acc}", flush=True)
                human_type(driver, text_inputs[0], account_val)
            else:
                raise RuntimeError("未能找到登录账号输入框！")

            human_sleep(0.8, 1.5)

            # 拟真填入密码
            pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            print("  📝 拟真填入密码...", flush=True)
            human_type(driver, pwd_elem, EK_PASSWORD)
            human_sleep(0.8, 1.6)

            # 验证码穿透
            solve_turnstile(driver, context_name="登录表单")
            human_sleep(1.0, 2.0)

            # 拟真移动并点击登录按钮
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'Iniciar') or contains(., 'Login') or contains(., 'Entrar')]")
            print("🔑 拟真点击登录提交按钮...", flush=True)
            human_click(driver, submit_btn)

            for _ in range(15):
                if "/login" not in driver.current_url:
                    break
                time.sleep(1)

            if "/login" in driver.current_url:
                driver.save_screenshot("ek_login_fail.png")
                raise RuntimeError("登录未跳转，请检查账号密码或风控拦截")

            print(f"✅ 登录成功！当前 URL: {driver.current_url}", flush=True)

        # 2. 访问服务器控制台
        if "/servers" not in driver.current_url:
            human_sleep(1.5, 2.5)
            driver.get(SERVERS_URL)
            human_sleep(3.0, 4.5)

        random_mouse_wander(driver)
        status_before = get_servers_info(driver)
        print(f"📊 续期前服务器状态:\n{status_before}", flush=True)

        # 3. 查找 RENOVAR 按钮
        renovar_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'RENOVAR')]")
        if not renovar_buttons:
            print("ℹ️ 当前未找到 RENOVAR 按钮或尚未到期。", flush=True)
            driver.save_screenshot("ek_no_button.png")
            return

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 拟真点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(2.5, 3.8)

            # 处理弹窗验证码
            solve_turnstile(driver, context_name=f"服务器#{idx+1} 续期弹窗")
            human_sleep(1.5, 2.5)

            # 拟真确认续期
            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btn = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btn and confirm_btn[0].is_displayed():
                print("  🚀 拟真点击 [CONFIRMAR RENOVACIÓN]...", flush=True)
                human_click(driver, confirm_btn[0])
                human_sleep(3.5, 5.0)

        # 4. 刷新获取最新状态并推送通知
        driver.refresh()
        human_sleep(4.0, 6.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        tg_send(
            f"📋 <b>EKNodes 服务器自动续期汇总</b>\n\n"
            f"<b>续期前：</b>\n{status_before}\n\n"
            f"<b>续期后：</b>\n{status_after}\n\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部操作已完成！", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 执行异常: {err}", flush=True)
        try:
            driver.save_screenshot("ek_error.png")
            tg_send(f"🔴 <b>EKNodes 续期异常</b>\n\n<code>{html.escape(err)}</code>", photo_path="ek_error.png")
        except Exception:
            pass
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
