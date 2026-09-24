#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# EKNodes 自动巡检与智能续期引擎 (修复版 v2)
# ------------------------------------------------------------
# 相对 v1 的修复:
#  1. 代理状态统一用 proxy_ready 标志: gost 启动失败时,
#     浏览器不再去连已经死掉的本地代理
#  2. 删除全部硬编码默认值: 解析失败直接告警退出,
#     不再用假数据("mi fghko"/默认日期/IP)触发续期
#  3. 续期结果真实验证: 点完 CONFIRMAR 后重新抓取到期日,
#     日期变大才算成功, 否则按失败告警
#  4. Cookie 注入失败会打印名字, 并尝试 httpOnly 方式重试
#  5. 登录态校验: 检查重定向到登录页 / WAF 拦截页 / 非 200 状态
#  6. Turnstile 30 秒未通过则直接失败, 不再盲点确认按钮
#  7. 状态卡片去掉写死的 CPU/RAM/磁盘假数据, 改为真实剩余天数
# ------------------------------------------------------------
# GitHub Actions 运行要求:
#  - 安装 gost (仅当使用 SOCKS5_PROXY 时)
#  - Xvfb (HEADLESS=false 时必须; 或设置 HEADLESS=true)
#  Secrets: EK_COOKIE, TG_BOT_TOKEN, TG_CHAT_ID
#  可选: SOCKS5_PROXY, FORCE_RENEW=true, HEADLESS=true
# ============================================================
import html
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

import requests
from PIL import Image, ImageDraw

BASE_URL = "https://dash.eknodes.es"
SERVERS_URL = f"{BASE_URL}/servers"
LOCAL_HTTP_PORT = 18080

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
EK_COOKIE = os.environ.get("EK_COOKIE", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()
FORCE_RENEW = os.environ.get("FORCE_RENEW", "false").lower() == "true"
HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

WAF_MARKERS = (
    "Failed to verify your browser",
    "Vercel Security",
    "Attention Required",
    "cf-challenge",
    "Verifying you are human",
)


class EKError(Exception):
    """可预期的业务异常, 会推送到 TG。"""


class ParseError(EKError):
    pass


class NotLoggedInError(EKError):
    pass


# ---------------- Telegram ----------------

def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID, 跳过通知。", flush=True)
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
            print("TG 通知推送成功", flush=True)
        else:
            print(f"TG 返回码 {resp.status_code}: {resp.text[:200]}", flush=True)
    except Exception as e:
        print(f"TG 发送异常: {e}", flush=True)


# ---------------- 代理 ----------------

def normalize_socks5_proxy(proxy_value: str) -> str:
    proxy_value = (proxy_value or "").strip()
    for prefix in ("socks5://", "socks://"):
        if proxy_value.startswith(prefix):
            proxy_value = proxy_value[len(prefix):]
            break
    if not proxy_value or ":" not in proxy_value:
        raise ValueError("SOCKS5_PROXY 格式错误, 应为 host:port 或 user:pass@host:port。")
    return proxy_value


def wait_http_proxy_ready(port: int, timeout: int = 15):
    proxies = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    last_error = None
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=8)
            if resp.ok:
                print("本地 HTTP 代理连通性测试成功", flush=True)
                return
        except Exception as e:
            last_error = e
        time.sleep(1)
    raise RuntimeError(f"本地代理就绪检测失败: {last_error}")


