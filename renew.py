#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import html
import time
import socket
import subprocess
import requests
from datetime import datetime, timezone, timedelta
from seleniumbase import Driver
from selenium.webdriver.common.by import By

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
LOCAL_HTTP_PORT = 18080

SERVER_ID = "75e19d55"
SERVER_CONSOLE_URL = f"https://aclclouds.com/server/{SERVER_ID}"


# ── Telegram 通知 ──────────────────────────────────────────────
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


# ── gost 代理 ──────────────────────────────────────────────────
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
                print("✅ 本地 HTTP 代理连通性测试成功")
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地 HTTP 代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("启动 gost 代理...")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"✅ gost 已启动，本地 HTTP 代理端口：{LOCAL_HTTP_PORT}")
    return proc


def get_expire_info(driver) -> str:
    """提取页面到期信息"""
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


def main():
    socks5_proxy = os.environ.get("SOCKS5_PROXY", "").strip()
    gost_proc = None
    uc_proxy = None

    if socks5_proxy:
        try:
            gost_proc = start_gost(socks5_proxy)
            uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
            print("✅ 浏览器将通过代理访问。")
        except Exception as e:
            print(f"⚠️ 代理启动失败：{e}，将直接连接。")

    raw_cookies = os.environ.get("ACL_COOKIES", "").strip()
    if not raw_cookies:
        print("❌ 未找到 ACL_COOKIES 环境变量。")
        tg_send("🔴 <b>ACLClouds 续期通知</b>\n\n❌ 未找到 ACL_COOKIES 环境变量。")
        if gost_proc:
            gost_proc.terminate()
        return

    # 启动真实桌面的 Chrome (UC 模式)
    driver = Driver(uc=True, headless=False, proxy=uc_proxy)

    try:
        print("🌐 正在初始化访问 ACLClouds 域名...")
        driver.uc_open_with_reconnect("https://aclclouds.com", reconnect_time=4)
        time.sleep(2)

        # 注入 Cookie
        normalized = raw_cookies.replace("\n", ";").replace("\r", "")
        for item in normalized.split(";"):
            item = item.strip()
            if "=" in item:
                name, value = item.split("=", 1)
                driver.add_cookie({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": "aclclouds.com",
                    "path": "/"
                })
        print("✅ Cookie 注入完成，打开服务器控制台...")

        driver.get(SERVER_CONSOLE_URL)
        time.sleep(5)

        if "login" in driver.current_url or "signin" in driver.current_url:
            print("❌ Cookie 未生效，重定向到登录页。")
            driver.save_screenshot("dashboard_status.png")
            tg_send(
                "🔴 <b>ACLClouds 续期通知</b>\n\n"
                "❌ <b>登录失败</b>：Cookie 已过期，请重新获取并更新 Secret。",
                photo_path="dashboard_status.png",
            )
            return

        print(f"✅ 成功进入页面：{driver.current_url}")

        expire_info_before = get_expire_info(driver)
        print(f"⏳ 续期前服务器到期状态：{expire_info_before}")

        # 查找提示条里的 Renew 按钮并使用 UC 模式物理点击
        renew_xpath = "//button[contains(., 'Renew') or contains(., 'Renouveler')]"
        driver.wait_for_element_visible(renew_xpath, by=By.XPATH, timeout=20)
        renew_btn = driver.find_element(By.XPATH, renew_xpath)

        print("👉 在真实桌面中物理点击 Renew 按钮...")
        try:
            renew_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", renew_btn)
        time.sleep(3)

        # 如果有弹窗或 Cloudflare 验证，直接处理
        try:
            driver.uc_gui_click_captcha()
        except Exception:
            pass

        # 检查并点击弹窗确认按钮
        modal_clicked = driver.execute_script("""
            const modalBtns = Array.from(document.querySelectorAll('.swal2-confirm, div[role="dialog"] button, .modal button'));
            for (let b of modalBtns) {
                if (b.offsetWidth > 0 && b.offsetHeight > 0) {
                    b.click();
                    return true;
                }
            }
            return false;
        """)
        if modal_clicked:
            print("👉 已点击弹窗确认按钮！")
            time.sleep(3)

        # 刷新页面验证最新天数
        driver.refresh()
        time.sleep(4)

        expire_info_after = get_expire_info(driver)
        driver.save_screenshot("final_page.png")

        now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        tg_send(
            f"📋 <b>ACLClouds 续期执行结果 (真实浏览器版)</b>\n\n"
            f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜ <code>{html.escape(expire_info_after)}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now}</code>",
            photo_path="final_page.png",
        )
        print(f"\n任务执行完毕，最新状态: {expire_info_after}")

    except Exception as e:
        err_msg = str(e)
        print(f"❌ 执行异常: {err_msg}")
        try:
            driver.save_screenshot("dashboard_status.png")
        except Exception:
            pass
        tg_send(
            f"🔴 <b>ACLClouds 续期通知</b>\n\n❌ <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
            photo_path="dashboard_status.png",
        )
    finally:
        driver.quit()
        if gost_proc:
            gost_proc.terminate()
            print("gost 进程已终止。")


if __name__ == "__main__":
    main()
