#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动巡检、开机拉起与 [RENOVAR SERVIDOR] 弹窗穿透续期
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

BASE_URL = "https://dash.eknodes.es"
SERVERS_URL = f"{BASE_URL}/servers"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
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
            print(f"  ⚠️ TG 返回码 {resp.status_code}: {resp.text}", flush=True)
    except Exception as e:
        print(f"  ⚠️ TG 发送异常: {e}", flush=True)


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


def human_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        human_sleep(0.2, 0.4)
        ActionChains(driver).move_to_element(element).pause(random.uniform(0.1, 0.3)).click().perform()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def solve_modal_turnstile(driver, timeout=30):
    """专门穿透 RENOVAR SERVIDOR 弹窗内的 Turnstile 复选框"""
    print("  🛡️ 正在攻破弹窗内的 Cloudflare Turnstile 验证...", flush=True)
    start = time.time()

    while time.time() - start < timeout:
        # 检查确认按钮是否已经被激活点亮（disabled 属性移除）
        confirm_btn = driver.find_elements(By.XPATH, "//button[contains(., 'CONFIRMAR RENOVACIÓN') or contains(., 'Confirmar')]")
        if confirm_btn:
            btn = confirm_btn[0]
            is_disabled = btn.get_attribute("disabled")
            aria_disabled = btn.get_attribute("aria-disabled")
            classes = btn.get_attribute("class") or ""
            # 如果没有 disabled 且未处于暗色禁用类
            if not is_disabled and aria_disabled != "true" and "opacity-50" not in classes:
                print("  🟢 [CONFIRMAR RENOVACIÓN] 按钮已被点亮激活！", flush=True)
                return True

        # 尝试 1：SeleniumBase 官方专用 GUI 接口
        try:
            driver.uc_gui_click_cf()
        except Exception:
            try:
                driver.uc_gui_click_captcha()
            except Exception:
                pass

        # 尝试 2：精准进入弹窗包含的 iframe 点击复选框
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
                        print("  🎯 成功切入 iframe 物理点击了验证复选框！", flush=True)
                    driver.switch_to.default_content()
                    break
        except Exception:
            driver.switch_to.default_content()

        time.sleep(2)

    return False


def smart_inject_cookies(driver, raw_input: str):
    """精准向 dash.eknodes.es 与 .eknodes.es 注入 Cookie"""
    if not raw_input:
        return 0

    cookie_str = raw_input
    match_h = re.search(r"(?i)-H\s+['\"]cookie:\s*(.*?)['\"]", raw_input)
    if match_h:
        cookie_str = match_h.group(1)
    else:
        match_b = re.search(r"(?i)-b\s+['\"](.*?)['\"]", raw_input)
        if match_b:
            cookie_str = match_b.group(1)

    cookies_list = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        cookies_list.append((k.strip(), v.strip()))

    injected = 0
    for name, value in cookies_list:
        for domain in ["dash.eknodes.es", ".eknodes.es"]:
            try:
                driver.add_cookie({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": "/",
                    "sameSite": "Lax"
                })
                injected += 1
                break
            except Exception:
                try:
                    driver.add_cookie({"name": name, "value": value, "path": "/"})
                    injected += 1
                    break
                except Exception:
                    pass
    return injected


def parse_server_cards(driver):
    """提取页面上所有卡片的详细信息"""
    results = []
    cards = driver.find_elements(By.XPATH, "//div[contains(., 'Expira') and (contains(., 'GESTIONAR') or contains(., 'RENOVAR'))]")
    for card in cards:
        text = card.text.strip()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        name = lines[0] if lines else "Server"

        exp_match = re.search(r'Expira\s+([0-9]{1,2}\s+[a-zA-Z]+\s+[0-9]{4})', text)
        exp_date = exp_match.group(1).strip() if exp_match else "未知"

        status = "ONLINE"
        if "Iniciando" in text:
            status = "Iniciando (启动中)"
        elif "Instalando" in text:
            status = "Instalando (安装中)"
        elif any(k in text for k in ("Inactivo", "Detenido", "Apagado", "Offline")):
            status = "STOPPED (已关机/停止)"
        elif "Activo" in text:
            status = "Activo (运行中)"

        results.append({
            "element": card,
            "name": name,
            "status": status,
            "exp_date": exp_date,
            "text": text
        })
    return results


def check_power_and_start(driver, cards_data):
    """关机状态自动点击 GESTIONAR 进入后台拉起开机"""
    actions = []
    main_window = driver.current_window_handle

    for item in cards_data:
        if "STOPPED" in item["status"]:
            print(f"⚡ 检测到 [{item['name']}] 处于停止状态，尝试进入后台启动...", flush=True)
            try:
                gest_btn = item["element"].find_element(By.XPATH, ".//button[contains(., 'GESTIONAR')]")
                human_click(driver, gest_btn)
                time.sleep(5)

                for handle in driver.window_handles:
                    if handle != main_window:
                        driver.switch_to.window(handle)
                        break

                start_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Start') or contains(., 'Iniciar')]")
                if start_btns and start_btns[0].is_enabled():
                    human_click(driver, start_btns[0])
                    actions.append(f"{item['name']}: 已发送开机指令")
                    time.sleep(3)
                driver.close()
                driver.switch_to.window(main_window)
            except Exception as e:
                print(f"开机拉起执行异常: {e}", flush=True)
                try:
                    driver.switch_to.window(main_window)
                except Exception:
                    pass

    return " | ".join(actions) if actions else "全部正常运行"


