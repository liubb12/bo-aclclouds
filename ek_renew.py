#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动续期与服务器状态巡检脚本 (Cookie 穿透与图文增强版)
# ============================================================
import html
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
import requests
from seleniumbase import Driver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

BASE_URL = "https://dash.eknodes.es"
ROOT_URL = "https://eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
EK_EMAIL = os.environ.get("EK_EMAIL", "").strip()
EK_USERNAME = os.environ.get("EK_USERNAME", "").strip()
EK_PASSWORD = os.environ.get("EK_PASSWORD", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def human_sleep(min_s=1.0, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def tg_send(text: str, photo_path: str = None):
    """发送 Telegram 消息，若有图片则发送带文字说明的 Photo"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ 未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过通知。")
        return
    try:
        if photo_path and os.path.exists(photo_path) and os.path.getsize(photo_path) > 1000:
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
            print("  ✅ TG 通知发送成功", flush=True)
        else:
            print(f"  ⚠️ TG 通知发送返回码 {resp.status_code}: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ TG 通知发送异常: {e}", flush=True)


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
                print("  ✅ 本地 HTTP 代理连通性测试成功", flush=True)
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("  🚀 启动 gost 代理中转...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}", flush=True)
    return proc


def human_type(driver, element, text: str):
    try:
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.2)).click().perform()
        human_sleep(0.1, 0.3)
        element.send_keys(Keys.CONTROL, "a")
        human_sleep(0.1, 0.2)
        element.send_keys(Keys.BACKSPACE)
        human_sleep(0.1, 0.2)

        for ch in text:
            element.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.12))

        driver.execute_script("""
            const el = arguments[0];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        """, element)
        human_sleep(0.2, 0.4)
    except Exception:
        pass


def human_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.2, 0.4)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def click_turnstile_checkbox(driver, timeout=25):
    """穿透点击弹窗内的 Turnstile 复选框"""
    print("  🛡️ 正在检测并穿透 Cloudflare Turnstile 验证框...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        # 方法 1：切入 iframe 物理定位
        try:
            driver.switch_to.default_content()
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for f in iframes:
                src = f.get_attribute("src") or ""
                if any(k in src for k in ("cloudflare", "turnstile", "challenges")):
                    driver.switch_to.frame(f)
                    time.sleep(0.3)
                    boxes = driver.find_elements(By.CSS_SELECTOR, "input[type='checkbox'], #checkbox, .ctp-checkbox-label")
                    if boxes:
                        ActionChains(driver).move_to_element(boxes[0]).pause(0.2).click().perform()
                        print("  🎯 成功切入 iframe 物理点击复选框！", flush=True)
                    driver.switch_to.default_content()
                    break
        except Exception:
            driver.switch_to.default_content()

        # 方法 2：SeleniumBase 原生辅助
        try:
            driver.uc_gui_click_cf()
        except Exception:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass

        time.sleep(2)
        confirm_btn = driver.find_elements(By.XPATH, "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar')]")
        if confirm_btn and confirm_btn[0].is_enabled():
            print("  🟢 确认续期按钮已激活就绪！", flush=True)
            return True

    return False


def get_servers_info(driver):
    """提取页面上的服务器卡片信息"""
    info = []
    try:
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and (.//button[contains(., 'RENOVAR')] or .//button[contains(., 'GESTIONAR')])]")
        if not cards:
            cards = driver.find_elements(By.XPATH, "//div[contains(., 'Expira') and contains(@class, 'rounded')]")

        for c in cards:
            text = c.text
            match = re.search(r'Expira\s+([0-9]{1,2}\s+[a-zA-Z]+\s+[0-9]{4})', text)
            exp_date = match.group(1).strip() if match else "未知"
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            name = lines[0] if lines else "Server"

            status = "ONLINE"
            if "Iniciando" in text:
                status = "Iniciando (启动中)"
            elif "Instalando" in text:
                status = "Instalando (安装中)"
            elif "Inactivo" in text or "Detenido" in text or "Apagado" in text:
                status = "STOPPED (已停止)"
            elif "Activo" in text:
                status = "Activo (运行中)"

            info.append(f"• <b>{name}</b>: 状态 <code>{status}</code> | 到期 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "服务器正常运行"


def inject_cookies_string(driver, cookie_raw: str):
    """将 cURL 格式的整串 Cookie 注入到浏览器域中"""
    print("🍪 正在初始化浏览器上下文并注入 Cookie...", flush=True)
    # 先打开主域以允许设置该域下的 Cookie
    driver.get(BASE_URL)
    time.sleep(2)

    # 拆解键值对
    cookies_list = []
    for pair in cookie_raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        cookies_list.append((k.strip(), v.strip()))

    injected_count = 0
    for name, value in cookies_list:
        for domain in [".eknodes.es", "dash.eknodes.es"]:
            try:
                driver.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "sameSite": "Lax"
                })
                injected_count += 1
                break
            except Exception:
                try:
                    driver.add_cookie({
                        "name": name,
                        "value": value,
                        "path": "/"
                    })
                    injected_count += 1
                    break
                except Exception:
                    pass

    print(f"  ✅ 成功向浏览器注入 {injected_count} 个 Session 凭据！", flush=True)


def check_and_start_if_stopped(driver):
    """若在控制台卡片中检测到已停止，点击 GESTIONAR 进入翼龙面板尝试拉起"""
    try:
        cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'rounded') and .//button[contains(., 'GESTIONAR')]]")
        for c in cards:
            text = c.text
            if any(k in text for k in ("Inactivo", "Detenido", "Apagado")):
                print("  ⚡ 检测到服务器处于停止状态，尝试进入控制台开机...", flush=True)
                btn = c.find_element(By.XPATH, ".//button[contains(., 'GESTIONAR')]")
                main_w = driver.current_window_handle
                human_click(driver, btn)
                time.sleep(5)
                for w in driver.window_handles:
                    if w != main_w:
                        driver.switch_to.window(w)
                        start_btn = driver.find_elements(By.XPATH, "//button[contains(., 'Start') or contains(., 'Iniciar')]")
                        if start_btn and start_btn[0].is_enabled():
                            human_click(driver, start_btn[0])
                            print("  👉 控制台中已成功点击 Start 启动服务器！", flush=True)
                            time.sleep(3)
                        driver.close()
                driver.switch_to.window(main_w)
                return "⚡ 已执行开机"
    except Exception:
        pass
    return "正常运行"


def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动续期与巡检任务启动", flush=True)
    print("=" * 45, flush=True)

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理已启动并生效。", flush=True)
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，尝试直连模式。", flush=True)

    # 规范标准桌面窗口尺寸
    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        # 1. 优先使用 Cookie 穿透登录
        is_logged_in = False
        if EK_COOKIE:
            print("🔑 检测到已配置 EK_COOKIE，执行免密直登穿透...", flush=True)
            inject_cookies_string(driver, EK_COOKIE)
            driver.get(SERVERS_URL)
            human_sleep(4.0, 6.0)

            if "/login" not in driver.current_url and "Failed to verify" not in driver.get_text("body"):
                print("🎉 Cookie 直登成功！成功穿透 Vercel 屏障直达后台！", flush=True)
                is_logged_in = True
            else:
                print("⚠️ Cookie 已失效或未放行，将尝试账号密码登录...", flush=True)

        # 2. 若无 Cookie 或 Cookie 失效，走账号密码回退登录
        if not is_logged_in:
            login_account = EK_EMAIL if EK_EMAIL else EK_USERNAME
            if not login_account or not EK_PASSWORD:
                raise RuntimeError("缺少有效 EK_COOKIE 且未配置账号密码，无法继续。")

            print(f"🌐 正在访问主站入口: {ROOT_URL} ...", flush=True)
            driver.uc_open_with_reconnect(ROOT_URL, reconnect_time=4)
            human_sleep(2.0, 3.0)

            print(f"🌐 跳转至登录页: {LOGIN_URL} ...", flush=True)
            driver.get(LOGIN_URL)
            human_sleep(4.0, 6.0)

            if "/servers" not in driver.current_url:
                user_selector = "input[type='email'], input[type='text'], input[name='email'], input[name='username']"
                email_elem = driver.wait_for_element_visible(user_selector, timeout=25)
                masked_acc = login_account[:3] + "***" if len(login_account) > 3 else "***"
                print(f"  📝 填入登录账号: {masked_acc}", flush=True)
                human_type(driver, email_elem, login_account)
                human_sleep(0.5, 0.8)

                pwd_elem = driver.wait_for_element_visible("input[type='password']", timeout=10)
                print("  📝 填入密码...", flush=True)
                human_type(driver, pwd_elem, EK_PASSWORD)
                human_sleep(0.6, 1.2)

                submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'INICIAR SESIÓN') or contains(., 'Iniciar')]")
                print("🔑 点击 INICIAR SESIÓN 提交...", flush=True)
                human_click(driver, submit_btn)

                human_sleep(2.0, 3.0)
                print("🛡️ 处理弹出的 Cloudflare 人机验证...", flush=True)
                click_turnstile_checkbox(driver, timeout=30)

                for _ in range(15):
                    if "/login" not in driver.current_url:
                        break
                    time.sleep(1)

                if "/login" in driver.current_url:
                    driver.save_screenshot("ek_login_fail.png")
                    raise RuntimeError("登录未成功跳转，请确认凭据与验证码。")

                print(f"✅ 登录成功！当前 URL: {driver.current_url}", flush=True)

        # 3. 确保位于服务器列表页
        if "/servers" not in driver.current_url:
            driver.get(SERVERS_URL)
            human_sleep(4.0, 6.0)

        try:
            driver.wait_for_element_present("//h1[contains(., 'SERVIDORES')] | //button[contains(., 'GESTIONAR')]", timeout=20)
            print("🎯 服务器管理列表加载就绪！", flush=True)
        except Exception:
            print("⚠️ 等待管理列表超时，继续尝试提取页面内容...", flush=True)

        # 状态提取与电源检测
        status_before = get_servers_info(driver)
        power_action = check_and_start_if_stopped(driver)
        print(f"📊 当前服务器状态:\n{status_before}\n⚡ 电源动作: {power_action}", flush=True)

        # 4. 检索待续期按钮
        renovar_btn_xpath = "//button[contains(., 'RENOVAR') or contains(., 'Renovar')]"
        renovar_buttons = driver.find_elements(By.XPATH, renovar_btn_xpath)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 若页面没有 RENOVAR 按钮（说明当前已是满额 7 天）
        if not renovar_buttons:
            print("ℹ️ 当前页面未检测到待续期按钮（服务器周期已处于上限）。", flush=True)
            driver.save_screenshot("ek_current_status.png")
            tg_send(
                f"🛡️ <b>EKNodes 自动巡检正常</b>\n\n"
                f"⚡ <b>电源状态：</b><code>{power_action}</code>\n"
                f"📊 <b>实例状态：</b>\n{status_before}\n\n"
                f"⏭️ <b>执行结果：</b><code>周期已达上限，维持满期</code>\n"
                f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
                photo_path="ek_current_status.png"
            )
            print("✅ 满期状态已推送到 Telegram。", flush=True)
            return

        # 5. 循环点击各个服务器卡片的 RENOVAR 按钮
        renew_success = False
        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 正在点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(2.5, 4.0)

            # 穿透弹窗内 Turnstile
            print("  🛡️ 穿透续期弹窗内 Turnstile 验证码...", flush=True)
            click_turnstile_checkbox(driver, timeout=20)
            human_sleep(1.5, 2.5)

            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btns = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🚀 拟真点击 [CONFIRMAR RENOVACIÓN] 确认续期！", flush=True)
                human_click(driver, confirm_btns[0])
                renew_success = True
                human_sleep(4.0, 6.0)
            else:
                print("  ⚠️ 未找到确认续期按钮或按钮未激活", flush=True)

        # 6. 刷新页面抓取续期后的最终状态
        driver.refresh()
        human_sleep(4.0, 6.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        result_tag = "✅ 续期完成 (+7天)" if renew_success else "⚠️ 续期动作已触发"
        tg_send(
            f"🎉 <b>EKNodes 服务器续期报告</b>\n\n"
            f"⚡ <b>电源动作：</b><code>{power_action}</code>\n"
            f"⏳ <b>续期前状态：</b>\n{status_before}\n\n"
            f"⌛ <b>续期后状态：</b>\n{status_after}\n\n"
            f"📊 <b>执行结果：</b><code>{result_tag}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部操作执行完毕，报告已发送至 Telegram！", flush=True)

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
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理中转已退出。", flush=True)


if __name__ == "__main__":
    main()
