#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# ACLClouds 自动登录与服务器续期脚本 (OCR 自动做题最终版)
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
from selenium.webdriver.common.action_chains import ActionChains

# 引入 OCR 模块
try:
    import ddddocr
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

BASE_URL = "https://aclclouds.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
SERVER_ID = "75e19d55"
SERVER_CONSOLE_URL = f"{BASE_URL}/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
ACL_USERNAME = os.environ.get("ACL_USERNAME", "").strip()
ACL_PASSWORD = os.environ.get("ACL_PASSWORD", "").strip()
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


def get_expire_info(driver) -> str:
    expire_info = "未知"
    try:
        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(r'(?i)(?:Time remaining|remaining|expire)[\s:]*([0-9]+\s*[jdhm]\s*(?:[0-9]+\s*[hm])?)', body_text)
        if time_match:
            expire_info = f"剩余 {time_match.group(1).strip()}"
        else:
            time_match_simple = re.search(r'(?i)\b(\d+\s*[jd]\s*(?:\d+\s*[hm])?)\b', body_text)
            if time_match_simple:
                expire_info = f"剩余 {time_match_simple.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取天数异常: {e}")
    return expire_info


def set_input_value(driver, element, value):
    try:
        element.click()
        time.sleep(0.2)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.BACKSPACE)
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


def ocr_recognize_image(image_bytes: bytes) -> str:
    if not OCR_AVAILABLE:
        return ""
    try:
        ocr = ddddocr.DdddOcr(show_ad=False)
        res = ocr.classification(image_bytes)
        return res.strip()
    except Exception as e:
        print(f"OCR 识别单图出错: {e}")
        return ""


def solve_acl_custom_captcha(driver, context_name="登录页"):
    """
    处理 ACLClouds 自研验证码：
    1. 点击复选框
    2. 若弹出 Click on xxx 题目，定位 4 个选项卡片进行 OCR 并点击匹配项
    3. 验证是否变成 Verified
    """
    print(f"  🛡️ 正在处理 [{context_name}] 的 'I am not a robot' 验证码...", flush=True)

    # 1. 物理点击复选框
    clicked = driver.execute_script("""
        const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
            const txt = (el.innerText || el.textContent || '').trim();
            return txt.includes('not a robot') && el.children.length <= 4 && el.clientHeight < 120;
        });

        if (candidates.length === 0) return false;
        
        const container = candidates[0];
        const clickable = container.querySelector('input, span, div, svg') || container;
        clickable.scrollIntoView({ block: 'center' });

        ['mouseover', 'mouseenter', 'mousedown', 'mouseup', 'click'].forEach(evtType => {
            clickable.dispatchEvent(new MouseEvent(evtType, { bubbles: true, cancelable: true, view: window }));
        });
        return true;
    """)

    if clicked:
        print(f"  👉 [{context_name}] 已派发物理事件点击验证码方框，等待响应...", flush=True)
        time.sleep(3)
    else:
        try:
            box_elem = driver.find_element(By.XPATH, "//*[contains(text(), 'not a robot')]/..")
            ActionChains(driver).move_to_element(box_elem).click().perform()
            print(f"  👉 [{context_name}] XPath 点击完成", flush=True)
            time.sleep(3)
        except Exception:
            pass

    # 2. 检测并处理二次点选答题卡
    try:
        prompt_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on') or contains(text(), 'click on')]")
        if prompt_elements and any(el.is_displayed() for el in prompt_elements):
            target_element = next(el for el in prompt_elements if el.is_displayed())
            prompt_text = target_element.text.strip()
            print(f"  🧩 发现二次验证点选题: {prompt_text}", flush=True)

            match = re.search(r'[Cc]lick on\s+([A-Za-z0-9_-]+)', prompt_text)
            if match:
                target_word = match.group(1).strip().lower()
                print(f"  🎯 目标关键字为: [{target_word}]", flush=True)

                # 精准抓取 4 个独立的选项卡片
                candidate_cards = driver.execute_script("""
                    const promptEl = arguments[0];
                    let parent = promptEl.parentElement;
                    while (parent && parent.querySelectorAll('canvas, img, div[style*="background"]').length < 4 && parent !== document.body) {
                        parent = parent.parentElement;
                    }
                    if (!parent) return [];

                    let items = Array.from(parent.querySelectorAll('canvas, img'));
                    if (items.length >= 4) return items.slice(0, 4);

                    let divs = Array.from(parent.querySelectorAll('div')).filter(d => {
                        return d.clientHeight > 20 && d.clientWidth > 40 && d.clientHeight < 150 && d.clientWidth < 300;
                    });
                    if (divs.length >= 4) return divs.slice(0, 4);

                    return [];
                """, target_element)

                if not candidate_cards or len(candidate_cards) < 4:
                    candidate_cards = driver.find_elements(By.XPATH, "//*[contains(text(), 'Click on')]/following::canvas | //*[contains(text(), 'Click on')]/following::img")
                    if len(candidate_cards) > 4:
                        candidate_cards = candidate_cards[:4]

                print(f"  🔍 成功定位到 {len(candidate_cards)} 个候选卡片，开始 OCR 识别...", flush=True)

                matched_card = None
                for idx, card in enumerate(candidate_cards):
                    if not OCR_AVAILABLE:
                        break
                    try:
                        card_png = card.screenshot_as_png
                        ocr_result = ocr_recognize_image(card_png).lower()
                        print(f"  🔍 卡片 #{idx+1} OCR 识别结果: [{ocr_result}]", flush=True)
                        
                        if target_word in ocr_result or ocr_result in target_word:
                            print(f"  ✨ 卡片 #{idx+1} 成功命中目标 [{target_word}]！", flush=True)
                            matched_card = card
                            break
                    except Exception as ocr_err:
                        print(f"  ⚠️ 卡片 #{idx+1} 识别出错: {ocr_err}")

                if matched_card:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", matched_card)
                    time.sleep(0.5)
                    try:
                        matched_card.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", matched_card)
                    print(f"  ✅ 已精准点击目标卡片: {target_word}", flush=True)
                    time.sleep(3)
                else:
                    print("  ⚠️ 4 个卡片未完全匹中，尝试点击第一个候选卡片以刷新题目", flush=True)
                    if candidate_cards:
                        driver.execute_script("arguments[0].click();", candidate_cards[0])
                        time.sleep(2)
    except Exception as e:
        print(f"  ℹ️ 点选验证阶段处理结束: {e}", flush=True)

    # 3. 验证是否变成 Verified
    for _ in range(6):
        body_str = driver.get_text("body")
        if "Verified" in body_str:
            print("  🟢 验证码已成功变为 Verified 状态！", flush=True)
            return True
        time.sleep(1)
    return False