def start_gost(socks_proxy: str) -> subprocess.Popen:
    normalized = normalize_socks5_proxy(socks_proxy)
    cmd = ["gost", "-L", f"http://127.0.0.1:{LOCAL_HTTP_PORT}", "-F", f"socks5://{normalized}"]
    print("启动 gost 代理中转...", flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError("gost 启动失败, 请检查代理格式 / gost 是否已安装。")
    wait_http_proxy_ready(LOCAL_HTTP_PORT)
    print(f"gost 已启动, 本地代理端口: {LOCAL_HTTP_PORT}", flush=True)
    return proc


# ---------------- Cookie ----------------

def extract_cookies(raw_input: str) -> dict:
    cookie_str = raw_input
    match_h = re.search(r"(?i)-H\s+['\"]cookie:\s*(.*?)['\"]", raw_input)
    if match_h:
        cookie_str = match_h.group(1)
    else:
        match_b = re.search(r"(?i)-b\s+['\"](.*?)['\"]", raw_input)
        if match_b:
            cookie_str = match_b.group(1)

    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        cookies[k.strip()] = v.strip()
    return cookies


# ---------------- 页面解析 ----------------

MONTHS = {
    "ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12,
}

EXP_RE = re.compile(
    r"([0-9]{1,2})\s+(ene|jan|feb|mar|abr|apr|may|jun|jul|ago|aug|sep|sept|oct|nov|dic|dec)[a-z]*\s+([0-9]{4})",
    re.IGNORECASE,
)


def parse_exp_datetime(exp_date_str: str) -> datetime:
    m = EXP_RE.search(exp_date_str or "")
    if not m:
        raise ParseError(f"无法解析到期日期: {exp_date_str!r}")
    day, mon_str, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    mon = MONTHS.get(mon_str)
    if not mon:
        raise ParseError(f"未知月份: {mon_str!r}")
    try:
        return datetime(year, mon, day, tzinfo=timezone.utc)
    except ValueError as e:
        raise ParseError(f"非法日期 {exp_date_str!r}: {e}")


def parse_days_remaining(exp_date_str: str) -> int:
    exp_dt = parse_exp_datetime(exp_date_str)
    now_dt = datetime.now(timezone.utc)
    return max((exp_dt - now_dt).days, 0)


def parse_server_page(html_text: str) -> dict:
    """解析服务器列表页。关键字段缺失则抛 ParseError, 不再使用假默认值。"""
    if not html_text or len(html_text) < 500:
        raise ParseError("页面内容过短, 可能被拦截或未登录")

    names = re.findall(
        r'<h3[^>]*>([^<]+)</h3>|<div[^>]*class="[^"]*font-(?:bold|semibold)[^"]*"[^>]*>([^<]+)</div>',
        html_text,
    )
    server_name = None
    for n1, n2 in names:
        val = (n1 or n2).strip()
        if val and val not in ("SERVIDORES", "Inicio", "Servidores", "Tienda", "Soporte"):
            server_name = val
            break
    if not server_name:
        raise ParseError("未找到服务器名称, 页面结构可能变化或 Cookie 未登录")

    m_exp = EXP_RE.search(html_text)
    if not m_exp:
        raise ParseError("未找到到期日期, 页面结构可能变化或 Cookie 未登录")
    exp_date = m_exp.group(0).strip()
    # 确保日期真的能解析, 避免脏数据
    parse_exp_datetime(exp_date)

    m_ip = re.search(r"([a-zA-Z0-9.\-_]+\.eknodes\.es:[0-9]+)", html_text)
    node_ip = m_ip.group(1).strip() if m_ip else "未知"

    status_tag = "未知"
    if "Iniciando" in html_text:
        status_tag = "Iniciando"
    elif any(k in html_text for k in ("Inactivo", "Detenido", "Apagado")):
        status_tag = "Offline"
    elif "Online" in html_text:
        status_tag = "Online"

    return {"name": server_name, "exp": exp_date, "ip": node_ip, "status": status_tag}


def fetch_server_page(session: requests.Session) -> str:
    """带登录态校验的页面抓取。"""
    try:
        resp = session.get(SERVERS_URL, timeout=20)
    except Exception as e:
        raise EKError(f"请求服务器列表失败: {e}")
    print(f"HTTP {resp.status_code} | 最终地址: {resp.url} | 长度: {len(resp.text)}", flush=True)

    if "login" in resp.url.lower():
        raise NotLoggedInError("被重定向到登录页, EK_COOKIE 可能已失效, 请更新 Secrets")
    if resp.status_code != 200:
        raise EKError(f"服务器列表返回 HTTP {resp.status_code}")
    for marker in WAF_MARKERS:
        if marker in resp.text:
            raise NotLoggedInError(
                f"命中 WAF 拦截({marker}), EK_COOKIE 可能失效或出口 IP 被拦截, 请更新 Secrets"
            )
    return resp.text


# ---------------- 状态卡片(只用真实数据) ----------------

def generate_status_image(server_name, status_tag, exp_date, days_left, node_ip,
                          result_tag, output_path="ek_card.png"):
    width, height = 760, 480
    img = Image.new("RGB", (width, height), color="#0b1120")
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([35, 30, width - 35, height - 30], radius=16,
                           fill="#111827", outline="#1f2937", width=2)
    draw.text((65, 55), server_name, fill="#f9fafb")

    badge_bg, badge_fg = {
        "Online": ("#064e3b", "#34d399"),
        "Offline": ("#7f1d1d", "#f87171"),
        "Iniciando": ("#78350f", "#fbbf24"),
    }.get(status_tag, ("#374151", "#9ca3af"))
    draw.rounded_rectangle([width - 190, 52, width - 65, 82], radius=14, fill=badge_bg)
    draw.text((width - 165, 60), status_tag, fill=badge_fg)

    draw.line([(65, 105), (width - 65, 105)], fill="#1f2937", width=1)

    draw.text((65, 130), "IP / PUERTO", fill="#6b7280")
    draw.text((240, 130), str(node_ip), fill="#e5e7eb")

    draw.text((65, 175), "EXPIRACION", fill="#6b7280")
    draw.text((240, 175), str(exp_date), fill="#38bdf8")

    draw.text((65, 220), "DIAS RESTANTES", fill="#6b7280")
    days_color = "#f87171" if days_left <= 3 else "#34d399"
    draw.text((240, 220), f"{days_left} dias", fill=days_color)

    draw.text((65, 265), "RESULTADO", fill="#6b7280")
    draw.text((240, 265), str(result_tag)[:58], fill="#e5e7eb")

    draw.rounded_rectangle([65, 320, 255, 362], radius=8, fill="#1f2937", outline="#374151")
    draw.text((110, 332), "RENOVAR", fill="#9ca3af")

    img.save(output_path)
    return output_path


# ---------------- 浏览器交互 ----------------

def physical_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
        time.sleep(0.3)
    except Exception:
        pass
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(driver).move_to_element(element).pause(0.2).click().perform()
        return
    except Exception:
        pass
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def inject_cookies(driver, cookies_dict):
    """注入 Cookie, 失败的逐个打印名字, 并尝试 httpOnly 方式重试。"""
    failed = []
    for k, v in cookies_dict.items():
        ok = False
        attempts = [
            {"name": k, "value": v, "domain": "dash.eknodes.es", "path": "/"},
            {"name": k, "value": v, "path": "/"},
            {"name": k, "value": v, "domain": "dash.eknodes.es", "path": "/", "httpOnly": True},
        ]
        for params in attempts:
            try:
                driver.add_cookie(params)
                ok = True
                break
            except Exception:
                continue
        if not ok:
            failed.append(k)
    if failed:
        print(f"警告: {len(failed)} 个 Cookie 注入失败: {failed}", flush=True)
    else:
        print(f"已注入 {len(cookies_dict)} 个 Cookie", flush=True)
    return failed


def shield_clear(driver, rounds=6):
    """Vercel 拦截盾处理, 返回是否通过。"""
    for i in range(rounds):
        time.sleep(2)
        try:
            src = driver.page_source
        except Exception:
            continue
        if not any(m in src for m in WAF_MARKERS):
            return True
        print(f"遭遇 Vercel 拦截盾, 尝试破盾 ({i + 1}/{rounds})...", flush=True)
        try:
            driver.uc_gui_click_captcha()
        except Exception:
            pass
        time.sleep(3)
    return False


def find_button(driver, xpaths, timeout=20):
    from selenium.webdriver.common.by import By
    for _ in range(timeout):
        time.sleep(1)
        for xp in xpaths:
            try:
                elems = driver.find_elements(By.XPATH, xp)
                for el in elems:
                    if el.is_displayed() and len(el.text.strip()) < 40:
                        return el
            except Exception:
                pass
    return None


def perform_browser_renew(cookies_dict, proxy_ready, old_exp_str):
    """返回 (ok, msg, new_exp_str|None)。ok=True 仅当验证到期日确实延后。"""
    from seleniumbase import Driver

    print("启动浏览器进行续期...", flush=True)
    proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}" if proxy_ready else None
    if proxy:
        print(f"浏览器走代理: {proxy}", flush=True)
    else:
        print("浏览器走直连", flush=True)
    driver = Driver(uc=True, headless=HEADLESS, proxy=proxy, uc_subprocess=True)

    if os.path.exists("real_browser_error.png"):
        os.remove("real_browser_error.png")

    try:
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=4)
        time.sleep(2)

        inject_cookies(driver, cookies_dict)

        print(f"打开服务器列表页: {SERVERS_URL} ...", flush=True)
        driver.uc_open_with_reconnect(SERVERS_URL, reconnect_time=6)

        if not shield_clear(driver):
            driver.save_screenshot("real_browser_error.png")
            return False, "Vercel 拦截盾未通过 (已截图)", None

        print("寻找续期按钮...", flush=True)
        target_btn = find_button(driver, [
            "//button[contains(., 'RENOVAR') or contains(., 'Renovar') or contains(., 'RENEW') or contains(., 'Renew')]",
            "//a[contains(., 'RENOVAR') or contains(., 'Renovar') or contains(., 'RENEW') or contains(., 'Renew')]",
        ])
        if not target_btn:
            driver.save_screenshot("real_browser_error.png")
            return False, "未找到 RENOVAR/RENEW 续期按钮 (已截图)", None

        print(f"点击 [{target_btn.text.strip()}] 唤出弹窗...", flush=True)
        physical_click(driver, target_btn)
        time.sleep(3)

        print("等待 Turnstile 验证...", flush=True)
        turnstile_ok = False
        start_t = time.time()
        while time.time() - start_t < 30:
            body_text = driver.execute_script("return document.body ? document.body.innerText : '';")
            token_val = driver.execute_script(
                "var el = document.querySelector('[name=\"cf-turnstile-response\"]');"
                " return el ? el.value : '';"
            )
            if "Verificación completada" in body_text or (token_val and len(token_val) > 20):
                print("Turnstile 验证通过", flush=True)
                turnstile_ok = True
                break
            try:
                driver.uc_gui_click_cf()
            except Exception:
                pass
            time.sleep(2)
        if not turnstile_ok:
            driver.save_screenshot("real_browser_error.png")
            return False, "Turnstile 验证 30 秒未通过 (已截图)", None

        confirm_btn = find_button(driver, [
            "//button[contains(., 'CONFIRMAR') or contains(., 'Confirmar') or contains(., 'CONFIRM') or contains(., 'Confirm')]",
        ], timeout=15)
        if not confirm_btn:
            driver.save_screenshot("real_browser_error.png")
            return False, "未找到 CONFIRMAR 确认按钮 (已截图)", None

        print(f"点击 [{confirm_btn.text.strip()}] 提交续期...", flush=True)
        physical_click(driver, confirm_btn)
        time.sleep(6)

        # ---- 真实验证: 重新抓取到期日, 变大才算成功 ----
        print("验证续期结果: 重新抓取到期日...", flush=True)
        driver.uc_open_with_reconnect(SERVERS_URL, reconnect_time=6)
        shield_clear(driver, rounds=3)
        try:
            info = parse_server_page(driver.page_source)
            new_dt = parse_exp_datetime(info["exp"])
            old_dt = parse_exp_datetime(old_exp_str)
            if new_dt > old_dt:
                return True, "续期成功", info["exp"]
            driver.save_screenshot("real_browser_error.png")
            return False, f"续期疑似未生效 (到期日仍为 {info['exp']}, 已截图)", info["exp"]
        except Exception as e:
            driver.save_screenshot("real_browser_error.png")
            return False, f"续期后验证失败: {e} (已截图)", None
    except Exception as e:
        try:
            driver.save_screenshot("real_browser_error.png")
        except Exception:
            pass
        return False, f"浏览器续期异常: {e}", None
    finally:
        driver.quit()


