#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# seedloaf (seedloaf.com) Minecraft 世界自动巡检开机脚本
#
# 关键事实 (2026-10-07 实测):
# 1. 面板 https://seedloaf.com/dashboard, 未登录 302 到
#    accounts.seedloaf.com/sign-in?redirect_url=... (Clerk 登录)。
#    accounts 域有 Cloudflare managed challenge (标题 "请稍候…",
#    正文 "正在进行安全验证")。自动化环境里 Turnstile iframe
#    可能不渲染 = 硬拦; Actions 机房 IP 有概率过不去, 备选
#    PROXY_SERVER / NODE_LINK (住宅节点)。
# 2. Clerk 登录两步: 邮箱 input#identifier-field → Continue →
#    密码 input#password-field → Continue, 然后跳回 dashboard。
# 3. 世界卡片: h3=世界名, 子域名文本, [Manage World] 链接,
#    离线时绿色 [Start World] 按钮, 在线时红色 [Stop World] 按钮
#    + "Online" 文本 + 绿点。点 Start 后按钮转圈, ~30s 变 Stop。
#
# 策略: 在线 → 静默退出; 离线 → 点 Start World → 复查确认 → TG 通知;
#       任何异常/登录失败 → 截图 + TG 告警。
#
# 运行: GitHub Actions + Xvfb, headless=False, uc=True。
# Secrets: SEED_EMAIL SEED_PASSWORD TG_BOT_TOKEN TG_CHAT_ID
# 可选:    PROXY_SERVER(http://host:port 或 socks5://host:port)
#          NODE_LINK(vmess:// 或 vless://, workflow 起 sing-box)
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
DASHBOARD_URL = "https://seedloaf.com/dashboard"