def main():
    print("=== Python 任务初始化启动 ===", flush=True)
    if not ACL_USERNAME or not ACL_PASSWORD:
        print("❌ 未在 Secrets 中配置 ACL_USERNAME 或 ACL_PASSWORD", flush=True)
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

    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 登录
        print(f"🌐 正在打开登录页面: {LOGIN_URL} ...", flush=True)
        driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5)
        time.sleep(4)

        user_selector = "input[name='user'], input[name='username'], input[name='email'], input[type='text'], input[type='email']"
        driver.wait_for_element_visible(user_selector, timeout=25)

        user_elem = driver.find_element(By.CSS_SELECTOR, user_selector)
        set_input_value(driver, user_elem, ACL_USERNAME)
        print(f"  📝 已填入账号: {ACL_USERNAME[:3]}***", flush=True)
        time.sleep(1)

        pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[type='password']")
        set_input_value(driver, pwd_elem, ACL_PASSWORD)
        print("  📝 已填入密码", flush=True)
        time.sleep(1)

        solve_acl_custom_captcha(driver, context_name="登录页")
        time.sleep(2)

        print("🔑 正在点击 [Sign in] 按钮提交登录...", flush=True)
        submit_btn = driver.find_element(By.XPATH, "//button[contains(., 'Sign in') or contains(., 'Login') or contains(., 'Connexion') or @type='submit']")
        try:
            submit_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", submit_btn)

        for _ in range(15):
            if "/auth/login" not in driver.current_url:
                break
            time.sleep(1)

        if "/auth/login" in driver.current_url:
            driver.save_screenshot("login_failed.png")
            body_text = driver.get_text("body")
            err_hint = "登录验证失败"
            for line in body_text.split("\n"):
                line_str = line.strip()
                if line_str and any(k in line_str.lower() for k in ["invalid", "incorrect", "password", "captcha", "erreur", "mot de passe", "not found"]):
                    err_hint = line_str
                    break
            print(f"❌ 登录未成功跳转，页面提示: {err_hint}", flush=True)
            tg_send(
                f"🔴 <b>ACLClouds 登录失败</b>\n\n❌ <b>提示：</b><code>{html.escape(err_hint)}</code>",
                photo_path="login_failed.png"
            )
            return

        print(f"✅ 登录成功！当前页面: {driver.current_url}", flush=True)

        # 2. 控制台
        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(5)

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_info_before}", flush=True)

        # 3. 寻找提示栏里的 Renew 按钮
        renew_xpath = "//button[contains(., 'Renew') or contains(., 'Renouveler')]"
        renew_elements = driver.find_elements(By.XPATH, renew_xpath)
        
        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        if not renew_elements:
            print("ℹ️ 当前未到续期时间（续期开放于到期前 2 天内）", flush=True)
            driver.save_screenshot("dashboard_status.png")
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_info_before)}</code>\n"
                f"📌 <b>续期状态：</b>未到操作窗口（到期前 2 天内开放）\n"
                f"⏰ <b>巡检时间：</b><code>{now}</code>",
                photo_path="dashboard_status.png"
            )
            return

        print("👉 物理真实点击 Renew 按钮...", flush=True)
        try:
            renew_elements[0].click()
        except Exception:
            driver.execute_script("arguments[0].click();", renew_elements[0])
        time.sleep(3)

        # 4. 处理 Anti-bot confirmation 弹窗
        print("🔍 检查 Anti-bot confirmation 弹窗...", flush=True)
        solve_acl_custom_captcha(driver, context_name="Anti-bot 弹窗")

        for _ in range(8):
            dialog_open = driver.execute_script("return !!document.querySelector('div[role=\"dialog\"], .modal, .swal2-modal');")
            if not dialog_open:
                break
            time.sleep(1)

        # 5. 刷新页面检查最新天数
        driver.refresh()
        time.sleep(4)

        expire_info_after = get_expire_info(driver)
        driver.save_screenshot("final_page.png")

        tg_send(
            f"📋 <b>ACLClouds 自动续期汇总</b>\n\n"
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
            f"🔴 <b>ACLClouds 续期通知</b>\n\n❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="error.png",
        )
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
