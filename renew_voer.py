#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Voer.host 免费服务器全自动开机/续期（Playwright Actions 离线唤醒版）
适配官方新机制：单次会话到期自动关机 -> 点击 Start 看 3 个广告重新开机
"""
import os
import sys
import time
import json
import requests
import urllib.request
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

SERVER_ID = os.environ.get("VOER_SERVER_ID", "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992").strip()
TOKEN = os.environ.get("VOER_TOKEN", "").strip()
if not TOKEN and os.environ.get("VOER_COOKIES"):
    cookie_str = os.environ.get("VOER_COOKIES")
    if "token=" in cookie_str:
        TOKEN = cookie_str.split("token=")[1].split(";")[0].strip()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

DEFAULT_CONFIG = {
    "server_id": SERVER_ID,
    "token": TOKEN,
    "ads_per_extension": 3,
    "ad_duration_sec": 32,
    "headless": False
}


def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def tg_send(text: str, photo_path: str = None):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        log(f"TG 通知异常: {e}")


def api_state(cfg):
    try:
        req = urllib.request.Request(
            f"https://voer.host/api/servers/{cfg['server_id']}",
            headers={"Cookie": f"token={cfg['token']}", "User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())["server"]
    except Exception as e:
        log(f"获取 API 状态异常: {e}")
        return {}


def click_anywhere(page, texts, timeout_ms):
    """在所有 frame（含跨进程 OOPIF iframe）里找文本并真实点击"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            for t in texts:
                for maker in (lambda: frame.get_by_text(t, exact=True).first,
                              lambda: frame.get_by_role("button", name=t).first):
                    try:
                        loc = maker()
                        if loc.count() and loc.is_visible():
                            loc.click(timeout=3000)
                            return f"{t}@{frame.url[:45]}"
                    except Exception:
                        pass
        time.sleep(1.5)
    return None


def save_next_cron_run(session_expires_at: str):
    """根据到期时间计算下一次开机任务的时间并存入 output.log"""
    now_utc = datetime.now(timezone.utc)
    delay = 14400  # 默认 4 小时唤醒一次
    if session_expires_at:
        try:
            exp_dt = datetime.fromisoformat(session_expires_at.replace("Z", "+00:00"))
            diff_sec = int((exp_dt - now_utc).total_seconds())
            # 到期后给 3 分钟的保存清理缓冲时间
            if diff_sec > 0:
                delay = diff_sec + 180
            else:
                delay = 300
        except Exception:
            pass

    next_run = now_utc + timedelta(seconds=delay)
    next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
    log(f"📌 下次执行计划 (UTC): {next_run_str}")
    with open("output.log", "a", encoding="utf-8") as f:
        f.write(f"NEXT_RUN_UTC={next_run_str}\n")


def trigger_start_or_extend(page):
    """触发看广告流程：优先点 Start，备选点 Extend"""
    log("正在寻找激活入口（Start / Extend）...")
    
    # 等待后台清理完成（如果遇到 Saving your server before shutdown，循环等待解冻）
    for _ in range(30):
        body_text = page.inner_text("body")
        if "Saving your server" in body_text or "Start is locked" in body_text:
            log("⏳ 节点正在保存清理，等待解冻...")
            page.wait_for_timeout(10000)
        else:
            break

    # 1. 离线状态：尝试点击 Start 按钮或中间电源大图标
    start_loc = page.locator("//button[contains(., 'Start') or contains(., '开始')]").first
    if start_loc.is_visible():
        log("发现 Start 按钮，点击唤醒...")
        start_loc.click()
        page.wait_for_timeout(3000)
        return True

    # 2. 兜底兼容旧版 Extend 按钮
    extend_loc = page.locator("//button[contains(., 'Extend') or contains(., '延伸')]").first
    if extend_loc.is_visible():
        log("发现 Extend 按钮，点击...")
        extend_loc.click()
        page.wait_for_timeout(2500)
        watch_btn = page.locator("//button[contains(., 'Watch Ads') or contains(., '觀看廣告')]").first
        if watch_btn.is_visible():
            watch_btn.click()
            page.wait_for_timeout(3000)
        return True

    return False


def main():
    cfg = DEFAULT_CONFIG
    if not cfg["token"]:
        log("❌ 未检测到有效的 VOER_TOKEN，请检查 GitHub Secrets！")
        sys.exit(1)

    url = f"https://voer.host/panel/server/{cfg['server_id']}"
    completed_ads = 0

    with sync_playwright() as p:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--window-size=1400,1000",
            "--no-sandbox",
            "--disable-setuid-sandbox"
        ]
        browser = p.chromium.launch(headless=cfg["headless"], args=launch_args)
        ctx = browser.new_context(viewport={"width": 1400, "height": 1000})
        ctx.add_cookies([{"name": "token", "value": cfg["token"], "domain": "voer.host", "path": "/", "secure": True}])

        page = ctx.new_page()
        log(f"正在访问控制面板: {url}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(7000)

        before = api_state(cfg)
        log("操作前状态:", before.get("status"),
            "| 当前到期:", before.get("sessionExpiresAt"),
            "| 今日次数:", before.get("sessionExtensionsToday"))

        # 关掉可能的 Cookie 提示
        try:
            page.get_by_role("button", name="Accept").first.click(timeout=3000)
        except Exception:
            pass

        # 调起开机广告弹窗
        trigger_start_or_extend(page)
        log("等待广告弹窗加载完毕...")
        page.wait_for_timeout(6000)

        # 循环观看 3 个激励广告
        total = int(cfg["ads_per_extension"])
        for i in range(1, total + 1):
            hit = click_anywhere(page, ["Watch ad", "Watch Ads", "觀看廣告", "观看广告", "Rewarded ad"], 75000)
            if not hit:
                log(f"第 {i} 个广告入口未找到，停止")
                break
            completed_ads += 1
            log(f"已成功击中第 {i}/{total} 个广告（{hit}），播放等待中…")
            page.wait_for_timeout(int(cfg["ad_duration_sec"]) * 1000)

            closed = click_anywhere(page, ["Close", "關閉", "关闭"], 60000)
            log(f"第 {i} 个广告状态:", f"已点击关闭（{closed}）" if closed else "未发现关闭按钮（可能已自动跳过）")
            page.wait_for_timeout(6000)

        # 轮询状态验证
        end = time.time() + 180
        final_state = before
        activated = False
        while time.time() < end:
            now = api_state(cfg)
            if now:
                final_state = now
                if now.get("status") in ("running", "starting") or now.get("sessionExpiresAt") != before.get("sessionExpiresAt"):
                    activated = True
                    log("🎉 激活成功！服务器运行中，到期时间:", now.get("sessionExpiresAt"))
                    break
            time.sleep(10)

        now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        status_text = "✅ 唤醒/续期成功" if activated else "⚠️ 流程完毕(等待刷新)"
        tg_send(
            f"📋 <b>VOER Host 自动开机/续期汇报</b>\n\n"
            f"🎯 <b>结果：</b>{status_text}\n"
            f"🎬 <b>广告进度：</b><code>{completed_ads}/3</code>\n"
            f"⚡ <b>运行状态：</b><code>{final_state.get('status')}</code>\n"
            f"⏳ <b>新到期时间：</b><code>{final_state.get('sessionExpiresAt')}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_str}</code>"
        )

        save_next_cron_run(final_state.get("sessionExpiresAt"))
        browser.close()


if __name__ == "__main__":
    main()
