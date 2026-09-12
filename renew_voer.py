#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 自动续期与离线开机脚本 (修复版 / CI 兼容)
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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = "https://voer.host"
SERVER_ID = os.environ.get("VOER_SERVER_ID", "").strip()
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()

IS_CI = (
    os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    or os.environ.get("CI", "").lower() == "true"
)


# ============================================================
# 通知
# ============================================================
def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN / TG_CHAT_ID,跳过通知。")
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


# ============================================================
# 代理
# ============================================================
def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_value.startswith(prefix):
            proxy_value = proxy_value[len(prefix):]
            break
    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误,应为 host:port 或 user:pass@host:port。")
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
        raise RuntimeError("gost 启动失败,请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动,本地代理端口:{LOCAL_HTTP_PORT}")
    return proc


# ============================================================
# 通用辅助
# ============================================================
def get_body_text(driver) -> str:
    """用 JS 拿 innerText,比 driver.get_text('body') 快很多。"""
    try:
        txt = driver.execute_script(
            "return document.body ? (document.body.innerText || '') : '';"
        )
    except Exception:
        txt = ""
    return (txt or "").replace("\u00a0", " ").replace("\u202f", " ")


def physical_click_trusted(driver, element):
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element
        )
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


# ============================================================
# 弹窗处理
# ============================================================
_unlock_scan_cache = {"last": 0.0}


def dismiss_unlock_modal(driver):
    """Unlock 全局拦截弹窗。3 秒节流,避免日志刷屏。"""
    now = time.time()
    if now - _unlock_scan_cache["last"] < 3:
        return
    _unlock_scan_cache["last"] = now

    print("  🔎 扫描 Unlock 拦截弹窗...", flush=True)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    unlock_xpaths = [
        "//button[contains(., 'View a short ad') or contains(., '观看一则短广告')]",
        "//div[contains(text(), 'Unlock more content') or contains(text(), '解锁更多内容')]/following::button[contains(., 'View a short') or contains(., '观看一则短广告')]",
        "//*[contains(text(), 'Site-wide access') or contains(text(), '网站级访问权限')]/ancestor::button",
        "//*[contains(text(), 'View a short ad') or contains(text(), '观看一则短广告')]",
    ]

    for attempt in range(2):
        for xpath in unlock_xpaths:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
                for el in elems:
                    if el.is_displayed():
                        print("  🚨 检测到 Unlock 全局拦截弹窗,准备击穿...", flush=True)
                        physical_click_trusted(driver, el)
                        print("  💥 已成功点击 [View a short ad / 观看一则短广告] 按钮!", flush=True)
                        time.sleep(4)
                        return
            except Exception:
                pass
        time.sleep(1)


def dismiss_pwa_popups(driver):
    try:
        btns = driver.find_elements(
            By.XPATH,
            "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close' "
            "or contains(., 'Close') or contains(., 'Dismiss') or contains(., 'Accept')]"
        )
        for b in btns:
            if b.is_displayed():
                physical_click_trusted(driver, b)
                time.sleep(0.5)
    except Exception:
        pass


# ============================================================
# 会话注入
# ============================================================
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

    token_match = re.search(r"token=([^;\s]+)", cookie_str)
    token_val = token_match.group(1).strip() if token_match else cookie_str.strip()

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
                        "path": "/",
                    })
                except Exception:
                    try:
                        driver.add_cookie(
                            {"name": name.strip(), "value": val.strip(), "path": "/"}
                        )
                    except Exception:
                        pass
        print("  🍪 Cookie 注入完成", flush=True)

    # ⚠️ 用 arguments 传参,避免 token 中的引号/换行破坏 JS
    try:
        driver.execute_script(
            "window.localStorage.setItem('token', arguments[0]);"
            "window.localStorage.setItem('auth_token', arguments[0]);"
            "window.sessionStorage.setItem('token', arguments[0]);",
            token_val,
        )
        if isinstance(storage_dict, dict):
            for k, v in storage_dict.items():
                driver.execute_script(
                    "window.localStorage.setItem(arguments[0], arguments[1]);", k, str(v)
                )
        print("  💾 LocalStorage 恢复完成", flush=True)
    except Exception as e:
        print(f"  ⚠️ LocalStorage 注入异常: {e}")


