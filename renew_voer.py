#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Voer.host 免费服务器全自动开机脚本 (WARP 网络优化 + 离线广告唤醒版)
优化点：放宽页面加载策略至 commit，延长网络握手超时至 60 秒，避免被第三方广告资源卡死
"""
import os
import sys
import time
import json
import requests
import urllib.request
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SERVER_ID = os.environ.get("VOER_SERVER_ID", "").strip()
TOKEN = os.environ.get("VOER_TOKEN", "").strip()
if not TOKEN and os.environ.get("VOER_COOKIES"):
    cookie_str = os.environ.get("VOER_COOKIES")
    if "token=" in cookie_str:
        TOKEN = cookie_str.split("token=")[1].split(";")[0].strip()

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

CONFIG = {
    "server_id": SERVER_ID,
    "token": TOKEN,
    "ads_per_extension": 3,
    "ad_duration_sec": 33,
}


def log(*args):
    print(f"[{time.strftime('%H:%M:%S')}]", *args, flush=True)


def tg_send(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    except Exception as e:
        log(f"TG 通知异常: {e}")


def api_state(server_id: str, token: str) -> dict:
    try:
        req = urllib.request.Request(
            f"https://voer.host/api/servers/{server_id}",
            headers={"Cookie": f"token={token}", "User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()).get("server", {})
    except Exception as e:
        log(f"获取 API 状态失败: {e}")
        return {}


def click_frame_target(page, target_texts, timeout_ms=60000):
    """递归穿透所有 iframe（包含 OOPIF）寻找指定文本并真实点击"""
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for frame in page.frames:
            for text in target_texts:
                for locator_fn in [
                    lambda: frame.get_by_text(text, exact=False).first,
                    lambda: frame.get_by_role("button", name=text).first
                ]:
                    try:
                        loc = locator_fn()
                        if loc.count() > 0 and loc.is_visible():
                            loc.click(timeout=3000)
                            return f"[{text}] in {frame.url[:40]}"
                    except Exception:
                        pass
        time.sleep(1.5)
    return None


def trigger_start_modal(page):
    """等待备份清理结束，点击 Start 或电源图标唤起广告弹窗"""
    log("正在检测并唤醒服务器...")

    # 等待后台解冻
    for _ in range(18):
        try:
            body = page.inner_text("body")
            if "Saving your server" in body or "Start is locked" in body:
                log("⏳ 节点正在备份/清理中，等待解冻...")
                page.wait_for_timeout(10000)
            else:
                break
        except Exception:
            page.wait_for_timeout(3000)

    # 优先点击绿色的 Start 按钮
    start_btn = page.locator("//button[contains(., 'Start') or contains(., '开始')]").first
    if start_btn.is_visible():
        log("发现 Start 按钮，点击唤起看广告弹窗...")
        start_btn.click()
        page.wait_for_timeout(4000)
        return True

    # 兜底点击电源中心图标
    power_btn = page.locator("svg path[d*='M12 2v10']").first
    if power_btn.is_visible():
        log("点击电源中心图标唤醒...")
        power_btn.click()
        page.wait_for_timeout(4000)
        return True

    return False


def save_next_cron(expires_at: str):
    """解析到期时间写入日志文件"""
    now = datetime.now(timezone.utc)
    delay_sec = 14400
    if expires_at:
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            remaining = int((exp_dt - now).total_seconds())
            if remaining > 0:
                delay_sec = remaining + 120
        except Exception:
            pass

    next_run = (now + timedelta(seconds=delay_sec)).strftime("%Y-%m-%d %H:%M:%S")
    log(f"📌 计划下次启动/续期检查 (UTC): {next_run}")
    with open("output.log", "a", encoding="utf-8") as f:
        f.write(f"NEXT_RUN_UTC={next_run}\n")


def main():
    if not CONFIG["token"] or not CONFIG["server_id"]:
        log("❌ 缺少 VOER_TOKEN 或 VOER_SERVER_ID，请在 GitHub Secrets 中配置！")
        sys.exit(1)

    panel_url = f"https://voer.host/panel/server/{CONFIG['server_id']}"
    completed_ads = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_cookies([{
            "name": "token",
            "value": CONFIG["token"],
            "domain": "voer.host",
            "path": "/",
            "secure": True
        }])

        page = context.new_page()
        page.set_default_timeout(60000)

        log(f"访问面板: {panel_url}")
        try:
            # wait_until='commit' 只要建立连接即放行，避免被 WARP 网络下的广告脚本卡死
            page.goto(panel_url, wait_until="commit", timeout=60000)
        except Exception as e:
            log(f"页面初次导航微超时，尝试继续执行: {e}")

        # 给前端框架 10 秒时间完成 Vue/React 组件挂载
        page.wait_for_timeout(10000)

        init_state = api_state(CONFIG["server_id"], CONFIG["token"])
        log(f"当前状态: {init_state.get('status')} | 到期时间: {init_state.get('sessionExpiresAt')}")

        # 触发激活广告弹窗
        trigger_start_modal(page)
        page.wait_for_timeout(5000)

        # 循环完成 3 轮广告
        total_target = CONFIG["ads_per_extension"]
        for round_idx in range(1, total_target + 1):
            log(f"等待第 {round_idx}/{total_target} 个广告入口...")
            hit = click_frame_target(
                page,
                ["Watch ad", "Watch Ads", "Rewarded ad", "Ready for Voer", "觀看廣告", "观看广告"],
                timeout_ms=75000
            )

            if not hit:
                log(f"⚠️ 第 {round_idx} 个广告未找到触发区域，退出广告轮询")
                break

            completed_ads += 1
            log(f"✅ 成功点击广告 ({hit})，开始播放等待...")
            page.wait_for_timeout(CONFIG["ad_duration_sec"] * 1000)

            # 点击关闭广告
            close_hit = click_frame_target(page, ["Close", "關閉", "关闭"], timeout_ms=45000)
            log(f"广告关闭状态: {close_hit if close_hit else '无关闭按钮/自动结算'}")
            page.wait_for_timeout(5000)

        # 轮询检验开机结果（等待 2 分钟）
        log("广告播放完毕，等待节点分配与开机...")
        final_state = init_state
        is_success = False
        wait_deadline = time.time() + 120

        while time.time() < wait_deadline:
            current_state = api_state(CONFIG["server_id"], CONFIG["token"])
            if current_state:
                final_state = current_state
                if current_state.get("status") in ("running", "starting") or current_state.get("sessionExpiresAt") != init_state.get("sessionExpiresAt"):
                    is_success = True
                    log(f"🎉 启动成功！服务器状态: {current_state.get('status')}，到期时间: {current_state.get('sessionExpiresAt')}")
                    break
            time.sleep(8)

        now_str = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
        summary_title = "✅ 唤醒开机成功" if is_success else "⚠️ 流程执行完毕(请核对状态)"
        tg_send(
            f"📋 <b>VOER Host 自动开机汇报</b>\n\n"
            f"🎯 <b>结果：</b>{summary_title}\n"
            f"🎬 <b>广告进度：</b><code>{completed_ads}/{total_target}</code>\n"
            f"⚡ <b>服务器状态：</b><code>{final_state.get('status')}</code>\n"
            f"⏳ <b>到期时间：</b><code>{final_state.get('sessionExpiresAt')}</code>\n"
            f"⏰ <b>执行时间：</b><code>{now_str}</code>"
        )

        save_next_cron(final_state.get("sessionExpiresAt"))
        browser.close()


if __name__ == "__main__":
    main()
