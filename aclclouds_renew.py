#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# ACLClouds 自动登录与服务器续期脚本 (Cap 验证修正版)
#
# 关键事实 (2026-10-01 实测):
# 1. 登录页 https://aclclouds.com/auth/login 直接 200, 是 React SPA
#    弹窗表单字段: input[name=username] / input[name=password]
# 2. "你是真人" 不是 Cloudflare / ALTCHA / 图片验证码,
#    而是开源自托管 Cap (trycap.dev), 页面上是
#    <cap-widget data-cap-api-endpoint="https://cap.aclclouds.com/<sitekey>/">
#      <input type="hidden" name="cap-token">   <- token 在【light DOM】里
#    </cap-widget>
#    真正的点击目标 div.captcha-trigger 在【open shadow DOM】里,
#    普通 find_element("checkbox") 永远点不到。
# 3. 该站 sitekey 的 challenge 只有 4 个 hashwx 工作量证明,
#    没有开启 instrumentation 浏览器指纹检测,
#    点一下等 WASM 算完即可, 不需要 OCR/点选。
# 4. 登录提交: GET /sanctum/csrf-cookie 然后
#    POST /auth/login  {user, password, captcha_token, captcha_answer}
#    浏览器会自动带 XSRF cookie, 不要手动注入。
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


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# gost: SOCKS5 -> 本地 HTTP 代理 (SeleniumBase 只吃 http 代理)
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# 通用工具
# ------------------------------------------------------------
def js_click(driver, element):
    """保底 JS 点击"""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
    time.sleep(0.2)
    driver.execute_script("arguments[0].click();", element)