def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动巡检、开机检测与精准续期任务", flush=True)
    print("=" * 45, flush=True)

    if not EK_COOKIE:
        print("❌ 未在 Secrets 中配置 EK_COOKIE，无法继续执行！", flush=True)
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

    driver = Driver(uc=True, headless=False, proxy=uc_proxy, uc_subprocess=True)

    try:
        # 1. 访问控制台子域
        print(f"🌐 [步骤 1] 访问控制台建立上下文: {BASE_URL} ...", flush=True)
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
        human_sleep(2.0, 3.5)

        # 2. 注入 Cookie
        print("🍪 [步骤 2] 精准注入 Cookie 凭据...", flush=True)
        injected_count = smart_inject_cookies(driver, EK_COOKIE)
        print(f"  ✅ 注入了 {injected_count} 个关键 Session 凭据！", flush=True)

        # 3. 访问服务器管理列表
        print(f"🚀 [步骤 3] 跳转到服务器列表: {SERVERS_URL} ...", flush=True)
        driver.get(SERVERS_URL)
        human_sleep(4.0, 6.0)

        # 4. 等待卡片渲染加载
        print("⏳ [步骤 4] 等待卡片与动态数据加载...", flush=True)
        for _ in range(15):
            body_text = driver.get_text("body")
            if "RENOVAR" in body_text or "Expira" in body_text:
                print("  🎯 成功识别到服务器卡片与 RENOVAR 按钮！", flush=True)
                break
            time.sleep(1)

        # 5. 解析卡片信息与电源检测
        cards_data = parse_server_cards(driver)
        power_status = check_power_and_start(driver, cards_data)

        status_text_list = [f"• <b>{c['name']}</b>: 状态 <code>{c['status']}</code> | 到期 <code>{c['exp_date']}</code>" for c in cards_data]
        status_before = "\n".join(status_text_list) if status_text_list else "服务器正常运行"
        print(f"📊 当前服务器状态:\n{status_before}\n⚡ 电源动作: {power_status}", flush=True)

        now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

        # 6. 定位卡片上的 RENOVAR 按钮
        renovar_buttons = driver.find_elements(By.XPATH, "//button[contains(., 'RENOVAR') or .//text()[contains(., 'RENOVAR')]]")

        if not renovar_buttons:
            print("ℹ️ 当前页面未找到可点击的 RENOVAR 按钮。", flush=True)
            driver.save_screenshot("ek_dashboard.png")
            tg_send(
                f"🛡️ <b>EKNodes 自动巡检正常</b>\n\n"
                f"⚡ <b>电源状态：</b><code>{power_status}</code>\n"
                f"📊 <b>实例状态：</b>\n{status_before}\n\n"
                f"⏭️ <b>执行结果：</b><code>周期已满 7 天 (维持满期)</code>\n"
                f"⏰ <b>巡检时间：</b><code>{now_time}</code>",
                photo_path="ek_dashboard.png"
            )
            return

        # 7. 循环处理各个服务器的续期
        renew_success = False
        for idx, btn in enumerate(renovar_buttons):
            server_name = cards_data[idx]["name"] if idx < len(cards_data) else f"Server-{idx+1}"
            print(f"👉 [步骤 5] 正在点击 [{server_name}] 的 RENOVAR 按钮...", flush=True)
            human_click(driver, btn)
            human_sleep(2.5, 3.5)

            # 等待 RENOVAR SERVIDOR 弹窗完全浮现
            try:
                driver.wait_for_element_visible("//div[contains(., 'RENOVAR SERVIDOR')]", timeout=10)
                print("  🪟 [RENOVAR SERVIDOR] 弹窗已展开！", flush=True)
            except Exception:
                pass

            # 穿透弹窗内的 Turnstile 验证框
            is_verified = solve_modal_turnstile(driver, timeout=30)
            human_sleep(1.0, 2.0)

            # 提交确认续期
            confirm_xpath = "//button[contains(., 'CONFIRMAR RENOVACIÓN') or contains(., 'Confirmar')]"
            confirm_btns = driver.find_elements(By.XPATH, confirm_xpath)
            if confirm_btns and confirm_btns[0].is_displayed():
                print("  🚀 拟真点击 [CONFIRMAR RENOVACIÓN] 确认续期！", flush=True)
                human_click(driver, confirm_btns[0])
                renew_success = True
                human_sleep(5.0, 7.0)
            else:
                print("  ⚠️ 未找到有效可点的确认续期按钮", flush=True)

        # 8. 刷新抓取最终结果
        driver.refresh()
        human_sleep(4.0, 6.0)
        cards_after = parse_server_cards(driver)
        status_after_list = [f"• <b>{c['name']}</b>: 状态 <code>{c['status']}</code> | 到期 <code>{c['exp_date']}</code>" for c in cards_after]
        status_after = "\n".join(status_after_list) if status_after_list else status_before
        driver.save_screenshot("ek_final.png")

        result_tag = "✅ 续期完成 (+7天)" if renew_success else "✅ 巡检正常 (当前维持满期)"
        tg_send(
            f"🎉 <b>EKNodes 巡检与续期报告</b>\n\n"
            f"⚡ <b>电源状态：</b><code>{power_status}</code>\n"
            f"⏳ <b>续期前状态：</b>\n{status_before}\n\n"
            f"⌛ <b>续期后状态：</b>\n{status_after}\n\n"
            f"📊 <b>执行结果：</b><code>{result_tag}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_time}</code>",
            photo_path="ek_final.png"
        )
        print("\n🎉 全部流程执行完毕，已推送到 Telegram！", flush=True)

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