# ============================================================
# 状态抓取
# ============================================================
def _extract_server_status(driver, body_text: str) -> str:
    # 优先从 badge / status 元素抓
    status_xpath = (
        "//*[contains(@class, 'badge') or contains(@class, 'status') or self::span]"
        "[contains(translate(normalize-space(text()), "
        "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'RUNNING') "
        "or contains(translate(normalize-space(text()), "
        "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'STOPPED') "
        "or contains(translate(normalize-space(text()), "
        "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'RESTORING') "
        "or contains(translate(normalize-space(text()), "
        "'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'CRASHED')]"
    )
    mapping = {
        "RUNNING": "🟢 RUNNING",
        "STOPPED": "🔴 STOPPED",
        "RESTORING": "🟠 RESTORING",
        "CRASHED": "💥 CRASHED",
    }
    try:
        for se in driver.find_elements(By.XPATH, status_xpath):
            t = (se.text or "").strip().upper()
            for k, v in mapping.items():
                if k == t:
                    return v
    except Exception:
        pass

    # 全局兜底
    upper = body_text.upper()
    for k, v in mapping.items():
        if k in upper:
            return v
    return "未知"


def get_expire_and_progress(driver):
    dismiss_pwa_popups(driver)
    raw_str = "未知"
    total_seconds = 0
    prog_str = "未知"

    try:
        body_text = get_body_text(driver)

        server_status = _extract_server_status(driver, body_text)

        # 倒计时
        elems = driver.find_elements(
            By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]"
        )
        for elem in elems:
            txt = (elem.text or "").strip()
            m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", txt)
            if m:
                total_seconds = (
                    int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                )
                raw_str = f"剩余 {txt}"
                break

        if raw_str == "未知":
            m = re.search(
                r"(?i)(?:Time Remaining|remaining|expire)[\s:]*([0-9]+:[0-9]+:[0-9]+)",
                body_text,
            )
            if m:
                t = m.group(1).strip()
                p = t.split(":")
                total_seconds = int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
                raw_str = f"剩余 {t}"
            elif "STOPPED" in server_status:
                raw_str = "离线待唤醒"
            elif "RESTORING" in server_status:
                raw_str = "系统恢复中"
            elif "CRASHED" in server_status:
                raw_str = "运行崩溃待恢复"

        # 今日额度
        pm = re.search(
            r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE
        )
        if pm:
            prog_str = pm.group(1).replace(" ", "")
        else:
            # 只在 "Extensions today" 附近局部搜索,避免误匹配
            idx = body_text.lower().find("extensions today")
            if idx >= 0:
                seg = body_text[idx : idx + 120]
                m2 = re.search(r"(\d+\s*/\s*\d+)", seg)
                if m2:
                    prog_str = m2.group(1).replace(" ", "")

    except Exception as e:
        print(f"⚠️ 提取状态异常: {e}")

    return f"[{server_status}] {raw_str}", total_seconds, prog_str


# ============================================================
# 递归点击
# ============================================================
def recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=4) -> bool:
    try:
        for xpath in xpaths:
            elems = driver.find_elements(By.XPATH, xpath)
            for el in elems:
                if el.is_displayed():
                    print(f"  👉 在深度 {current_depth} 击中目标: {xpath}...", flush=True)
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
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass
            if found:
                return True
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass

    return False