def real_click(driver, element):
    """物理点击 + JS 兜底"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.3)
        ActionChains(driver).move_to_element(element).pause(0.25).click().perform()
        return True
    except Exception:
        try:
            js_click(driver, element)
            return True
        except Exception:
            return False


def fill_input(driver, element, value: str):
    """优先真人键盘输入 (React 受控组件最稳), JS 原型 setter 兜底"""
    try:
        element.click()
        time.sleep(0.15)
        element.send_keys(Keys.CONTROL, "a")
        time.sleep(0.05)
        element.send_keys(Keys.DELETE)
        time.sleep(0.1)
        element.send_keys(value)
        time.sleep(0.2)
        if element.get_attribute("value") == value:
            return
    except Exception:
        pass
    # 兜底: React/Vue 原生 setter + 完整事件
    driver.execute_script(
        """
        const el = arguments[0], val = arguments[1];
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        """,
        element, value,
    )


def dismiss_popups(driver):
    """只点真正的关闭小按钮, 避免误杀带 Close 字样的业务按钮"""
    try:
        btns = driver.find_elements(
            By.XPATH,
            "//button[contains(translate(@aria-label,'CLOSE','close'),'close') "
            "or contains(@class,'close') or @aria-label='Dismiss']",
        )
        for b in btns:
            if b.is_displayed():
                driver.execute_script("arguments[0].click();", b)
                time.sleep(0.3)
    except Exception:
        pass


# ------------------------------------------------------------
# Cloudflare (站点套了 CF, 机房 IP 偶尔出整页五秒盾)
# ------------------------------------------------------------
def is_cloudflare_challenge(driver) -> bool:
    try:
        title = (driver.title or "").lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    try:
        return bool(driver.execute_script(
            "return !!document.querySelector('#challenge-stage, #cf-please-wait-alert, "
            "iframe[src*=\"challenges.cloudflare.com\"], div.cf-turnstile');"
        ))
    except Exception:
        return False


def click_turnstile_if_present(driver) -> bool:
    """托管挑战偶尔弹复选框, 跨域 iframe 无法 switch, 直接点包装层坐标"""
    try:
        wrapper = driver.find_element(By.CSS_SELECTOR, "div.cf-turnstile, #challenge-stage")
        if not wrapper.is_displayed():
            return False
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", wrapper)
        time.sleep(0.5)
        # 复选框在小部件左侧, 距上/左约 30px
        ActionChains(driver).move_to_element_with_offset(wrapper, -115, 0).pause(0.2).click().perform()
        print("  👉 已尝试点击 Cloudflare Turnstile 复选框")
        time.sleep(4)
        return True
    except Exception:
        return False


def open_login_page(driver):
    print(f"🌐 打开登录页: {LOGIN_URL} ...", flush=True)
    # 本站没有整页 CF 盾时, 绝不能用 uc_open_with_reconnect:
    # 它会在 React SPA 引导期间切断 CDP 连接, 直接表现为"主页进不去/白屏"。
    driver.get(LOGIN_URL)
    time.sleep(3)

    for attempt in range(2):
        try:
            driver.wait_for_element_visible("input[name='username']", timeout=20)
            return
        except Exception:
            pass
        if is_cloudflare_challenge(driver):
            print(f"  🛡️ 检测到 Cloudflare 挑战 (第 {attempt + 1} 次), 启用 UC 重连...", flush=True)
            driver.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            time.sleep(4)
            click_turnstile_if_present(driver)
        else:
            # 可能只是 SPA chunk 慢, 普通刷新一次
            driver.get(LOGIN_URL)
            time.sleep(3)

    # 最后再等一轮
    driver.wait_for_element_visible("input[name='username']", timeout=20)


# ------------------------------------------------------------
# Cap 验证码 (核心修正)
# ------------------------------------------------------------
CAP_STATE_JS = r"""
const hosts = Array.from(document.querySelectorAll('cap-widget'))
  .filter(w => { const r = w.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
if (!hosts.length) return { found:false };
const w = hosts[hosts.length - 1];
const input = w.querySelector("input[name='cap-token']");
const sr = w.shadowRoot;
const label = sr ? (sr.querySelector('.label.active') || {}).textContent || '' : '';
const stateEl = sr ? sr.querySelector('.captcha') : null;
return {
  found: true,
  token: input ? (input.value || '') : '',
  label: String(label).trim(),
  state: stateEl ? (stateEl.getAttribute('data-state') || '') : '',
  disabled: sr ? !!sr.querySelector('.captcha-trigger[disabled]') : false
};
"""


def cap_read(driver) -> dict:
    try:
        return driver.execute_script(CAP_STATE_JS) or {}
    except Exception:
        return {"found": False}


def solve_cap(driver, context_name: str = "登录页", timeout: int = 75) -> bool:
    """
    点击 cap-widget 并等待 PoW 完成。
    成功判据 = light DOM 的 input[name=cap-token] 出现非空 token
    (shadow DOM 里的 "You're human" 文案用 document 根本读不到)。
    """
    print(f"  🛡️ 正在处理 [{context_name}] 的 Cap 验证 (工作量证明, 非图片验证码)...", flush=True)
    host = None
    deadline = time.time() + 25
    while time.time() < deadline:
        hosts = [
            w for w in driver.find_elements(By.CSS_SELECTOR, "cap-widget")
            if w.is_displayed() and w.size.get("width", 0) > 0
        ]
        if hosts:
            host = hosts[-1]
            break
        time.sleep(1)
    if host is None:
        print(f"  ❌ [{context_name}] 页面上没有找到 <cap-widget>", flush=True)
        return False

    for round_idx in range(1, 3):  # 最多尝试两轮 (出错会 reset)
        st = cap_read(driver)
        if st.get("token"):
            print(f"  🟢 [{context_name}] Cap token 已存在，无需再点", flush=True)
            return True

        # 1) 物理点击: captcha-trigger 铺满整个 cap-widget, 点宿主中心即可穿入 shadow DOM
        clicked = False
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", host)
            time.sleep(0.3)
            ActionChains(driver).move_to_element(host).pause(0.3).click().perform()
            clicked = True
            print(f"  👉 [{context_name}] 物理点击验证卡片 (第 {round_idx} 轮)", flush=True)
        except Exception:
            pass

        # 2) 兜底: 直接点 shadow 内的 trigger
        if not clicked:
            try:
                driver.execute_script(
                    "const t=arguments[0].shadowRoot.querySelector('.captcha-trigger');"
                    "t && t.click();",
                    host,
                )
                print(f"  👉 [{context_name}] shadow DOM JS 点击 (第 {round_idx} 轮)", flush=True)
            except Exception:
                pass

        # 3) 轮询 token / 错误状态
        err_seen = False
        end = time.time() + timeout
        while time.time() < end:
            st = cap_read(driver)
            if st.get("token"):
                print(f"  🟢 [{context_name}] Cap 验证成功 (state={st.get('state')})", flush=True)
                time.sleep(0.8)  # 等 React 接住 solve 事件写入表单状态
                return True
            label = (st.get("label") or "").lower()
            if "error" in label or st.get("state") == "error":
                print(f"  ⚠️ [{context_name}] Cap 报错: {st.get('label')}，reset 后重试...", flush=True)
                err_seen = True
                break
            time.sleep(1)

        if err_seen:
            try:
                driver.execute_script("arguments[0].reset && arguments[0].reset();", host)
            except Exception:
                pass
            time.sleep(1.5)
            continue

        # 3) 终极兜底: 直接调用组件公开的 solve() Promise
        print(f"  🔁 [{context_name}] 点击未触发，直接 await cap-widget.solve() ...", flush=True)
        try:
            driver.execute_async_script(
                """
                const w = arguments[0], cb = arguments[arguments.length - 1];
                Promise.resolve(w.solve ? w.solve() : null)
                  .then(() => cb('ok'))
                  .catch(e => cb(String(e)));
                setTimeout(() => cb('timeout'), 60000);
                """,
                host,
            )
        except Exception:
            pass
        end = time.time() + 30
        while time.time() < end:
            if cap_read(driver).get("token"):
                print(f"  🟢 [{context_name}] Cap 验证成功 (solve 调用)", flush=True)
                time.sleep(0.8)
                return True
            time.sleep(1)

    print(f"  ❌ [{context_name}] Cap 验证超时未拿到 token", flush=True)
    return False


# ------------------------------------------------------------
# 业务
# ------------------------------------------------------------
def get_expire_info(driver) -> str:
    dismiss_popups(driver)
    expire_info = "未知"
    try:
        body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")
        time_match = re.search(
            r'(?i)(?:Time remaining|remaining|expire)[\s:]*([0-9]+\s*[jdhm]\s*(?:[0-9]+\s*[hm])?)',
            body_text,
        )
        if time_match:
            expire_info = f"剩余 {time_match.group(1).strip()}"
        else:
            simple = re.search(r'(?i)\b(\d+\s*[jd]\s*(?:\d+\s*[hm])?)\b', body_text)
            if simple:
                expire_info = f"剩余 {simple.group(1).strip()}"
    except Exception as e:
        print(f"⚠️ 提取天数异常: {e}")
    return expire_info


def main():
    print("=== ACLClouds 续期任务启动 ===", flush=True)
    if not ACL_USERNAME or not ACL_PASSWORD:
        print("❌ 未配置 ACL_USERNAME 或 ACL_PASSWORD", flush=True)
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

    # GitHub Actions 里请配合 Xvfb 使用 headless=False (既有约定)
    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # ---------- 1. 打开登录页 (普通 get, 检测到 CF 盾才重连) ----------
        open_login_page(driver)
        time.sleep(1)

        # ---------- 2. 填账号密码 ----------
        user_elem = driver.find_element(By.CSS_SELECTOR, "input[name='username']")
        fill_input(driver, user_elem, ACL_USERNAME)
        print(f"  📝 已填入账号: {ACL_USERNAME[:3]}***", flush=True)

        pwd_elem = driver.find_element(By.CSS_SELECTOR, "input[name='password']")
        fill_input(driver, pwd_elem, ACL_PASSWORD)
        print("  📝 已填入密码", flush=True)
        time.sleep(0.5)

        # ---------- 3. 过 Cap (点一下, 等 PoW token) ----------
        if not solve_cap(driver, context_name="登录页"):
            driver.save_screenshot("cap_failed.png")
            tg_send("🔴 <b>ACLClouds 登录失败</b>\n\n❌ Cap 人机验证未通过（未拿到 cap-token）",
                    photo_path="cap_failed.png")
            return

        # ---------- 4. 提交登录 ----------
        print("🔑 点击 [Sign in] 提交登录...", flush=True)
        submit_btn = driver.find_element(
            By.XPATH,
            "//button[@type='submit' or contains(., 'Sign in') or contains(., 'Login')]",
        )
        real_click(driver, submit_btn)

        # 等离开 /auth/login
        logged_in = False
        for _ in range(30):
            if "/auth/login" not in driver.current_url:
                logged_in = True
                break
            time.sleep(1)

        if not logged_in:
            driver.save_screenshot("login_failed.png")
            err_hint = "登录验证失败"
            try:
                body_text = driver.get_text("body")
                for line in body_text.split("\n"):
                    s = line.strip()
                    if s and any(k in s.lower() for k in
                                 ["invalid", "incorrect", "captcha", "not found",
                                  "verify", "credentials", "required"]):
                        err_hint = s
                        break
            except Exception:
                pass
            print(f"❌ 登录未成功跳转，页面提示: {err_hint}", flush=True)
            tg_send(
                f"🔴 <b>ACLClouds 登录失败</b>\n\n❌ <b>提示：</b><code>{html.escape(err_hint)}</code>",
                photo_path="login_failed.png",
            )
            return

        print(f"✅ 登录成功！当前页面: {driver.current_url}", flush=True)

        # ---------- 5. 打开服务器控制台 ----------
        print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
        driver.get(SERVER_CONSOLE_URL)
        time.sleep(5)
        dismiss_popups(driver)

        expire_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器状态: {expire_before}", flush=True)

        # ---------- 6. 找 Renew ----------
        renew_xpath = "//button[contains(., 'Renew') or contains(., 'Renouveler')]"
        renew_elements = driver.find_elements(By.XPATH, renew_xpath)
        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        if not renew_elements:
            # 每轮都发巡检通知, 方便用户确认任务在跑/排查哪一步出问题
            print(
                f"ℹ️ 当前未发现 Renew 按钮（{expire_before}，到期前 2 天内才开放）",
                flush=True,
            )
            driver.save_screenshot("dashboard_status.png")
            tg_send(
                f"ℹ️ <b>ACLClouds 状态巡检</b>\n\n"
                f"⏳ <b>有效时间：</b><code>{html.escape(expire_before)}</code>\n"
                f"📌 <b>续期状态：</b>未到操作窗口（到期前 2 天内开放）\n"
                f"⏰ <b>巡检时间：</b><code>{now}</code>",
                photo_path="dashboard_status.png",
            )
            return

        print("👉 点击 Renew 按钮...", flush=True)
        real_click(driver, renew_elements[0])
        time.sleep(2.5)

        # ---------- 7. 弹窗里再来一次 Cap ----------
        cap_in_modal = any(
            w.is_displayed() for w in driver.find_elements(By.CSS_SELECTOR, "cap-widget")
        )
        if cap_in_modal:
            if not solve_cap(driver, context_name="Renew 弹窗"):
                driver.save_screenshot("renew_cap_failed.png")
                tg_send("🔴 <b>ACLClouds 续期失败</b>\n\n❌ Renew 弹窗 Cap 验证未通过",
                        photo_path="renew_cap_failed.png")
                return
        else:
            print("  ℹ️ 弹窗内未发现 cap-widget，可能直接确认即可", flush=True)
        time.sleep(1)

        # ---------- 8. 弹窗确认按钮 ----------
        try:
            modal_buttons = driver.find_elements(By.XPATH, "//div[@role='dialog']//button")
            for mb in modal_buttons:
                txt = (mb.text or "").strip().lower()
                if not mb.is_displayed() or not txt:
                    continue
                if any(k in txt for k in ("cancel", "close", "annuler")):
                    continue
                if any(k in txt for k in ("renew", "confirm", "continue", "submit", "valider", "confirmer")):
                    print(f"  👉 点击弹窗确认按钮: [{mb.text.strip()}]", flush=True)
                    real_click(driver, mb)
                    time.sleep(3)
                    break
        except Exception as e:
            print(f"  ℹ️ 弹窗确认按钮检测异常: {e}")

        for _ in range(10):
            dialog_open = driver.execute_script(
                "return !!document.querySelector('div[role=\"dialog\"], .modal');"
            )
            if not dialog_open:
                print("  ✅ 弹窗已关闭", flush=True)
                break
            time.sleep(1)

        # ---------- 9. 刷新核对 ----------
        time.sleep(5)
        driver.refresh()
        time.sleep(4)
        dismiss_popups(driver)

        expire_after = get_expire_info(driver)
        driver.save_screenshot("final_page.png")
        tg_send(
            f"📋 <b>ACLClouds 自动续期汇总</b>\n\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_before)}</code> ➜ "
            f"<code>{html.escape(expire_after)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_page.png",
        )
        print(f"\n✅ 任务执行完毕，最新状态: {expire_after}", flush=True)

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}", flush=True)
        try:
            driver.save_screenshot("error.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>ACLClouds 续期通知</b>\n\n❌ <b>脚本执行异常</b>：\n"
            f"<code>{html.escape(err_msg)}</code>",
            photo_path="error.png",
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
