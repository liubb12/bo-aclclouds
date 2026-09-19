#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动登录与续期脚本 (模拟真实人类操作全流程版)
# ============================================================
import html
import os
import random
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests
from seleniumbase import Driver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

ROOT_URL = "https://eknodes.es"
BASE_URL = "https://dash.eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_EMAIL = os.environ.get("EK_EMAIL", "").strip()
EK_USERNAME = os.environ.get("EK_USERNAME", "").strip()
EK_PASSWORD = os.environ.get("EK_PASSWORD", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


def human_sleep(min_s=1.0, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def tg_send(text: str, photo_path: str = None):
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
            time.sleep(random.uniform(0.04, 0.10))

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


def solve_turnstile_challenge(driver, timeout=35, tag="Turnstile"):
    """专为穿透 Cloudflare Turnstile 复选框设计"""
    print(f"  🛡️ 正在检测并处理 [{tag}] 验证框...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        # 如果已经成功跳转进入后台或主页
        curr = driver.current_url
        if "/servers" in curr or (BASE_URL in curr and "/login" not in curr):
            print("  🟢 检测到已成功放行并跳转！", flush=True)
            return True

        # 如果是续期弹窗，检查确认按钮是否点亮
        if tag == "续期确认":
            confirm = driver.find_elements(By.XPATH, "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar')]")
            if confirm and confirm[0].is_enabled():
                print("  🟢 确认续期按钮已激活！", flush=True)
                return True

        # 1. SeleniumBase 原生接口辅助
        try:
            driver.uc_gui_click_cf()
        except Exception:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass

        # 2. 深入 iframe 点击 checkbox
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
                        print("  🎯 成功物理点击 Turnstile 复选框！", flush=True)
                    driver.switch_to.default_content()
                    break
        except Exception:
            driver.switch_to.default_content()

        time.sleep(2)

    return False


def get_servers_info(driver):
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
            elif any(k in text for k in ("Inactivo", "Detenido", "Apagado")):
                status = "STOPPED (已停止)"
            elif "Activo" in text:
                status = "Activo (运行中)"

            info.append(f"• <b>{name}</b>: 状态 <code>{status}</code> | 到期 <code>{exp_date}</code>")
    except Exception as e:
        print(f"提取状态异常: {e}")
    return "\n".join(info) if info else "服务器正常运行"


def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动登录与续期任务启动", flush=True)
    print("=" * 45, flush=True)

    login_account = EK_EMAIL if EK_EMAIL else EK_USERNAME
    if not login_account or not EK_PASSWORD:
        print("❌ 未在 Secrets 中配置账号 (EK_EMAIL) 或密码 (EK_PASSWORD)！", flush=True)
        return

    gost_proc = None
    uc_proxy = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("🔗 代理已启动并生效。", flush=True)
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，尝试直连模式。", flush=True)

    # 抹除自动化特征
    driver = Driver(
        uc=True,
        headless=False,
        proxy=uc_proxy,
        uc_subprocess=True,
    )

    try:
        # 第一步：先访问主站 eknodes.es 建立可信连接，避开直接访问 /login 触发 Code 11
        print(f"🌐 [步骤 1] 访问主站入口: {ROOT_URL} ...", flush=True)
        driver.uc_open_with_reconnect(ROOT_URL, reconnect_time=4)
        human_sleep(2.0, 3.5)

        # 寻找顶部的 Panel 按钮点击进入
        panel_btns = driver.find_elements(By.XPATH, "//a[contains(., 'Panel')] | //button[contains(., 'Panel')]")
        if panel_btns:
            print("  👉 点击主站右上角 [Panel] 按钮跳转...", flush=True)
            human_click(driver, panel_btns[0])
            human_sleep(4.0, 6.0)
        else:
            print("  🌐 直接导航至登录页...", flush=True)
            driver.get(LOGIN_URL)
            human_sleep(4.0, 6.0)

        # 遇到 Vercel 拦截屏自动尝试穿透
        if "Failed to verify your browser" in driver.get_text("body"):
            print("  🛡️ 触发 Vercel Checkpoint，执行自动穿透...", flush=True)
            try:
                driver.uc_gui_click_cf()
            except Exception:
                pass
            human_sleep(4.0, 6.0)

        # 第二步：填入邮箱和密码
        print("📝 [步骤 2] 等待登录表单加载...", flush=True)
        input_xpath = "//input[@type='email' or @type='text' or contains(@placeholder, 'CORREO') or contains(@placeholder, 'email')]"
        email_elem = driver.wait_for_element_visible(input_xpath, timeout=30)

        masked_acc = login_account[:3] + "***" if len(login_account) > 3 else "***"
        print(f"  ✍️ 填入邮箱账号: {masked_acc}", flush=True)
        human_type(driver, email_elem, login_account)
        human_sleep(0.5, 0.8)

        pwd_elem = driver.wait_for_element_visible("//input[@type='password']", timeout=15)
        print("  ✍️ 填入登录密码...", flush=True)
        human_type(driver, pwd_elem, EK_PASSWORD)
        human_sleep(0.6, 1.2)

        # 第三步：点击 INICIAR SESIÓN 按钮
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit' or contains(., 'INICIAR SESIÓN') or contains(., 'Iniciar')]")
        print("🔑 [步骤 3] 点击 INICIAR SESIÓN 提交...", flush=True)
        human_click(driver, submit_btn)

        # 第四步：处理提交后弹出的 Turnstile 验证框
        human_sleep(2.5, 3.5)
        print("🛡️ [步骤 4] 检测并穿透弹出的 Turnstile 验证框...", flush=True)
        solve_turnstile_challenge(driver, timeout=30, tag="登录验证")

        # 等待页面跳入后台
        for _ in range(15):
            curr_url = driver.current_url
            if "/login" not in curr_url and BASE_URL in curr_url:
                break
            time.sleep(1)

        if "/login" in driver.current_url:
            driver.save_screenshot("ek_login_fail.png")
            raise RuntimeError("登录后未成功进入后台，请检查凭据或人机验证。")

        print(f"🎉 登录成功！当前页面: {driver.current_url}", flush=True)

        # 第五步：若在 Inicio 页面，点击左侧 Servidores 导航进入服务器列表
        human_sleep(2.0, 3.0)
        if "/servers" not in driver.current_url:
            print("🚀 [步骤 5] 导航至服务器管理页 (Servidores)...", flush=True)
            serv_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'servers') or contains(., 'Servidores')] | //div[contains(., 'Servidores')]")
            if serv_links:
                human_click(driver, serv_links[0])
                human_sleep(3.0, 5.0)
            else:
                driver.get(SERVERS_URL)
                human_sleep(4.0, 6.0)

        try:
            driver.wait_for_element_present("//h1[contains(., 'SERVIDORES')] | //button[contains(., 'GESTIONAR')]", timeout=20)
            print("🎯 服务器管理列表加载就绪！", flush=True)
        except Exception:
            pass

        status_before = get_servers_info(driver)
        print(f"📊 当前服务器状态:\n{status_before}", flush=True)

        # 第六步：检索待续期按钮
        renovar_btn_xpath = "//button[contains(., 'RENOVAR') or contains(., 'Renovar')]"
        renovar_buttons = driver.find_elements(By.XPATH, renovar_btn_xpath)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 若无待续期按钮（满期 7 天无需续期）
        if not renovar_buttons:
            print("ℹ️ 当前页面未检测到待续期按钮（周期已是上限 7 天，无需续期）。", flush=True)
            driver.save_screenshot("ek_current_status.png")
            tg_send(
                f"🛡️ <b>EKNodes 自动巡检正常</b>\n\n"
                f"📊 <b>实例状态：</b>\n{status_before}\n\n"
                f"⏭️ <b>执行结果：</b><code>周期充足，无需续期</code>\n"
                f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
                photo_path="ek_current_status.png"
            )
            print("✅ 状态已推送到 Telegram。", flush=True)
            return

        # 第七步：点击 RENOVAR 并处理弹窗二次验证
        renew_success = False
        for idx, btn in enumerate(renovar_buttons):
            print(f"👉 [步骤 7] 正在点击第 {idx+1}/{len(renovar_buttons)} 台服务器的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(2.5, 3.5)

            # 处理弹窗内验证码
            solve_turnstile_challenge(driver, timeout=20, tag="续期确认")
            human_sleep(1.0, 2.0)

            confirm_xpath = "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'RENOVACIÓN')]"
            confirm_btns = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🚀 点击 [CONFIRMAR RENOVACIÓN] 确认续期！", flush=True)
                human_click(driver, confirm_btns[0])
                renew_success = True
                human_sleep(4.0, 6.0)
            else:
                print("  ⚠️ 未找到确认续期按钮或按钮未激活", flush=True)

        # 第八步：刷新获取续期后状态并推送
        driver.refresh()
        human_sleep(4.0, 6.0)
        status_after = get_servers_info(driver)
        driver.save_screenshot("ek_final.png")

        result_tag = "✅ 续期完成 (+7天)" if renew_success else "⚠️ 续期已提交"
        tg_send(
            f"🎉 <b>EKNodes 服务器续期报告</b>\n\n"
            f"⏳ <b>续期前状态：</b>\n{status_before}\n\n"
            f"⌛ <b>续期后状态：</b>\n{status_after}\n\n"
            f"📊 <b>执行结果：</b><code>{result_tag}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部流程执行完毕，图文报告已推送至 Telegram！", flush=True)

    except Exception as e:
        err = str(e)
        print(f"❌ 执行异常: {err}", flush=True)
        try:
            driver.save_screenshot("ek_error.png")
            tg_send(f"🔴 <b>EKNodes 异常</b>\n\n<code>{html.escape(err)}</code>", photo_path="ek_error.png")
        except Exception:
            pass
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理中转已退出。", flush=True)


if __name__ == "__main__":
    main()