# ---------------- 主流程 ----------------

def main():
    print("=" * 45, flush=True)
    print(" EKNodes 自动巡检与续期调度 (修复版 v2)", flush=True)
    print("=" * 45, flush=True)

    if not EK_COOKIE:
        print("未在 Secrets 中配置 EK_COOKIE!", flush=True)
        return

    proxy_ready = False
    gost_proc = None
    proxies = None

    if SOCKS5_PROXY:
        try:
            gost_proc = start_gost(SOCKS5_PROXY)
            proxies = {
                "http": f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
                "https": f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
            }
            proxy_ready = True
            print("代理已挂载生效 (请求 + 浏览器都会走代理)。", flush=True)
        except Exception as e:
            print(f"代理启动异常: {e}, 请求与浏览器都将采用直连。", flush=True)

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    })

    cookies_dict = extract_cookies(EK_COOKIE)
    if not cookies_dict:
        print("EK_COOKIE 解析出 0 个 Cookie, 请检查格式!", flush=True)
        return
    session.cookies.update(cookies_dict)
    print(f"已装配 {len(cookies_dict)} 个 Session 凭据", flush=True)

    now_time = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        print("正在获取服务器列表...", flush=True)
        html_text = fetch_server_page(session)
        info = parse_server_page(html_text)

        days_left = parse_days_remaining(info["exp"])
        server_info = (
            f"• <b>{html.escape(info['name'])}</b>: 状态 <code>{html.escape(info['status'])}</code> | "
            f"到期 <code>{html.escape(info['exp'])}</code> (剩 <b>{days_left}</b> 天)"
        )
        print(f"提取到的数据:\n{server_info}\n地址: {info['ip']}", flush=True)

        if days_left > 3 and not FORCE_RENEW:
            print(f"剩余 {days_left} 天, 无需续期。", flush=True)
            result_tag = f"周期充足 ({days_left}天), 无需续期"
            final_photo = generate_status_image(
                info["name"], info["status"], info["exp"], days_left, info["ip"], result_tag)
        else:
            print(f"剩余 {days_left} 天, 触发续期流程...", flush=True)
            ok, msg, new_exp = perform_browser_renew(cookies_dict, proxy_ready, info["exp"])
            if ok:
                new_days = parse_days_remaining(new_exp)
                result_tag = f"续期成功, 到期延至 {new_exp} (剩 {new_days} 天)"
                print(result_tag, flush=True)
                final_photo = generate_status_image(
                    info["name"], info["status"], new_exp, new_days, info["ip"], "续期成功")
            else:
                result_tag = msg
                print(f"续期失败: {msg}", flush=True)
                if os.path.exists("real_browser_error.png"):
                    final_photo = "real_browser_error.png"
                else:
                    final_photo = generate_status_image(
                        info["name"], info["status"], info["exp"], days_left, info["ip"], msg)

        tg_send(
            f"<b>EKNodes 服务器巡检与续期报告</b>\n\n"
            f"<b>实例状态:</b>\n{server_info}\n\n"
            f"<b>连接地址:</b> <code>{html.escape(info['ip'])}</code>\n"
            f"<b>执行结果:</b> <code>{html.escape(result_tag)}</code>\n"
            f"<b>巡检时间:</b> <code>{now_time}</code>",
            photo_path=final_photo,
        )
        print("流程完成, 通知已推送。", flush=True)

    except EKError as e:
        err = str(e)
        print(f"执行异常: {err}", flush=True)
        tg_send(f"<b>EKNodes 巡检异常</b>\n\n<code>{html.escape(err)}</code>\n\n请检查 EK_COOKIE 或页面结构。")
    except Exception as e:
        err = str(e)
        print(f"未知异常: {err}", flush=True)
        tg_send(f"<b>EKNodes 巡检未知异常</b>\n\n<code>{html.escape(err)}</code>")
    finally:
        if gost_proc:
            gost_proc.terminate()
            print("gost 代理已退出。", flush=True)


if __name__ == "__main__":
    main()
