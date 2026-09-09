#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 自动续期脚本 (多层嵌套 iframe 递归穿透 + 声音弹窗破解版)
# ============================================================
import os
import re
import json
import html
import time
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains

BASE_URL = "https://voer.host"
SERVER_ID = "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992"
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
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


def restore_session_data(driver, credential_str: str, domain=".voer.host"):
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

    if cookie_str:
        parts = [c.strip() for c in cookie_str.split(";") if c.strip()]
        for part in parts:
            if "=" in part:
                name, val = part.split("=", 1)
                try:
                    driver.add_cookie({
                        "name": name.strip(),
                        "value": val.strip(),
                        "domain": domain,
                        "path": "/"
                    })
                except Exception:
                    try:
                        driver.add_cookie({"name": name.strip(), "value": val.strip(), "path": "/"})
                    except Exception:
                        pass
        print("  🍪 Cookie 注入完成", flush=True)

    if storage_dict and isinstance(storage_dict, dict):
        for k, v in storage_dict.items():
            driver.execute_script("window.localStorage.setItem(arguments[0], arguments[1]);", k, str(v))
        print("  💾 LocalStorage 恢复完成", flush=True)


def get_expire_info(driver) -> str:
    dismiss_pwa_popups(driver)
    try:
        elems = driver.find_elements(By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]")
        for elem in elems:
            txt = elem.text.strip()
            if re.match(r'^\d{1,2}:\d{2}:\d{2}$', txt):
                return f"剩余 {txt}"

        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(r'(?i)(?:Time Remaining|remaining|expire)[\s:]*([0-9]+:[0-9]+:[0-9]+)', body_text)
        if time_match:
            return f"剩余 {time_match.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取时间异常: {e}")
    return "未知"


def force_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        time.sleep(0.2)
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
        try:
            ActionChains(driver).move_to_element(element).pause(0.2).click().perform()
        except Exception:
            try:
                element.click()
            except Exception:
                driver.execute_script("arguments[0].click();", element)


def recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=4) -> bool:
    """递归深入所有嵌套 iframe 查找并点击匹配的元素"""
    # 1. 检查当前 context 内部
    for xpath in xpaths:
        try:
            elems = driver.find_elements(By.XPATH, xpath)
            for el in elems:
                if el.is_displayed():
                    print(f"  👉 在深度 {current_depth} 捕获目标: {xpath}，执行点击...", flush=True)
                    force_click(driver, el)
                    return True
        except Exception:
            pass

    if current_depth >= max_depth:
        return False

    # 2. 递归遍历子 iframe
    try:
        sub_frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        sub_frames = []

    for idx in range(len(sub_frames)):
        try:
            # 重新获取避免 DOM 过期
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


def click_watch_ad_everywhere(driver) -> bool:
    """全域穿透定位并点击 Watch ad 绿色按钮"""
    xpaths = [
        "//button[normalize-space(.)='Watch ad' or text()='Watch ad']",
        "//button[contains(translate(., 'AD', 'ad'), 'watch ad')]",
        "//div[contains(., 'Rewarded ad')]//button[contains(., 'Watch')]"
    ]
    driver.switch_to.default_content()
    return recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=3)


def handle_sound_and_close_ad(driver, max_wait_sec=55) -> bool:
    """穿透多层 iframe 处理声音 Continue 弹窗，并等待 Close 按钮点击"""
    print(f"  ⏳ 正在监控广告流程 (深度穿透声音弹窗及 Close, 最长 {max_wait_sec} 秒)...", flush=True)
    start_time = time.time()

    continue_xpaths = [
        "//button[normalize-space(.)='Continue' or contains(., 'Continue')]",
        "//*[text()='Continue' or contains(text(), 'Continue')]",
        "//div[contains(text(), 'play with sound')]/following::button[contains(., 'Continue')]",
        "//*[@id='continue-button']"
    ]

    close_xpaths = [
        "//*[text()='Close' or translate(text(), 'CLOSE', 'close')='close' or @aria-label='Close' or @id='dismiss-button']",
        "//button[contains(., 'Close')]",
        "//*[@id='close-button']"
    ]

    while time.time() - start_time < max_wait_sec:
        # A. 深入所有嵌套 iframe 点击 Continue
        driver.switch_to.default_content()
        c_clicked = recursive_find_and_click(driver, continue_xpaths, current_depth=0, max_depth=4)
        if c_clicked:
            print("  🎉 成功穿透并击发声音确认弹窗 [Continue]！广告正式起播...", flush=True)
            time.sleep(2)

        # B. 检查并点击广告的 Close 按钮
        driver.switch_to.default_content()
        closed = recursive_find_and_click(driver, close_xpaths, current_depth=0, max_depth=4)
        if closed:
            print("  🎉 成功击发广告 [Close] 按钮！", flush=True)
            driver.switch_to.default_content()
            time.sleep(2)
            return True

        time.sleep(2)

    driver.switch_to.default_content()
    print("  ℹ️ 广告展示超时轮候结束", flush=True)
    return True