def ensure_inside_ads_modal(driver):
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    dismiss_unlock_modal(driver)

    # 卡在保存/解冻期
    deadline = time.time() + 150
    while time.time() < deadline:
        body = get_body_text(driver)
        if any(k in body for k in ("Saving your server", "Start is locked")) or "RESTORING" in body.upper():
            print("  ⏳ 服务器正在保存/解冻中,等待 10 秒...", flush=True)
            time.sleep(10)
        else:
            break

    # Watch Ads 已在弹窗内
    watch_ads_xpath = "//button[contains(., 'Watch Ads') or contains(., 'Watch ad')]"
    try:
        for b in driver.find_elements(By.XPATH, watch_ads_xpath):
            if b.is_displayed() and "watch" in b.text.strip().lower():
                print("  ℹ️ 处于对话框内,点击 [Watch Ads]...", flush=True)
                physical_click_trusted(driver, b)
                time.sleep(3)
                return
    except Exception:
        pass

    # Extend 按钮
    try:
        extend_btns = driver.find_elements(
            By.XPATH, "//button[contains(., 'Extend') and not(@disabled)]"
        )
        if extend_btns and extend_btns[0].is_displayed():
            print("  ℹ️ 服务器运行中,点击 [+ Extend] 触发续期...", flush=True)
            physical_click_trusted(driver, extend_btns[0])
            time.sleep(2)
            for b in driver.find_elements(By.XPATH, watch_ads_xpath):
                if b.is_displayed():
                    physical_click_trusted(driver, b)
                    time.sleep(3)
                    break
            return
    except Exception:
        pass

    # Start / Recover
    try:
        start_btns = driver.find_elements(
            By.XPATH,
            "//button[(contains(., 'Start') or contains(., '开始') "
            "or contains(., 'Recover') or contains(., '恢复')) and not(@disabled)]",
        )
        if start_btns and start_btns[0].is_displayed():
            btn_text = start_btns[0].text.strip()
            print(f"  ℹ️ 服务器离线,点击 [{btn_text}] 唤醒看广告弹窗...", flush=True)
            physical_click_trusted(driver, start_btns[0])
            time.sleep(4)
            dismiss_unlock_modal(driver)
            return
    except Exception:
        pass


def click_watch_ad_everywhere(driver) -> bool:
    xpaths = [
        "//button[normalize-space(.)='Watch ad' or text()='Watch ad']",
        "//button[contains(translate(., 'AD', 'ad'), 'watch ad')]",
        "//div[contains(., 'Rewarded ad')]//button[contains(., 'Watch')]",
        "//*[contains(text(), 'Ready for Voer')]",
    ]
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=3)


def click_close_by_coordinates(driver) -> bool:
    """用 JS 派发点击,避开 move_to_element_with_offset 越界异常。"""
    try:
        driver.switch_to.default_content()
        ok = driver.execute_script("""
            const iframes = Array.from(document.querySelectorAll('iframe'));
            for (let f of iframes) {
                const r = f.getBoundingClientRect();
                if (r.width > 250 && r.height > 200 && r.top > 50) {
                    const x = r.right - 15;
                    const y = r.top + 15;
                    const target = document.elementFromPoint(x, y);
                    if (target) {
                        ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
                            target.dispatchEvent(new MouseEvent(evt, {
                                bubbles: true, cancelable: true, view: window,
                                clientX: x, clientY: y
                            }));
                        });
                        return true;
                    }
                }
            }
            return false;
        """)
        if ok:
            print("  🎯 执行右上角坐标点击!", flush=True)
            return True
    except Exception as e:
        print(f"  ⚠️ 坐标点击失败: {e}", flush=True)
    return False


def handle_sound_and_close_ad(driver, max_wait_sec=65) -> bool:
    print("  ⏳ 正在监控广告生命周期...", flush=True)
    start_time = time.time()

    continue_xpaths = [
        "//button[normalize-space(.)='Continue' or text()='Continue']",
        "//*[text()='Continue' or contains(text(), 'Continue')]",
        "//div[contains(text(), 'play with sound')]/following::button[contains(., 'Continue')]",
        "//*[@id='continue-button']",
    ]
    close_xpaths = [
        "//*[normalize-space(.)='Close' or text()='Close']",
        "//*[translate(text(), 'CLOSE', 'close')='close']",
        "//div[text()='Close' or contains(text(), 'Close')]",
        "//span[text()='Close' or contains(text(), 'Close')]",
        "//button[contains(., 'Close') or @aria-label='Close']",
        "//div[@id='dismiss-button' or @aria-label='Close ad']",
        "//*[@id='close-button']",
    ]

    has_sound_continued = False

    while time.time() - start_time < max_wait_sec:
        elapsed = time.time() - start_time

        if not has_sound_continued and elapsed < 15:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if recursive_find_and_click(driver, continue_xpaths, 0, 4):
                print("  🎉 点击声音遮罩 [Continue] 成功!", flush=True)
                has_sound_continued = True

        if elapsed < 30:
            time.sleep(2)
            continue

        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        if recursive_find_and_click(driver, close_xpaths, 0, 4):
            print("  🎯 成功命中并关闭广告 [Close]!", flush=True)
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            time.sleep(3)
            return True

        if elapsed > 40:
            if click_close_by_coordinates(driver):
                time.sleep(2)
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                return True

        time.sleep(1.5)

    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    print("  ⚠️ 本轮广告展示结束", flush=True)
    return False


