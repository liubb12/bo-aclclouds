#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 终极自动续期脚本 (修复透明广告遮挡与三连击版)
# ============================================================
import html
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from seleniumbase import Driver

BASE_URL = "https://voer.host"
LOGIN_URL = f"{BASE_URL}/login"
SERVER_ID = os.environ.get("VOER_SERVER_ID", "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992").strip()
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

# 凭据配置：优先使用 Cookie，过期自动使用 邮箱+密码 登录刷新
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip() or os.environ.get("VOER_TOKEN", "").strip()
VOER_EMAIL = os.environ.get("VOER_EMAIL", "").strip() or os.environ.get("EMAIL", "").strip()
VOER_PASSWORD = os.environ.get("VOER_PASSWORD", "").strip() or os.environ.get("PASSWORD", "").strip()

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


def physical_click_trusted(driver, element):
    """终极霸道点击法：融合 JS 无视遮挡穿透与物理鼠标模拟"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        time.sleep(0.2)
    except Exception:
        pass
        
    # 第一层：使用 JS 强制点击，彻底无视任何透明广告遮挡层！
    try:
        driver.execute_script("arguments[0].click();", element)
    except Exception:
        pass
        
    # 第二层：如果 JS 不生效，补一发真实的物理点击
    try:
        ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
    except Exception:
        pass
        
    try:
        element.click()
    except Exception:
        pass


def dismiss_unlock_modal(driver):
    driver.switch_to.default_content()
    unlock_xpaths = [
        "//button[contains(., 'View a short ad') or contains(., '观看一则短广告')]",
        "//div[contains(text(), 'Unlock more content') or contains(text(), '解锁更多内容')]/following::button[contains(., 'View a short') or contains(., '观看一则短广告')]",
        "//*[contains(text(), 'Site-wide access') or contains(text(), '网站级访问权限')]/ancestor::button",
        "//*[contains(text(), 'View a short ad') or contains(text(), '观看一则短广告')]",
    ]
    for _ in range(2):
        for xpath in unlock_xpaths:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
                for el in elems:
                    if el.is_displayed():
                        print("  🚨 检测到 Unlock 全局拦截弹窗，准备击穿...", flush=True)
                        physical_click_trusted(driver, el)
                        time.sleep(4)
                        return
            except Exception:
                pass
        time.sleep(1)


def dismiss_pwa_popups(driver):
    try:
        btns = driver.find_elements(
            By.XPATH,
            "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' or contains(., 'Close') or contains(., 'Dismiss') or contains(., 'Accept')]"
        )
        for b in btns:
            if b.is_displayed():
                physical_click_trusted(driver, b)
                time.sleep(0.5)
    except Exception:
        pass


def restore_session_data(driver, credential_str: str, domain=".voer.host"):
    if not credential_str:
        return
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

    token_match = re.search(r"token=([^;\s]+)", cookie_str)
    token_val = token_match.group(1).strip() if token_match else cookie_str.strip()

    if cookie_str:
        parts = [c.strip() for c in cookie_str.split(";") if c.strip()]
        for part in parts:
            if "=" in part:
                name, val = part.split("=", 1)
                try:
                    driver.add_cookie({"name": name.strip(), "value": val.strip(), "domain": domain, "path": "/"})
                except Exception:
                    try:
                        driver.add_cookie({"name": name.strip(), "value": val.strip(), "path": "/"})
                    except Exception:
                        pass
        print("  🍪 Cookie 注入完成", flush=True)

    try:
        driver.execute_script(f"""
            localStorage.setItem('token', '{token_val}');
            localStorage.setItem('auth_token', '{token_val}');
            sessionStorage.setItem('token', '{token_val}');
        """)
        if storage_dict and isinstance(storage_dict, dict):
            for k, v in storage_dict.items():
                driver.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", k, str(v))
        print("  💾 LocalStorage 恢复完成", flush=True)
    except Exception as e:
        print(f"  ⚠️ LocalStorage 注入异常: {e}")


def handle_turnstile_and_login(driver, email: str, password: str) -> bool:
    if not email or not password:
        print("  ⚠️ 未配置 VOER_EMAIL / VOER_PASSWORD，无法自动执行账号密码登录。")
        return False

    print(f"  🔐 开始使用账号密码登录: {email} ...", flush=True)
    driver.get(LOGIN_URL)
    time.sleep(4)

    for _ in range(4):
        try:
            driver.uc_gui_click_captcha()
            print("  🛡️ 已触发 uc_gui_click_captcha 破盾")
            time.sleep(4)
            break
        except Exception:
            time.sleep(2)

    email_selectors = ["#login-email", 'input[name="email"]', 'input[type="email"]']
    for sel in email_selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems and elems[0].is_displayed():
                elems[0].clear()
                elems[0].send_keys(email)
                print("  📧 邮箱填写完毕")
                break
        except Exception:
            continue

    pw_selectors = ["#login-password", 'input[name="password"]', 'input[type="password"]']
    for sel in pw_selectors:
        try:
            elems = driver.find_elements(By.CSS_SELECTOR, sel)
            if elems and elems[0].is_displayed():
                elems[0].clear()
                elems[0].send_keys(password)
                print("  🔑 密码填写完毕")
                break
        except Exception:
            continue

    time.sleep(1)

    login_btn_xpaths = [
        "//button[contains(., 'Sign in') or contains(., 'Login') or contains(., '登录')]",
        "//button[@type='submit']"
    ]
    for xpath in login_btn_xpaths:
        try:
            btns = driver.find_elements(By.XPATH, xpath)
            for b in btns:
                if b.is_displayed():
                    physical_click_trusted(driver, b)
                    print("  👉 点击了登录按钮")
                    break
        except Exception:
            continue

    for _ in range(12):
        time.sleep(2)
        url = driver.current_url.lower()
        if "/login" not in url:
            print(f"  🎉 登录成功，跳转至: {driver.current_url}")
            try:
                cookies = driver.get_cookies()
                for c in cookies:
                    if c.get("name") == "token" and c.get("value"):
                        new_t = c["value"].strip()
                        print(f"  🔑 提取到全新 Token: {new_t[:10]}...")
                        gh_env = os.environ.get("GITHUB_ENV")
                        if gh_env and os.path.exists(gh_env):
                            with open(gh_env, "a") as f:
                                f.write(f"VOER_COOKIES=token={new_t}\n")
                        break
            except Exception:
                pass
            return True

    print("  ❌ 邮箱密码登录失败，仍处于登录页面。")
    return False


def get_expire_and_progress(driver) -> tuple:
    dismiss_pwa_popups(driver)
    raw_str = "未知"
    total_seconds = 0
    prog_str = "未知"
    server_status = "未知"

    try:
        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")

        status_elems = driver.find_elements(
            By.XPATH,
            "//*[contains(@class, 'badge') or contains(@class, 'status') or self::span][translate(text(), 'running', 'RUNNING')='RUNNING' or translate(text(), 'stopped', 'STOPPED')='STOPPED' or translate(text(), 'restoring', 'RESTORING')='RESTORING' or translate(text(), 'crashed', 'CRASHED')='CRASHED']"
        )
        for se in status_elems:
            txt = se.text.strip().upper()
            if txt in ("RUNNING", "STOPPED", "RESTORING", "CRASHED"):
                if txt == "RUNNING":
                    server_status = "🟢 RUNNING"
                elif txt == "STOPPED":
                    server_status = "🔴 STOPPED"
                elif txt == "RESTORING":
                    server_status = "🟠 RESTORING"
                else:
                    server_status = "💥 CRASHED"
                break

        elems = driver.find_elements(By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]")
        for elem in elems:
            txt = elem.text.strip()
            m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", txt)
            if m:
                total_seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                raw_str = f"剩余 {txt}"
                break

        pm = re.search(r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE)
        if pm:
            prog_str = pm.group(1).replace(" ", "")
        else:
            all_p = re.findall(r"(\d+\s*/\s*[1-9]\b)", body_text)
            if all_p:
                prog_str = all_p[0].replace(" ", "")

    except Exception as e:
        print(f"⚠️ 提取状态异常: {e}")

    full_status_str = f"[{server_status}] {raw_str}"
    return full_status_str, total_seconds, prog_str


def recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=4) -> bool:
    for xpath in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xpath)
            for el in elems:
                if el.is_displayed():
                    print(f"   👉 击中目标: {xpath}...", flush=True)
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


def ensure_inside_ads_modal(driver):
    """强化版三段式：防遮挡防丢失的重试确认机制"""
    driver.switch_to.default_content()
    dismiss_unlock_modal(driver)
    time.sleep(1) # 等待任何可能的遮罩动画消退

    # 0. 如果已经在最终播放器内了，直接返回
    final_modal_indicators = [
        "//*[contains(text(), 'Progress')]",
        "//*[contains(text(), 'Watch') and contains(text(), 'ads to start')]",
        "//*[contains(text(), 'Finding ad')]",
        "//*[contains(text(), 'Rewarded ad')]"
    ]
    for ind in final_modal_indicators:
        try:
            if driver.find_elements(By.XPATH, ind):
                print("  ℹ️ 最终广告播放器已就绪，等待获取广告...", flush=True)
                return True
        except Exception:
            pass

    # 包含多种可能性（兼容按钮或a标签）的绿色确认按钮
    confirm_xpaths = [
        "//button[contains(., 'Watch Ads') or contains(., 'Watch ads')]",
        "//a[contains(., 'Watch Ads') or contains(., 'Watch ads')]",
        "//*[contains(text(), 'Watch Ads') or contains(text(), 'Watch ads')]"
    ]

    def try_click_confirm_modal():
        for cx in confirm_xpaths:
            try:
                c_btns = driver.find_elements(By.XPATH, cx)
                for cb in c_btns:
                    if cb.is_displayed():
                        print("  ℹ️ 成功捕捉到中间确认弹窗，点击 [✓ Watch Ads] 进入播放器...", flush=True)
                        physical_click_trusted(driver, cb)
                        time.sleep(4)
                        return True
            except Exception:
                pass
        return False

    # 1. 尝试直接点击现成的绿色确认弹窗
    if try_click_confirm_modal():
        return True

    # 2. 如果没弹窗，就去点击 [+ Extend]
    try:
        extend_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Extend') and not(@disabled)]")
        if extend_btns:
            for eb in extend_btns:
                if eb.is_displayed():
                    print("  ℹ️ 点击面板上的 [+ Extend] 触发续期...", flush=True)
                    # 【核心修复】：增加三连击机制。点一次，等5秒，如果不出来，再点！防止点击被透明遮罩吃掉！
                    for attempt in range(3):
                        physical_click_trusted(driver, eb)
                        print(f"  ⏳ 正在等待确认弹窗加载 (尝试 {attempt+1}/3)...", flush=True)
                        for _ in range(5):
                            time.sleep(1)
                            if try_click_confirm_modal():
                                return True
                    print("  ⚠️ 三次尝试点击 [+ Extend] 均未见弹窗，可能被严重遮挡！", flush=True)
                    return False
    except Exception:
        pass

    # 3. 如果是关机状态，尝试点击 Start 唤醒
    try:
        start_btns = driver.find_elements(
            By.XPATH,
            "//button[(contains(., 'Start') or contains(., '开始') or contains(., 'Recover')) and not(@disabled)]"
        )
        if start_btns:
            for sb in start_btns:
                if sb.is_displayed():
                    print(f"  ℹ️ 服务器离线，点击 [{sb.text.strip()}] 唤醒控制台...", flush=True)
                    for attempt in range(3):
                        physical_click_trusted(driver, sb)
                        print(f"  ⏳ 正在等待确认弹窗加载 (尝试 {attempt+1}/3)...", flush=True)
                        for _ in range(5):
                            time.sleep(1)
                            if try_click_confirm_modal():
                                return True
                    return False
    except Exception:
        pass


def click_watch_ad_everywhere(driver) -> bool:
    """仅在最终的广告播放器中寻找真正的【看广告】按钮"""
    xpaths = [
        "//button[normalize-space(.)='Watch ad' or text()='Watch ad']",
        "//button[contains(translate(., 'AD', 'ad'), 'watch ad')]",
        "//div[contains(., 'Rewarded ad')]//button[contains(., 'Watch')]",
        "//*[contains(text(), 'Ready for Voer')]"
    ]
    driver.switch_to.default_content()
    return recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=3)


def click_close_by_coordinates(driver) -> bool:
    try:
        driver.switch_to.default_content()
        ad_card = driver.execute_script("""
            const iframes = Array.from(document.querySelectorAll('iframe'));
            for (let f of iframes) {
                const rect = f.getBoundingClientRect();
                if (rect.width > 250 && rect.height > 200 && rect.top > 50) {
                    return f;
                }
            }
            return null;
        """)
        if ad_card:
            ActionChains(driver).move_to_element_with_offset(
                ad_card, int(ad_card.size["width"] / 2 - 10), -12
            ).click().perform()
            print("  🎯 执行右上角物理坐标打击！", flush=True)
            return True
    except Exception:
        pass
    return False


def handle_sound_and_close_ad(driver, max_wait_sec=65) -> bool:
    print("  ⏳ 正在监控广告生命周期...", flush=True)
    start_time = time.time()

    continue_xpaths = [
        "//button[normalize-space(.)='Continue' or text()='Continue']",
        "//*[@id='continue-button']"
    ]
    close_xpaths = [
        "//*[normalize-space(.)='Close' or text()='Close']",
        "//*[translate(text(), 'CLOSE', 'close')='close']",
        "//button[contains(., 'Close') or @aria-label='Close']",
        "//div[@id='dismiss-button' or @aria-label='Close ad']",
        "//*[@id='close-button']"
    ]

    has_sound_continued = False
    while time.time() - start_time < max_wait_sec:
        elapsed = time.time() - start_time
        if not has_sound_continued and elapsed < 15:
            driver.switch_to.default_content()
            if recursive_find_and_click(driver, continue_xpaths, current_depth=0, max_depth=4):
                print("  🎉 点击声音遮罩 [Continue] 成功！", flush=True)
                has_sound_continued = True

        if elapsed < 30:
            time.sleep(2)
            continue

        driver.switch_to.default_content()
        if recursive_find_and_click(driver, close_xpaths, current_depth=0, max_depth=4):
            print("  🎯 成功命中并关闭广告 [Close]！", flush=True)
            time.sleep(3)
            return True

        if elapsed > 40:
            if click_close_by_coordinates(driver):
                time.sleep(2)
                return True

        time.sleep(1.5)

    driver.switch_to.default_content()
    return False


def main():
    print("=== VOER 终极自动续期任务初始化 ===", flush=True)

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
        "--window-size=1920,1080",
    ]
    driver = Driver(uc=True, headless=False, proxy=uc_proxy, chromium_arg=" ".join(chromium_args))

    try:
        logged_in = False
        if VOER_COOKIES:
            print("🔑 执行现有会话注入恢复...", flush=True)
            driver.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
            time.sleep(2)
            restore_session_data(driver, VOER_COOKIES)
            time.sleep(1)
            driver.get(SERVER_CONSOLE_URL)
            time.sleep(8)
            dismiss_pwa_popups(driver)

            if "/login" not in driver.current_url.lower():
                print("✅ 会话凭据依然有效，直接进入控制台！", flush=True)
                logged_in = True
            else:
                print("⚠️ 现有会话已失效，被重定向至登录页，准备降级尝试账号密码登录...", flush=True)

        if not logged_in:
            if not handle_turnstile_and_login(driver, VOER_EMAIL, VOER_PASSWORD):
                print("❌ 所有登录途径均失败，退出任务。", flush=True)
                driver.save_screenshot("login_failed.png")
                tg_send("🔴 <b>VOER 续期失败</b>\n\n会话失效且账号密码登录未通过人机验证。", photo_path="login_failed.png")
                return
            driver.get(SERVER_CONSOLE_URL)
            time.sleep(8)
            dismiss_pwa_popups(driver)

        expire_info_before, init_sec, init_prog = get_expire_and_progress(driver)
        print(f"⏳ 初始服务器状态: {expire_info_before} | 今日进度: {init_prog}", flush=True)

        if init_sec > 12600:
            print(f"💡 剩余时间充裕（约 {round(init_sec / 3600, 1)} 小时），跳过看广告。", flush=True)
            return

        completed = 0
        for current_ad in range(1, 5):
            print(f"\n🎬 === 正在执行第 {current_ad}/4 轮广告 ===", flush=True)
            
            # 使用穿透点击与重试的确保弹窗逻辑
            ensure_inside_ads_modal(driver)

            clicked = False
            for sec in range(35):
                if click_watch_ad_everywhere(driver):
                    print(f"  🎯 第 {sec + 1} 秒击发第 {current_ad} 轮 [Watch ad]！", flush=True)
                    clicked = True
                    break
                time.sleep(1)

            if not clicked:
                driver.switch_to.default_content()
                driver.save_screenshot(f"stuck_round_{current_ad}.png")
                print(f"  ⚠️ 未能出现第 {current_ad} 轮的 [Watch ad] 按钮", flush=True)
                break

            time.sleep(2)
            handle_sound_and_close_ad(driver, max_wait_sec=65)
            completed += 1
            print(f"  ✅ 第 {current_ad} 个广告展示完毕！", flush=True)
            time.sleep(3)

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        driver.switch_to.default_content()
        final_close_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Close') or contains(., 'Done')]")
        for b in final_close_btns:
            if b.is_displayed():
                physical_click_trusted(driver, b)
                time.sleep(1)

        print("\n⏳ 等待 30 秒后台同步数据...", flush=True)
        time.sleep(30)
        driver.switch_to.default_content()
        driver.refresh()
        time.sleep(8)
        dismiss_pwa_popups(driver)
        dismiss_unlock_modal(driver)

        expire_info_after, _, final_prog = get_expire_and_progress(driver)
        driver.save_screenshot("final_success.png")

        tg_send(
            f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
            f"🎬 <b>观看广告：</b><code>{completed}/4</code> 轮\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"📊 <b>今日进度：</b><code>{html.escape(final_prog)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_success.png",
        )
        print(f"\n✅ 任务圆满完成，最新状态: {expire_info_after} | 额度: {final_prog}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.switch_to.default_content()
            driver.save_screenshot("error.png")
        except Exception:
            pass
        tg_send(f"🔴 <b>VOER Host 续期异常</b>\n\n<code>{html.escape(err_msg)}</code>", photo_path="error.png")
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
