#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# eknodes (dash.eknodes.es) 免费游戏服务器自动续期脚本
#
# 关键事实 (2026-10-06 调研):
# 1. 面板是 Next.js + Vercel, 入口 https://dash.eknodes.es/login
#    未登录访问 /servers 会被重定向回 /login。
#    首次访问有 Vercel 安全检查 (challenge.v2.min.js),
#    UC 模式真浏览器一般自动放行, 个别情况是 "按住确认" 按钮。
# 2. 登录表单: input#email / input#password,
#    提交按钮文字 "Iniciar Sesión" (另有 Discord OAuth, 本脚本不用)。
#    页面加载了 Turnstile api.js (render=explicit):
#    登录用的是隐形/交互挑战, 提交后若弹复选框需要点。
# 3. /servers 服务器卡片: 名称 / 状态胶囊(Iniciando...) / IP /
#    "Expira 13 oct 2026" / [GESTIONAR] [RENOVAR]。
#    点 RENOVAR 出弹窗 "RENOVAR SERVIDOR",
#    里面是【可见的 Cloudflare Turnstile 复选框】,
#    验证通过后文案变 "Verificación completada.",
#    CONFIRMAR RENOVACIÓN 按钮才会真正启用;
#    成功后显示 "¡Servidor renovado! Se han añadido 7 días de vigencia."
#    (弹窗这段 "añadido 7 días" 文案是误导, 实际不是一次 +7)
# 4. 续期规则(用户实测纠正): 每次点续期到期日只 +1 天, 每天限一次,
#    剩余天数硬上限 7 天。策略: 只要剩余 < 7 天就点一次把天数顶到上限,
#    一天巡检 2 次 (Turnstile 失败有当晚第二轮机会;
#    当天已续过会被平台拒绝, 属正常, 不算失败)。
#
# 运行: GitHub Actions + Xvfb, headless=False, uc=True。
# Secrets: EK_EMAIL EK_PASSWORD TG_BOT_TOKEN TG_CHAT_ID
# 可选:    PROXY_SERVER(http://host:port 或 socks5://host:port, 无账密)
# Vars:    EK_MAX_DAYS (剩余天数上限, 默认 7)
# ============================================================
import html
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta

import requests
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

# ------------------------------------------------------------
# 配置
# ------------------------------------------------------------
BASE_URL = "https://dash.eknodes.es"
LOGIN_URL = f"{BASE_URL}/login"
SERVERS_URL = f"{BASE_URL}/servers"