# ============================================================
# 诊断
# ============================================================
def diag_page(driver, tag: str = "open"):
    print(f"\n===== [DIAG:{tag}] =====", flush=True)
    try:
        print(f"[DIAG] current_url = {driver.current_url}", flush=True)
    except Exception as e:
        print(f"[DIAG] current_url error: {e}", flush=True)
    try:
        print(f"[DIAG] title       = {driver.title!r}", flush=True)
    except Exception as e:
        print(f"[DIAG] title error: {e}", flush=True)
    try:
        ps_len = len(driver.page_source or "")
        print(f"[DIAG] page_source length = {ps_len}", flush=True)
    except Exception as e:
        print(f"[DIAG] page_source error: {e}", flush=True)

    try:
        head = driver.execute_script(
            "return document.body ? (document.body.innerText || '').slice(0,500) : 'NO_BODY';"
        )
        print(f"[DIAG] body_head = {head!r}", flush=True)
    except Exception as e:
        print(f"[DIAG] body read error: {e}", flush=True)

    try:
        ps = (driver.page_source or "").lower()
        for kw in (
            "just a moment",
            "cf-chl",
            "challenge-platform",
            "checking your browser",
            "cloudflare",
            "attention required",
            "access denied",
            "sign in",
            "log in",
            "登录",
        ):
            if kw in ps:
                print(f"[DIAG] ⚠️ page contains keyword: {kw}", flush=True)
    except Exception:
        pass

    try:
        driver.save_screenshot(f"diag_{tag}.png")
        print(f"[DIAG] 截图已保存: diag_{tag}.png", flush=True)
    except Exception as e:
        print(f"[DIAG] 截图失败: {e}", flush=True)
    print("===== [DIAG END] =====\n", flush=True)


def is_session_invalid(driver) -> bool:
    url = (driver.current_url or "").lower()
    if "/login" in url or "/signin" in url:
        return True
    body = get_body_text(driver).lower()
    keywords = (
        "sign in to your account",
        "please log in",
        "please sign in",
        "登录以继续",
        "请登录",
        "invalid token",
    )
    return any(k in body for k in keywords)


# ============================================================
# 主流程
# ============================================================
def save_next_cron_run(seconds_remaining: int, ext_prog: str):
    now_utc = datetime.now(timezone.utc)
    delay = seconds_remaining - 600 if seconds_remaining > 1200 else 14400
    next_run = now_utc + timedelta(seconds=delay)
    next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
    print(f"📌 下次执行时间 (UTC): {next_run_str}")
    with open("output.log", "a", encoding="utf-8") as f:
        f.write(f"NEXT_RUN_UTC={next_run_str}\n")