def main():
    print("=== Python 任务初始化启动 ===", flush=True)

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

    try:
        # 1. 会话恢复直登
        print("🔑 执行会话注入恢复...", flush=True)
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
        time.sleep(2)
        restore_session_data(driver, VOER_COOKIES)
        time.sleep(1)

        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(6)
        dismiss_pwa_popups(driver)

        if "/login" in driver.current_url.lower():
            print("❌ 会话已失效，请重新更新 VOER_COOKIES", flush=True)
            return

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_info_before}", flush=True)

        # 2. 唤起 Extend 弹窗
        modal_opened = False
        watch_ads_xpath = "//button[contains(., 'Watch Ads') or contains(., 'Watch ad')]"

        for attempt in range(1, 4):
            print(f"👉 正在尝试第 {attempt} 次点击 [+ Extend] 唤起弹窗...", flush=True)
            extend_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Extend')]")
            if not extend_btns:
                print("ℹ️ 未发现 Extend 按钮，可能次数已满", flush=True)
                return

            force_click(driver, extend_btns[0])
            time.sleep(2)

            confirm_btns = driver.find_elements(By.XPATH, watch_ads_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🎉 成功唤起 Extend Session 弹窗！", flush=True)
                modal_opened = True
                break
            time.sleep(1)

        if not modal_opened:
            driver.save_screenshot("failed_extend_modal.png")
            print("❌ 未能成功弹出 Extend Session 对话框", flush=True)
            tg_send("🔴 <b>VOER Host 未能唤起续期弹窗</b>", photo_path="failed_extend_modal.png")
            return

        # 3. 点击绿色确认按钮 [✓ Watch Ads] 激活看广告模态框
        watch_btn_target = driver.find_element(By.XPATH, watch_ads_xpath)
        print("👉 物理点击确认 [Watch Ads] 启动看广告模态框...", flush=True)
        force_click(driver, watch_btn_target)
        time.sleep(3)

        # 4. 在全屏模态框内依次观看 3 个激励广告
        completed = 0
        stuck_screenshot = None

        for current_ad in range(1, 4):
            print(f"\n🎬 === 正在准备第 {current_ad}/3 个广告 ===", flush=True)

            clicked = False
            for sec in range(35):
                if click_watch_ad_everywhere(driver):
                    print(f"  🎯 第 {sec + 1} 秒成功捕获并点击第 {current_ad} 轮的 [Watch ad] 按钮！", flush=True)
                    clicked = True
                    break
                time.sleep(1)

            if not clicked:
                stuck_screenshot = f"stuck_round_{current_ad}.png"
                driver.switch_to.default_content()
                driver.save_screenshot(stuck_screenshot)
                print(f"  ⚠️ 未能在模态框内等到第 {current_ad} 轮的 [Watch ad] 按钮，已抓取现场截图", flush=True)
                break

            time.sleep(3)
            # 穿透处理声音 Continue 弹窗并等待 Close 按钮
            handle_sound_and_close_ad(driver, max_wait_sec=55)
            completed += 1
            print(f"  ✅ 第 {current_ad} 个广告观看完成！", flush=True)
            time.sleep(4)

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 5. 若未完整看完 3 轮，发 TG 预警
        if completed < 3 and stuck_screenshot:
            tg_send(
                f"⚠️ <b>VOER Host 看广告流程中断</b>\n\n"
                f"🎬 <b>已完成：</b><code>{completed}/3</code> 轮\n"
                f"⏳ <b>当前时长：</b><code>{html.escape(expire_info_before)}</code>\n"
                f"📌 <b>原因：</b>广告流程超时，附带中断现场截图\n"
                f"⏰ <b>时间：</b><code>{now}</code>",
                photo_path=stuck_screenshot
            )
            return

        # 6. 全部完成：刷新并汇总
        print("\n⏳ 3 轮广告全部看完，等待 8 秒后端落库后刷新页面...", flush=True)
        time.sleep(8)
        driver.switch_to.default_content()
        driver.refresh()
        time.sleep(5)
        dismiss_pwa_popups(driver)

        expire_info_after = get_expire_info(driver)
        driver.save_screenshot("final_success.png")

        tg_send(
            f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
            f"🎬 <b>观看广告：</b><code>{completed}/3</code> 轮 (满载续期)\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_success.png",
        )
        print(f"\n✅ 任务执行完毕，最新状态: {expire_info_after}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.switch_to.default_content()
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