SEED_EMAIL = os.environ.get("SEED_EMAIL", "").strip()
SEED_PASSWORD = os.environ.get("SEED_PASSWORD", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip()

# requests 走代理时让 socks5 的 DNS 也走代理
REQ_PROXIES = None
if PROXY_SERVER:
    _p = PROXY_SERVER.replace("socks5://", "socks5h://").replace("socks://", "socks5h://")
    REQ_PROXIES = {"http": _p, "https": _p}

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
    """真人键盘输入 (React/Clerk 受控组件最稳), 原生 setter 兜底"""
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
# Cloudflare challenge (accounts.seedloaf.com 的 managed challenge)
# ------------------------------------------------------------
CF_HINT_RE = re.compile(
    r"正在进行安全验证|verify you are human|verifying you are human|"
    r"needs to review the security|checking your browser|"
    r"verify you are a human|安全验证",
    re.IGNORECASE,
)
CF_TITLE_RE = re.compile(r"请稍候|just a moment", re.IGNORECASE)


def cf_challenge(driver):
    """只有真正的 CF 挑战页才算; 登录页/面板页一律 false"""
    try:
        # 有业务表单/世界卡片就不是挑战页
        if driver.find_elements(By.CSS_SELECTOR,
                                "input#identifier-field, input[name='identifier'],"
                                " input[type='password'], [data-seed-world]"):
            return False
        title = (driver.get_title() or "")
        body = (driver.get_text("body") or "")[:800]
        if CF_TITLE_RE.search(title) and CF_HINT_RE.search(body):
            return True
        if CF_HINT_RE.search(body) and "cloudflare" in body.lower():
            return True
        return False
    except Exception:
        return False


def visible_turnstiles(driver):
    """页面上真正可见的 Turnstile iframe (隐形的小尺寸会被过滤)"""
    out = []
    try:
        for f in driver.find_elements(
                By.CSS_SELECTOR, "iframe[src*='challenges.cloudflare.com']"):
            try:
                if f.is_displayed() and f.size.get("width", 0) > 100 \
                        and f.size.get("height", 0) > 40:
                    out.append(f)
            except Exception:
                pass
    except Exception:
        pass
    return out


def click_turnstile(driver, where=""):
    """SeleniumBase 图像级点击 (Xvfb 下可穿透跨域 iframe), 坐标兜底"""
    try:
        driver.uc_gui_click_captcha()
        log(f"  👉 [{where}] uc_gui_click_captcha 已点击")
        time.sleep(5)
        return True
    except Exception as e:
        log(f"  ℹ️ [{where}] uc_gui_click_captcha 不可用: {str(e)[:120]}")
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


def wait_cf(driver, timeout=60):
    """等 CF 挑战自动放行; 有可见复选框就点; 返回是否已通过"""
    end = time.time() + timeout
    clicked = 0
    while time.time() < end:
        if not cf_challenge(driver):
            return True
        if visible_turnstiles(driver) and clicked < 3:
            click_turnstile(driver, where="CF挑战")
            clicked += 1
        time.sleep(3)
    return not cf_challenge(driver)


# ------------------------------------------------------------
# 反指纹补丁 (Xvfb 无 GPU, WebGL 是 SwiftShader 机房铁证)
# ------------------------------------------------------------
STEALTH_JS = r"""
(() => {
  const VENDOR = "Google Inc. (Intel)";
  const RENDERER =
    "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)";
  const patchGL = (proto) => {
    if (!proto) return;
    const orig = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 37445) return VENDOR;
      if (p === 37446) return RENDERER;
      return orig.call(this, p);
    };
  };
  patchGL(window.WebGLRenderingContext &&
          window.WebGLRenderingContext.prototype);
  patchGL(window.WebGL2RenderingContext &&
          window.WebGL2RenderingContext.prototype);
  try {
    Object.defineProperty(navigator, "hardwareConcurrency", { get: () => 8 });
  } catch (e) {}
  try {
    Object.defineProperty(navigator, "deviceMemory", { get: () => 8 });
  } catch (e) {}
  if (!window.chrome) {
    window.chrome = { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
  }
})();
"""


def stealth_inject(driver):
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
    except Exception:
        pass


def open_with_disconnect(driver, url, reconnect_time=10):
    """uc_open_with_reconnect: 质询期间断开 CDP 不留自动化痕迹, 失败回退普通 get"""
    try:
        log(f"  🔌 断开模式打开 (reconnect={reconnect_time}s)...")
        driver.uc_open_with_reconnect(url, reconnect_time=reconnect_time)
    except Exception as e:
        log(f"  ℹ️ uc_open_with_reconnect 不可用({str(e)[:80]}), 回退普通打开")
        driver.get(url)
    stealth_inject(driver)


# ------------------------------------------------------------
# Clerk 登录 (accounts.seedloaf.com)
# ------------------------------------------------------------
IDENTIFIER_SEL = ("input#identifier-field, input[name='identifier'],"
                  " input[type='email']")
PASSWORD_SEL = ("input#password-field, input[name='password'],"
                " input[type='password']")


def click_continue(driver):
    """Clerk 的 Continue 按钮"""
    for b in driver.find_elements(By.XPATH, "//button"):
        try:
            t = (b.text or "").strip().lower()
            if t in ("continue", "继续", "sign in", "log in") and b.is_displayed():
                return real_click(driver, b)
        except Exception:
            pass
    try:
        return real_click(driver, driver.find_element(
            By.CSS_SELECTOR, "button[type='submit']"))
    except Exception:
        return False


def do_login(driver):
    """打开 dashboard, 被重定向到 Clerk 登录页后完成邮箱+密码两步"""
    log(f"🌐 打开面板: {DASHBOARD_URL}")

    # 等页面稳定: 要么直接进 dashboard (Cookie 还有效), 要么到登录页
    settled = False
    for retry in (1, 2, 3):
        open_with_disconnect(driver, DASHBOARD_URL, reconnect_time=8 + retry * 2)
        time.sleep(6 if retry == 1 else 4)
        deadline = time.time() + 50
        while time.time() < deadline:
            cur = (driver.current_url or "").lower()
            if "seedloaf.com/dashboard" in cur and "accounts." not in cur:
                if read_worlds(driver):
                    log("  ✅ Cookie 会话仍有效，直接进入面板")
                    return True
            if cf_challenge(driver):
                log("  🛡️ 命中 Cloudflare 验证，等待/点击...")
                wait_cf(driver, timeout=45)
                continue
            if driver.find_elements(By.CSS_SELECTOR, IDENTIFIER_SEL):
                settled = True
                break
            time.sleep(2.5)
        if settled:
            break
        # 诊断
        try:
            log(f"  🔍 第 {retry} 次未等到登录表单: title={driver.get_title()!r} "
                f"url={driver.current_url!r}")
            body = (driver.get_text("body") or "").strip().replace("\n", " | ")
            log(f"  🔍 页面文本: {body[:300] or '(空白)'}")
        except Exception:
            pass

    if not settled:
        shot(driver, "seed_login_stuck.png")
        log("  ❌ 多次尝试仍无法看到登录表单，疑似被 CF 硬拦")
        return False

    # 第一步: 邮箱
    email_el = driver.find_element(By.CSS_SELECTOR, IDENTIFIER_SEL)
    fill_input(driver, email_el, SEED_EMAIL)
    log(f"  📝 已填邮箱: {SEED_EMAIL[:3]}***")
    time.sleep(0.6)
    click_continue(driver)

    # 等密码框 (Clerk 第二步)
    pwd_el = None
    end = time.time() + 25
    while time.time() < end:
        if cf_challenge(driver):
            wait_cf(driver, timeout=40)
            continue
        els = driver.find_elements(By.CSS_SELECTOR, PASSWORD_SEL)
        if els and els[0].is_displayed():
            pwd_el = els[0]
            break
        # 账号不存在的报错
        try:
            low = (driver.get_text("body") or "").lower()
            if "couldn't find your account" in low or "no account" in low:
                log("  ❌ Clerk 提示账号不存在")
                return False
        except Exception:
            pass
        time.sleep(1.5)
    if not pwd_el:
        log("  ❌ 提交邮箱后未出现密码框")
        shot(driver, "seed_login_nopwd.png")
        return False

    # 第二步: 密码
    fill_input(driver, pwd_el, SEED_PASSWORD)
    log("  📝 已填密码")
    time.sleep(0.6)

    for attempt in range(3):
        log(f"🔑 提交密码（第 {attempt + 1} 次）...")
        click_continue(driver)
        for _ in range(15):
            time.sleep(2)
            cur = (driver.current_url or "").lower()
            if "accounts.seedloaf.com" not in cur and "seedloaf.com" in cur:
                log(f"  ✅ 登录成功 → {driver.current_url}")
                return True
            if cf_challenge(driver):
                wait_cf(driver, timeout=40)
                break
        # 密码错误提示
        try:
            low = (driver.get_text("body") or "").lower()
            if any(k in low for k in ("password is incorrect", "incorrect password",
                                      "too many attempts", "too many failed")):
                log("  ❌ Clerk 提示密码错误或尝试过多")
                return False
        except Exception:
            pass
    return False


# ------------------------------------------------------------
# 世界卡片识别
# ------------------------------------------------------------
READ_WORLDS_JS = r"""
document.querySelectorAll('[data-seed-tag]').forEach(e =>
    e.removeAttribute('data-seed-tag'));
const btns = [...document.querySelectorAll('button')].filter(el => {
    const t = (el.innerText || '').trim().toLowerCase();
    return (t === 'start world' || t === 'stop world')
        && el.offsetParent !== null;
});
return btns.map((btn, idx) => {
    let card = btn;
    for (let i = 0; i < 9; i++) {
        const p = card.parentElement;
        if (!p) break;
        card = p;
        if (card.querySelector('h1,h2,h3,h4')) break;
    }
    btn.setAttribute('data-seed-tag', String(idx));
    btn.setAttribute('data-seed-world', '1');
    const nameEl = card.querySelector('h1,h2,h3,h4');
    const text = (card.innerText || '');
    const subM = text.match(/[a-z0-9.-]+\.seedloaf\.gg/i);
    return {
        index: idx,
        name: nameEl ? (nameEl.innerText || '').trim() : '',
        action: (btn.innerText || '').trim().toLowerCase(),
        subdomain: subM ? subM[0] : '',
        online: /^\s*online/im.test(text) || /\bonline\b/i.test(text),
        text: text.slice(0, 300)
    };
});
"""


def read_worlds(driver):
    """返回 [{index,name,action,subdomain,online}], action: 'start world'|'stop world'"""
    try:
        return driver.execute_script(READ_WORLDS_JS) or []
    except Exception:
        return []


def read_worlds_stable(driver, rounds=8, interval=3):
    """SPA 卡片先渲染默认状态再异步回填真实状态, 必须连续两轮
    读到相同的 (名称,按钮) 组合才采信, 防止把在线误判成离线"""
    prev = None
    last = []
    for i in range(rounds):
        last = read_worlds(driver)
        sig = sorted((w["name"], w["action"]) for w in last)
        if last and sig == prev:
            log(f"  🔁 状态已稳定（第 {i + 1} 轮读数一致）")
            return last
        prev = sig
        time.sleep(interval)
    return last


def start_world(driver, world):
    """点击该世界的 Start World, 返回是否点到"""
    try:
        btn = driver.find_element(
            By.CSS_SELECTOR, f"[data-seed-tag='{world['index']}']")
        return real_click(driver, btn)
    except Exception:
        return False


def wait_world_online(driver, name, timeout=240):
    """点击后复查: 卡片按钮变 Stop World / 出现 Online 文本才算成功。
    MC 冷启动约 2 分钟, 超时后再刷新确认一次, 防止状态翻转边缘误判"""
    end = time.time() + timeout
    refreshed = False
    while time.time() < end:
        worlds = read_worlds(driver)
        w = next((x for x in worlds if x["name"] == name), None)
        if w and (w["action"] == "stop world" or w["online"]):
            return True, w
        # 中途刷新一次, 防止 SPA 状态不更新
        if not refreshed and time.time() > end - timeout / 2:
            log("  🔄 刷新面板复查状态...")
            driver.get(DASHBOARD_URL)
            time.sleep(6)
            refreshed = True
            continue
        time.sleep(6)
    # 超时: 最后再刷新确认一次
    log("  🔄 超时，最后刷新确认一次...")
    driver.get(DASHBOARD_URL)
    time.sleep(10)
    worlds = read_worlds(driver)
    w = next((x for x in worlds if x["name"] == name), None)
    return bool(w and (w["action"] == "stop world" or w["online"])), w


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main():
    log("=== seedloaf 自动巡检任务启动 ===")
    if not SEED_EMAIL or not SEED_PASSWORD:
        log("❌ 未配置 SEED_EMAIL / SEED_PASSWORD")
        tg_send("🔴 <b>seedloaf 巡检失败</b>\n\n未配置 SEED_EMAIL / SEED_PASSWORD")
        return

    # CF 挑战页会跑 WebRTC 收集, 机房 IP 泄漏直接拉低评分
    webrtc_args = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp,"
                   "--enforce-webrtc-ip-permission-check")
    driver = Driver(uc=True, headless=False,
                    proxy=PROXY_SERVER or None, ad_block=True,
                    chromium_arg=webrtc_args)
    stealth_inject(driver)

    try:
        driver.set_page_load_timeout(45)

        if not do_login(driver):
            shot(driver, "seed_login_failed.png")
            hint = ("\n\nGitHub 机房 IP 可能被 Cloudflare 拒，"
                    "建议在仓库 Secret 配置 <code>NODE_LINK</code>"
                    "（vmess:// / vless:// 住宅节点）或 <code>PROXY_SERVER</code>"
                    " 后重试；或改用软路由 Docker 常驻方案。") if not PROXY_SERVER \
                else "\n\n已走代理仍被拒，请更换代理节点。"
            tg_send("🔴 <b>seedloaf 登录失败</b>\n\n请检查凭据 / Cloudflare 验证。"
                    + hint, "seed_login_failed.png")
            return

        # ---------- 读世界卡片 (状态异步回填, 需连续两轮一致才采信) ----------
        if "seedloaf.com/dashboard" not in (driver.current_url or ""):
            driver.get(DASHBOARD_URL)
            time.sleep(5)

        worlds = read_worlds_stable(driver)
        if not worlds:
            shot(driver, "seed_unknown.png")
            tg_send("⚪ <b>seedloaf 巡检异常</b>\n\n未识别到世界卡片，"
                    "可能面板改版或账号下无世界。", "seed_unknown.png")
            return

        for w in worlds:
            state = "🟢在线" if (w["action"] == "stop world" or w["online"]) else "🔴离线"
            log(f"   🌍 {w['name']} | {w['subdomain']} | {state} | 按钮={w['action']}")

        shot(driver, "seed_before.png")

        offline = [w for w in worlds
                   if w["action"] == "start world" and not w["online"]]
        if not offline:
            log("🟢 全部世界在线，静默退出（不发 TG）")
            return

        # ---------- 逐台开机 ----------
        results = []  # (world, ok, detail)
        for w in offline:
            name = w["name"] or "world"
            log(f"🟠 [{name}] 离线，点击 Start World...")
            if not start_world(driver, w):
                results.append((w, False, "Start World 按钮点击失败"))
                continue
            ok, after = wait_world_online(driver, name, timeout=240)
            if ok:
                log(f"  ✅ [{name}] 已上线")
                results.append((w, True, after["subdomain"] if after else w["subdomain"]))
            else:
                results.append((w, False, "点击后 240s 内未确认在线"))
            time.sleep(2)

        shot(driver, "seed_after.png")

        # ---------- 汇总通知 ----------
        lines = []
        for w, ok, detail in results:
            name = html.escape(w["name"] or "world")
            if ok:
                lines.append(f"✅ <code>{name}</code> 已开机上线"
                             + (f"\n   <code>{html.escape(detail)}</code>" if detail else ""))
            else:
                lines.append(f"❌ <code>{name}</code>：{html.escape(detail)}")
        online_cnt = len(worlds) - len(offline)
        if online_cnt:
            lines.append(f"➖ 其余 {online_cnt} 台本就在线")

        ok_cnt = sum(1 for _, ok, _ in results if ok)
        if ok_cnt == len(results):
            head = "🟢 <b>seedloaf 已自动开机</b>"
        elif ok_cnt > 0:
            head = "🟠 <b>seedloaf 部分开机失败</b>"
        else:
            head = "🔴 <b>seedloaf 开机失败</b>"
        now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        tg_send(f"{head}\n\n" + "\n".join(lines) +
                f"\n\n⏰ <code>{now_str}</code>", "seed_after.png")

    except Exception as e:
        import traceback
        log(f"❌ 运行异常: {e}")
        traceback.print_exc()
        shot(driver, "seed_error.png")
        tg_send(f"🔴 <b>seedloaf 巡检脚本异常</b>\n\n<code>{html.escape(str(e)[:500])}</code>",
                "seed_error.png")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