def main():
    print("=== Python 任务初始化启动 ===", flush=True)
    print(f"[ENV] IS_CI = {IS_CI}", flush=True)

    gost_proc = None
    uc_proxy = None
    final_seconds, final_prog = 0, "未知"

    if not SERVER_ID:
        print("❌ 未配置 VOER_SERVER_ID,退出。", flush=True)
        return

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理检测正常,已启用中转。")
        except Exception as e:
            print(f"⚠️ 代理启动失败:{e},将尝试直连。")

    chromium_args = [
        "--disable-heavy-ad-intervention",
        "--disable-features=HeavyAdIntervention,HeavyAdInterventionWarning",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1920,1080",
        "--mute-audio",
    ]
    if IS_CI:
        chromium_args += [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]

    driver_kwargs = dict(
        uc=True,
        headless=False,
        proxy=uc_proxy,
        chromium_arg=" ".join(chromium_args),
    )
    # xvfb 由外层 workflow 的 xvfb-run 提供,此处不再传 xvfb=True
    # (部分 seleniumbase 版本不支持该参数,会 TypeError)

    print("🌐 启动浏览器 (xvfb 由 workflow 提供) ...", flush=True)
    driver = Driver(**driver_kwargs)

    try:
        print("🔑 执行会话注入恢复...", flush=True)
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
        time.sleep(2)
        restore_session_data(driver, VOER_COOKIES)
        time.sleep(1)

        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(6)

        # 等待页面有内容
        try:
            WebDriverWait(driver, 20).until(
                lambda d: len((d.execute_script("return document.body ? document.body.innerText : ''") or "")) > 20
            )
        except Exception:
            print("⚠️ 等待页面内容超时", flush=True)

        dismiss_pwa_popups(driver)

        # ===== 诊断 =====
        diag_page(driver, tag="after_open")

        if is_session_invalid(driver):
            print("❌ 会话已失效或被拦截,请检查 VOER_COOKIES / 代理。", flush=True)
            tg_send("🔴 <b>VOER Host</b>\n\n❌ 会话已失效或被拦截,请更新 VOER_COOKIES 或代理。",
                    photo_path="diag_after_open.png")
            return

        expire_info_before, init_sec, init_prog = get_expire_and_progress(driver)
        print(f"⏳ 初始服务器状态: {expire_info_before} | 今日进度: {init_prog}", flush=True)

        # 初始状态若仍全"未知",直接告警
        if "[未知]" in expire_info_before and init_prog == "未知":
            print("🚨 页面内容为空或非控制台,请查看 diag_after_open.png", flush=True)
            tg_send(
                "🔴 <b>VOER Host</b>\n\n页面无法识别,疑似被 Cloudflare 拦截或代理异常。请查看截图。",
                photo_path="diag_after_open.png",
            )
            return

        completed = 0
        for current_ad in range(1, 5):
            print(f"\n🎬 === 正在准备第 {current_ad}/4 个广告 ===", flush=True)
            ensure_inside_ads_modal(driver)

            clicked = False
            for sec in range(35):
                ensure_inside_ads_modal(driver)
                if click_watch_ad_everywhere(driver):
                    print(f"  🎯 第 {sec + 1} 秒成功击发第 {current_ad} 轮的 [Watch ad]!", flush=True)
                    clicked = True
                    break
                time.sleep(1)

            if not clicked:
                stuck_screenshot = f"stuck_round_{current_ad}.png"
                try:
                    driver.switch_to.default_content()
                    driver.save_screenshot(stuck_screenshot)
                except Exception:
                    pass
                print(f"  ⚠️ 未能在模态框内等到第 {current_ad} 轮的 [Watch ad]", flush=True)
                break

            time.sleep(2)
            handle_sound_and_close_ad(driver, max_wait_sec=65)
            completed += 1
            print(f"  ✅ 第 {current_ad} 个广告闭环完成!", flush=True)
            time.sleep(3)

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        for b in driver.find_elements(
            By.XPATH, "//button[contains(., 'Close') or contains(., 'Done') or contains(., 'Finish')]"
        ):
            try:
                if b.is_displayed():
                    physical_click_trusted(driver, b)
                    time.sleep(1)
            except Exception:
                pass

        print("\n⏳ 4 轮流程完毕,等待 30 秒同步...", flush=True)
        time.sleep(30)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        driver.refresh()
        time.sleep(8)

        dismiss_pwa_popups(driver)
        dismiss_unlock_modal(driver)

        expire_info_after, final_seconds, final_prog = get_expire_and_progress(driver)
        try:
            driver.save_screenshot("final_success.png")
        except Exception:
            pass

        tg_send(
            f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
            f"🎬 <b>观看广告:</b><code>{completed}/4</code> 轮\n"
            f"⏳ <b>到期变动:</b><code>{html.escape(expire_info_before)}</code> ➜ "
            f"<code>{html.escape(expire_info_after)}</code>\n"
            f"📊 <b>今日进度:</b><code>{html.escape(final_prog)}</code>\n"
            f"⏰ <b>执行时间:</b><code>{now}</code>",
            photo_path="final_success.png",
        )
        print(f"\n✅ 任务执行完毕,最新状态: {expire_info_after} | 额度: {final_prog}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.switch_to.default_content()
            driver.save_screenshot("error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>VOER Host 续期通知</b>\n\n❌ <b>脚本执行异常</b>:\n"
            f"<code>{html.escape(err_msg)}</code>",
            photo_path="error.png",
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if gost_proc:
            try:
                gost_proc.terminate()
                print("gost 进程已终止。")
            except Exception:
                pass
        save_next_cron_run(final_seconds, final_prog)


if __name__ == "__main__":
    main()