EK_EMAIL = os.environ.get("EK_EMAIL", "").strip()
EK_PASSWORD = os.environ.get("EK_PASSWORD", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip()

# GitHub Actions 未设置的 vars 会注入空字符串, 必须空串回退默认值
def _env_int(name, default):
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default

# 续期规则(用户实测): 每天可点一次, 每次到期日 +1 天, 剩余天数硬上限 7 天
MAX_DAYS = _env_int("EK_MAX_DAYS", 7)

# requests 走代理时让 socks5 的 DNS 也走代理
REQ_PROXIES = None
if PROXY_SERVER:
    _p = PROXY_SERVER.replace("socks5://", "socks5h://").replace("socks://", "socks5h://")
    REQ_PROXIES = {"http": _p, "https": _p}

# 西班牙语月份
ES_MONTHS = {
    "ene": 1, "enero": 1,
    "feb": 2, "febrero": 2,
    "mar": 3, "marzo": 3,
    "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "junio": 6,
    "jul": 7, "julio": 7,
    "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "septiembre": 9,
    "oct": 10, "octubre": 10,
    "nov": 11, "noviembre": 11,
    "dic": 12, "diciembre": 12,
}

# 卡片状态词 (西/英), 仅用于 TG 展示
STATUS_WORDS = (
    "iniciando", "activo", "online", "running", "reiniciando",
    "detenido", "apagado", "offline", "inactivo", "stopped",
    "starting", "stopping", "suspendido", "suspended",
)

BEIJING_TZ = timezone(timedelta(hours=8))


def log(msg):
    now = datetime.now(BEIJING_TZ).strftime("%H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------
def tg_send(text, photo_path=None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("⚠️ 未配置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知")
        return
    try:
        if photo_path and os.path.exists(photo_path) and os.path.getsize(photo_path) > 1000:
            with open(photo_path, "rb") as f:
                resp = requests.post(
                    f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto",
                    data={"chat_id": TG_CHAT_ID, "caption": text, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=30, proxies=REQ_PROXIES,
                )
        else:
            resp = requests.post(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=30, proxies=REQ_PROXIES,
            )
        if resp.status_code == 200:
            log("✅ TG 通知发送成功")
        else:
            log(f"⚠️ TG 通知失败: {resp.text[:300]}")
    except Exception as e:
        log(f"⚠️ TG 通知异常: {e}")


def shot(driver, name):
    try:
        driver.save_screenshot(name)
        return name
    except Exception:
        return None


# ------------------------------------------------------------
# 通用交互
# ------------------------------------------------------------
def fill_input(driver, element, value):
    """真人键盘输入 (React 受控组件最稳), 原生 setter 兜底"""
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
    driver.execute_script(
        """
        const el = arguments[0], val = arguments[1];
        el.focus();
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
        """,
        element, value,
    )


def real_click(driver, element):
    """ActionChains 物理点击 + JS 兜底"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.3)
        ActionChains(driver).move_to_element(element).pause(0.25).click().perform()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            return False


# ------------------------------------------------------------
# Vercel 安全检查 / Cloudflare Turnstile
# ------------------------------------------------------------
# Vercel WAF 拦截页的典型文案 (脚本本身全站常驻, 不能拿 script 标签当判据)
VERCEL_HINT_RE = re.compile(
    r"press\s*(?:&|and)?\s*hold|hold to confirm|verifying (you|that)|"
    r"you are human|comprobando|verificando tu|un momento, por favor|"
    r"please wait while we|security checkpoint",
    re.IGNORECASE,
)

# 浏览器校验失败文案 (出现说明质询已跑完但被拒, 刷新重跑有新机会)
VERCEL_FAILED_RE = re.compile(
    r"failed to verify|verification failed|no se pudo verificar",
    re.IGNORECASE,
)


def vercel_failed(driver):
    """质询已跑完但浏览器被拒 (Failed to verify your browser)"""
    try:
        body = (driver.get_text("body") or "")[:600]
        return bool(VERCEL_FAILED_RE.search(body))
    except Exception:
        return False


def vercel_checkpoint(driver):
    """只有出现真正的挑战页文案才算; 正常登录页/服务器页一律 false"""
    try:
        if driver.find_elements(By.CSS_SELECTOR, "input#email, input[name='email']"):
            return False
        if driver.find_elements(By.CSS_SELECTOR, "[data-ek-renew]"):
            return False
        body = (driver.get_text("body") or "")[:600]
        if VERCEL_HINT_RE.search(body):
            return True
        # 兜底: 存在专门的挑战容器且页面没有任何业务内容
        has_box = bool(driver.execute_script(
            "return !!document.querySelector("
            "'[id*=\"challenge-stage\"], [class*=\"challenge\"], "
            "iframe[src*=\"vercel/security\"]');"))
        return has_box and len(body.strip()) < 200
    except Exception:
        return False


def wait_vercel(driver, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if not vercel_checkpoint(driver):
            return True
        # 个别 WAF 策略是 "按住按钮" 挑战, 尝试物理按住 2.5s
        try:
            for el in driver.find_elements(
                    By.CSS_SELECTOR, "button, [role='button'], iframe, canvas"):
                if el.is_displayed() and el.size.get("width", 0) > 60:
                    try:
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});", el)
                        ActionChains(driver).move_to_element(el).pause(0.3) \
                            .click_and_hold().pause(2.5).release().perform()
                        log("  🛡️ 尝试按住 Vercel 挑战按钮")
                        time.sleep(3)
                        break
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(2)
    return not vercel_checkpoint(driver)


def visible_turnstiles(driver):
    """返回页面上真正可见的 Turnstile iframe (隐形的尺寸很小, 会被过滤)"""
    out = []
    try:
        for f in driver.find_elements(
                By.CSS_SELECTOR, "iframe[src*='challenges.cloudflare.com']"):
            try:
                if f.is_displayed() and f.size.get("width", 0) > 180 \
                        and f.size.get("height", 0) > 40:
                    out.append(f)
            except Exception:
                pass
    except Exception:
        pass
    return out


def click_turnstile(driver, where=""):
    """先 SeleniumBase 图像级点击 (Xvfb 下可穿透跨域 iframe), 再坐标兜底"""
    # 1) uc_gui_click_captcha: 内部识别 Turnstile 复选框位置并 OS 级点击
    try:
        driver.uc_gui_click_captcha()
        log(f"  👉 [{where}] uc_gui_click_captcha 已点击 Turnstile")
        time.sleep(5)
        return True
    except Exception as e:
        log(f"  ℹ️ [{where}] uc_gui_click_captcha 不可用: {str(e)[:120]}")
    # 2) 坐标兜底: 复选框在小部件左侧约 35px 处 (iframe 宽约 300)
    frames = visible_turnstiles(driver)
    if not frames:
        return False
    f = frames[0]
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", f)
        time.sleep(0.4)
        ActionChains(driver).move_to_element(f).pause(0.2) \
            .move_by_offset(-115, 0).click().perform()
        log(f"  👉 [{where}] 坐标兜底点击 Turnstile 复选框")
        time.sleep(5)
        return True
    except Exception as e:
        log(f"  ⚠️ [{where}] 坐标点击失败: {str(e)[:120]}")
        return False


# ------------------------------------------------------------
# 登录
# ------------------------------------------------------------
EMAIL_SEL = "input#email, input[name='email'], input[type='email']"
PWD_SEL = "input#password, input[name='password'], input[type='password']"


def page_has_email(driver):
    try:
        return bool(driver.find_elements(By.CSS_SELECTOR, EMAIL_SEL))
    except Exception:
        return False


def do_login(driver):
    log(f"🌐 打开登录页: {LOGIN_URL}")

    def _wait_stable(max_wait=55):
        """循环等: URL 是 login 且能看到邮箱框; 中间卡住 Vercel/CF 就等"""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            cur = (driver.current_url or "").lower()
            # 被重定向到安全检查页就等自动放行
            if not cur.rstrip("/").endswith("/login"):
                log(f"  ⏳ 当前 URL: {driver.current_url}，等待跳转回 login...")
                time.sleep(2.5)
                continue
            if page_has_email(driver):
                return True
            # 质询已跑完但浏览器被拒: 重新加载页面重跑质询
            if vercel_failed(driver):
                log("  🛡️ Vercel 校验被拒 (Failed to verify)，刷新重跑质询...")
                driver.get(LOGIN_URL)
                time.sleep(6)
                continue
            # 卡住常见原因: Vercel 检查页还在或 Turnstile 加载中
            if vercel_checkpoint(driver):
                log("  🛡️ 命中 Vercel 安全检查, 等待自动放行...")
                wait_vercel(driver, timeout=20)
                time.sleep(2)
                continue
            # 也可能是隐形 Turnstile 正在加载
            if visible_turnstiles(driver):
                click_turnstile(driver, where="登录页")
                time.sleep(3)
                continue
            # 页面存在但 email 仍不可见: 等 JS 渲染完成
            log("  ⏳ 页面已加载，等待邮箱输入框出现...")
            time.sleep(2)
        return page_has_email(driver)

    for retry in (1, 2, 3, 4):
        driver.get(LOGIN_URL)
        time.sleep(6 if retry == 1 else 4)
        if _wait_stable(max_wait=50 if retry == 1 else 35):
            break
        if retry == 4:
            # 诊断: 记录当时页面状态, 便于判断卡在哪个环节
            try:
                log(f"  🔍 卡住诊断: title={driver.get_title()!r} "
                    f"url={driver.current_url!r}")
                body = (driver.get_text("body") or "").strip().replace("\n", " | ")
                log(f"  🔍 页面文本: {body[:300] or '(空白)'}")
            except Exception:
                pass
            shot(driver, "ek_login_stuck.png")
            log("  ❌ 连续两次打开登录页均无法找到邮箱框，疑似被安全检查拦截")
            return False

    email_el = driver.find_element(By.CSS_SELECTOR, EMAIL_SEL)
    fill_input(driver, email_el, EK_EMAIL)
    pwd_el = driver.find_element(By.CSS_SELECTOR, PWD_SEL)
    fill_input(driver, pwd_el, EK_PASSWORD)
    log(f"  📝 已填入凭据: {EK_EMAIL[:3]}***")
    time.sleep(0.8)

    for attempt in range(5):
        log(f"🔑 提交登录（第 {attempt + 1} 次）...")
        try:
            btn = driver.find_element(
                By.XPATH,
                "//button[@type='submit' or contains(translate(normalize-space(.),"
                " 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),"
                " 'iniciar sesión') or contains(.,'Iniciar Sesión')]")
            real_click(driver, btn)
        except Exception:
            try:
                driver.find_element(By.CSS_SELECTOR, "button[type='submit']").submit()
            except Exception:
                pass
        # 等跳转, 期间处理跳出来的交互 Turnstile
        for _ in range(12):
            time.sleep(1.5)
            url = (driver.current_url or "").lower()
            if "/login" not in url:
                log(f"  ✅ 登录成功 → {driver.current_url}")
                return True
            if visible_turnstiles(driver):
                click_turnstile(driver, where="登录页")
        # 页面级错误提示
        try:
            body = (driver.get_text("body") or "").lower()
            if any(k in body for k in
                   ("credenciales", "incorrect", "contraseña", "invalid",
                    "no existe", "demasiados intentos", "too many")):
                for line in body.split("\n"):
                    s = line.strip()
                    if s and any(k in s for k in
                                 ("credencial", "incorrect", "contraseña",
                                  "invalid", "intentos", "existe")):
                        log(f"  ❌ 页面报错: {s[:150]}")
                        break
        except Exception:
            pass
    return False


# ------------------------------------------------------------
# 服务器卡片识别
# ------------------------------------------------------------
READ_CARDS_JS = r"""
const onlyEnabled = arguments[0];
document.querySelectorAll('[data-ek-renew]').forEach(e =>
    e.removeAttribute('data-ek-renew'));
const btns = [...document.querySelectorAll('button, a')].filter(el => {
    const t = (el.innerText || '').trim().toLowerCase();
    if (!t.includes('renovar') || el.offsetParent === null) return false;
    const disabled = !!el.disabled
        || el.getAttribute('aria-disabled') === 'true';
    return onlyEnabled ? !disabled : true;
});
return btns.map((btn, idx) => {
    let card = btn;
    for (let i = 0; i < 9; i++) {
        const p = card.parentElement;
        if (!p) break;
        card = p;
        if (/expira/i.test(card.innerText || '')) break;
    }
    btn.setAttribute('data-ek-renew', String(idx));
    const nameEl = card.querySelector('h1,h2,h3,h4,strong,b');
    return {
        index: idx,
        name: nameEl ? (nameEl.innerText || '').trim() : '',
        disabled: !!btn.disabled || btn.getAttribute('aria-disabled') === 'true',
        text: (card.innerText || '')
    };
});
"""

EXPIRA_RE = re.compile(
    # 标签和日期可能换行/冒号分隔; 月份西语, 兼容 "09 oct 2026" / "9 de octubre de 2026"
    r"expira[\s:.\-–]{0,25}(\d{1,2})\s+(?:de\s+)?"
    r"([a-záéíóúñ]{3,10})\.?\s+(?:de\s+)?(\d{4})",
    re.IGNORECASE,
)
IP_RE = re.compile(r"([a-z0-9][\w.-]*\.[a-z]{2,}:\d{2,5})", re.IGNORECASE)


def parse_expiry(text):
    m = EXPIRA_RE.search(text.lower())
    if not m:
        return None
    day, mon_raw, year = int(m.group(1)), m.group(2).strip("."), int(m.group(3))
    # 去掉西语重音
    mon = mon_raw.replace("á", "a").replace("é", "e").replace("í", "i") \
                 .replace("ó", "o").replace("ú", "u")
    month = ES_MONTHS.get(mon)
    if not month:
        return None
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).date()
    except ValueError:
        return None


def parse_status(text):
    for line in text.split("\n"):
        s = line.strip().lower()
        if s in STATUS_WORDS:
            return s
    return "—"


def read_cards(driver, only_enabled=True):
    raw = driver.execute_script(READ_CARDS_JS, only_enabled) or []
    today = datetime.now(timezone.utc).date()
    cards = []
    for item in raw:
        text = item.get("text", "")
        exp = parse_expiry(text)
        ip_m = IP_RE.search(text)
        name = item.get("name") or "servidor"
        cards.append({
            "index": item["index"],
            "name": name.strip()[:60],
            "disabled": bool(item.get("disabled")),
            "status": parse_status(text),
            "ip": ip_m.group(1) if ip_m else "—",
            "expire": exp,
            "expire_str": exp.strftime("%Y-%m-%d") if exp else "未知",
            "left": (exp - today).days if exp else None,
        })
    return cards


# ------------------------------------------------------------
# 续期弹窗 + Turnstile
# ------------------------------------------------------------
def confirm_button(driver):
    """弹窗里的 CONFIRMAR RENOVACIÓN 按钮 (只在弹窗容器内找, 避免误点背景)"""
    xpath = ("(//div[@role='dialog'] | //*[contains(@class,'modal')])"
             "//button | (//div[@role='dialog'] | //*[contains(@class,'modal')])"
             "//*[@role='button']")
    for b in driver.find_elements(By.XPATH, xpath):
        try:
            t = (b.text or "").strip().lower()
            if "confirmar renovaci" in t and b.is_displayed():
                return b
        except Exception:
            pass
    # 兜底: 全页按钮 (前两层容器里含 "renovar servidor" 标题的才算)
    for b in driver.find_elements(By.XPATH, "//button"):
        try:
            t = (b.text or "").strip().lower()
            if "confirmar renovaci" in t and b.is_displayed():
                return b
        except Exception:
            pass
    return None


def confirm_enabled(btn):
    try:
        if btn.get_property("disabled"):
            return False
        if btn.get_attribute("aria-disabled") == "true":
            return False
        cls = (btn.get_attribute("class") or "").lower()
        if "disabled" in cls or "opacity-50" in cls or "pointer-events-none" in cls:
            return False
        return True
    except Exception:
        return False


def modal_text(driver):
    try:
        for sel in ("div[role='dialog']", "[class*='modal']", "body"):
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                return (els[0].text or "")
    except Exception:
        pass
    return ""


# 平台对 "每天限续一次" 的典型拒绝文案 (西/英)
DAILY_LIMIT_PHRASES = (
    "ya has renovado", "ya renovaste", "solo puedes renovar",
    "una vez al día", "una vez por día", "cada 24", "cada día",
    "vuelve a intentarlo mañana", "vuelve mañana", "límite diario",
    "limite diario", "daily limit", "already renewed", "once per day",
    "once a day", "try again tomorrow", "24 hours",
)
# 其他真实失败文案
FAIL_PHRASES = (
    "error al renovar", "no se pudo renovar", "renovación fallida",
    "inténtalo de nuevo más tarde", "too many requests",
)


def solve_renew_modal(driver, name, timeout=110):
    """
    1) 过弹窗内可见 Turnstile, 等 'Verificación completada' / 确认按钮启用
    2) 点 CONFIRMAR RENOVACIÓN
    3) 识别结果
    返回 (status, detail); status ∈ {"ok", "limit", "fail"}
      ok    = 续期成功 (到期日 +1)
      limit = 平台提示今日已续过/每日限一次 (正常情况, 不算失败)
      fail  = 验证没过 / 提交失败 / 状态未知
    """
    # 等弹窗出现
    end = time.time() + 12
    while time.time() < end:
        if "renovar servidor" in modal_text(driver).lower():
            break
        time.sleep(1)

    verified = False
    deadline = time.time() + timeout
    clicks = 0
    while time.time() < deadline:
        body_l = (driver.get_text("body") or "").lower()
        btn = confirm_button(driver)
        if "verificaci" in body_l and "completada" in body_l and btn and confirm_enabled(btn):
            verified = True
            break
        # 隐形挑战可能直接给过: 按钮自己启用也算
        if btn and confirm_enabled(btn) and visible_turnstiles(driver) == []:
            verified = True
            break
        if btn and confirm_enabled(btn) and clicks >= 1:
            # 点过之后按钮启用, 信任状态
            verified = True
            break
        frames = visible_turnstiles(driver)
        if frames and clicks < 4:
            click_turnstile(driver, where=f"续期弹窗/{name}")
            clicks += 1
        time.sleep(3)

    if not verified:
        return "fail", "Turnstile 验证未完成（确认按钮未启用）"

    log(f"  🟢 [{name}] 人机验证完成, 点击 CONFIRMAR RENOVACIÓN")
    btn = confirm_button(driver)
    if not btn or not real_click(driver, btn):
        return "fail", "确认按钮点击失败"

    end = time.time() + 20
    while time.time() < end:
        low = ((modal_text(driver) or "") + "\n"
               + (driver.get_text("body") or "")).lower()
        if "servidor renovado" in low:
            return "ok", "续期成功（到期日 +1 天）"
        if any(k in low for k in DAILY_LIMIT_PHRASES):
            return "limit", "平台提示今日已续过（每天限 1 次）"
        if any(k in low for k in FAIL_PHRASES):
            return "fail", "平台返回续期失败提示"
        time.sleep(2)
    return "fail", "提交后未出现 renovado 成功提示"


def close_modal(driver):
    """只在弹窗容器内找 CERRAR / X, 绝不碰背景卡片上的删除图标"""
    dialog_xpath = "//div[@role='dialog'] | //*[contains(@class,'modal')]"
    dialogs = []
    try:
        dialogs = [d for d in driver.find_elements(By.XPATH, dialog_xpath)
                   if d.is_displayed()]
    except Exception:
        pass
    for dlg in dialogs:
        try:
            for b in dlg.find_elements(By.XPATH, ".//button | .//*[@role='button']"):
                t = (b.text or "").strip().lower()
                if not b.is_displayed():
                    continue
                if t in ("cerrar", "x", "×"):
                    real_click(driver, b)
                    time.sleep(1)
                    return
            # 右上角小尺寸无文字图标 (X)
            for b in dlg.find_elements(By.XPATH, ".//button | .//*[@role='button']"):
                if not b.is_displayed() or (b.text or "").strip():
                    continue
                box = b.size or {}
                if box.get("width", 0) <= 48 and box.get("height", 0) <= 48:
                    real_click(driver, b)
                    time.sleep(1)
                    return
        except Exception:
            pass


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main():
    log("=== eknodes 自动续期任务启动 ===")
    if not EK_EMAIL or not EK_PASSWORD:
        log("❌ 未配置 EK_EMAIL / EK_PASSWORD")
        tg_send("🔴 <b>eknodes 续期失败</b>\n\n未配置 EK_EMAIL / EK_PASSWORD")
        return

    driver = Driver(uc=True, headless=False,
                    proxy=PROXY_SERVER or None, ad_block=False)
    summary = {"renewed": [], "limited": [], "failed": [], "skipped": []}

    try:
        driver.set_page_load_timeout(45)

        if not do_login(driver):
            shot(driver, "ek_login_failed.png")
            hint = ("\n\n检测到 Vercel 浏览器校验被拒，GitHub 机房 IP 信誉低，"
                    "建议在仓库 Secret 配置 <code>PROXY_SERVER</code>"
                    "（http/socks5 无账密代理）后重试。") if not PROXY_SERVER \
                else "\n\n已走代理仍被拒，请更换代理节点。"
            tg_send("🔴 <b>eknodes 登录失败</b>\n\n请检查凭据 / Vercel 检查 / "
                    "Turnstile。" + hint, "ek_login_failed.png")
            return

        # ---------- 服务器列表 ----------
        log(f"🔄 打开服务器列表: {SERVERS_URL}")
        driver.get(SERVERS_URL)
        time.sleep(5)
        wait_vercel(driver, timeout=20)
        time.sleep(2)

        cards = read_cards(driver, only_enabled=True)
        if not cards:
            # 兜底: RENOVAR 可能全部置灰(如启动中), 连同禁用按钮再扫一次
            cards = read_cards(driver, only_enabled=False)
        if not cards:
            shot(driver, "ek_unknown.png")
            tg_send("⚪ <b>eknodes 巡检异常</b>\n\n未识别到带 RENOVAR 的服务器卡片，"
                    "可能已改版或账号下无服务器。", "ek_unknown.png")
            return

        for c in cards:
            log(f"   🖥️ {c['name']} | 状态={c['status']} | {c['ip']} | "
                f"到期={c['expire_str']} | 剩余={c['left']}天")

        shot(driver, "ek_servers.png")

        unknown = [c for c in cards if c["left"] is None]
        # 续期规则: 每天 +1 天, 上限 7 天 → 只要剩余 < 上限就补一天
        due = [c for c in cards if c["left"] is not None and c["left"] < MAX_DAYS]
        for c in cards:
            if c not in due and c not in unknown:
                summary["skipped"].append(c)
        for c in unknown:
            summary["failed"].append((c, "到期日无法识别，未敢自动续期"))

        if not due and unknown:
            # 全部是无法识别的卡片: 直接带着失败汇总发通知收尾
            lines = [f"❌ <code>{html.escape(c['name'])}</code>：{html.escape(d)}"
                     for c, d in summary["failed"]]
            now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            tg_send("🔴 <b>eknodes 续期失败</b>\n\n" + "\n".join(lines)
                    + f"\n\n⏰ <code>{now_str}</code>", "ek_servers.png")
            return

        if not due:
            log(f"🟢 全部服务器剩余已达 {MAX_DAYS} 天上限，无需续期")
            detail = "\n".join(
                f"🖥️ <code>{html.escape(c['name'])}</code>｜{c['status']}｜"
                f"到期 <code>{c['expire_str']}</code>（剩 {c['left']} 天）"
                for c in cards)
            now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            tg_send(f"ℹ️ <b>eknodes 巡检</b>\n\n{detail}\n\n"
                    f"📌 已达 {MAX_DAYS} 天上限，今日无需续期\n"
                    f"⏰ <code>{now_str}</code>",
                    "ek_servers.png")
            return

        def _open_modal(card):
            """定位并点击某张卡片的 RENOVAR, 返回是否成功打开弹窗"""
            try:
                btn = driver.find_element(
                    By.CSS_SELECTOR, f"[data-ek-renew='{card['index']}']")
            except Exception:
                fresh = {x["name"]: x for x in read_cards(driver)}
                c2 = fresh.get(card["name"])
                if not c2:
                    return False
                card["index"] = c2["index"]
                try:
                    btn = driver.find_element(
                        By.CSS_SELECTOR, f"[data-ek-renew='{c2['index']}']")
                except Exception:
                    return False
            if not real_click(driver, btn):
                return False
            time.sleep(2)
            return "renovar servidor" in modal_text(driver).lower()

        # ---------- 逐台续期 (失败在同一轮内重开弹窗再试 1 次) ----------
        for c in due:
            name = c["name"]
            log(f"🟠 [{name}] 剩余 {c['left']} 天 < {MAX_DAYS}，补续 1 天...")
            if c.get("disabled"):
                log(f"  ⚠️ [{name}] RENOVAR 按钮置灰，本轮跳过")
                summary["failed"].append(
                    (c, "RENOVAR 按钮置灰（服务器可能启动中），下轮巡检重试"))
                continue

            shot(driver, f"ek_before_{c['index']}.png")
            status, detail = "fail", "弹窗未打开"
            for attempt in (1, 2):
                if not _open_modal(c):
                    detail = "RENOVAR 按钮点击后弹窗未出现"
                    time.sleep(2)
                    continue
                status, detail = solve_renew_modal(driver, name)
                shot(driver, f"ek_renew_{c['index']}_{attempt}.png")
                close_modal(driver)
                log(f"  {'✅' if status == 'ok' else 'ℹ️' if status == 'limit' else '❌'}"
                    f" [{name}] 第 {attempt} 次: {status} / {detail}")
                if status in ("ok", "limit"):
                    break
                time.sleep(3)

            if status == "ok":
                summary["renewed"].append(c)
            elif status == "limit":
                summary["limited"].append(c)
            else:
                summary["failed"].append((c, detail))
            time.sleep(2)

        # ---------- 刷新核对新到期日 ----------
        log("🔄 刷新列表核对到期日...")
        driver.get(SERVERS_URL)
        time.sleep(5)
        after_cards = {x["name"]: x for x in read_cards(driver)}
        shot(driver, "ek_final.png")

        lines = []
        renewed_unconfirmed = []
        for c in summary["renewed"]:
            a = after_cards.get(c["name"])
            new_str = a["expire_str"] if a else "未知"
            new_left = a["left"] if a and a["left"] is not None else "?"
            # 规则是到期日恰好 +1 天
            confirmed = bool(a and a["expire"] and c["expire"]
                             and (a["expire"] - c["expire"]).days == 1)
            if confirmed:
                lines.append(
                    f"✅ <code>{html.escape(c['name'])}</code> 续期成功（+1 天）\n"
                    f"   到期 <code>{c['expire_str']}</code> ➜ <code>{new_str}</code>"
                    f"（剩 {new_left} 天）")
            else:
                renewed_unconfirmed.append(
                    (c, f"弹窗提示成功但到期日未变成 +1（现为 {new_str}），请人工核对"))
        for c in summary["limited"]:
            a = after_cards.get(c["name"])
            left_str = a["left"] if a and a["left"] is not None else c["left"]
            lines.append(
                f"ℹ️ <code>{html.escape(c['name'])}</code> 今日已续过（平台每天限 1 次）"
                f"，当前剩 {left_str} 天，明早自动再补")
        for c, detail in summary["failed"] + renewed_unconfirmed:
            lines.append(f"❌ <code>{html.escape(c['name'])}</code>：{html.escape(detail)}")
        for c in summary["skipped"]:
            lines.append(
                f"⏭️ <code>{html.escape(c['name'])}</code> 已在 {MAX_DAYS} 天上限"
                f"（剩 {c['left']} 天），未操作")

        all_fail = summary["failed"] + renewed_unconfirmed
        now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        if all_fail:
            head = "🔴 <b>eknodes 续期部分失败</b>" if summary["renewed"] \
                else "🔴 <b>eknodes 续期失败</b>"
        elif summary["renewed"]:
            head = "🟢 <b>eknodes 续期成功</b>（+1 天，每日补满至 7 天上限）"
        else:
            head = "ℹ️ <b>eknodes 巡检</b>（今日已续或已在上限）"
        tg_send(f"{head}\n\n" + "\n".join(lines) +
                f"\n\n⏰ <code>{now_str}</code>", "ek_final.png")

    except Exception as e:
        import traceback
        log(f"❌ 运行异常: {e}")
        traceback.print_exc()
        shot(driver, "ek_error.png")
        tg_send(f"🔴 <b>eknodes 续期脚本异常</b>\n\n<code>{html.escape(str(e)[:500])}</code>",
                "ek_error.png")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
