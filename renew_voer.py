#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# VOER Host 自动续期与离线开机脚本 (外部唤醒纯净版 + 3轮广告)
# ============================================================
import html
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
import requests
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from seleniumbase import Driver

BASE_URL = "https://voer.host"
SERVER_ID = os.environ.get(
    "VOER_SERVER_ID", "84a3ea1a-c2b4-4798-ba20-a6b83a4d7992"
).strip()
SERVER_CONSOLE_URL = f"{BASE_URL}/panel/server/{SERVER_ID}"

LOCAL_HTTP_PORT = 18080
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
VOER_COOKIES = os.environ.get("VOER_COOKIES", "").strip()
SOCKS5_PROXY = os.environ.get("SOCKS5_PROXY", "").strip()


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
            data={
                "chat_id": TG_CHAT_ID,
                "caption": text,
                "parse_mode": "HTML",
            },
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


def normalize_socks5_proxy(proxy_value: str) -> str:
  proxy_value = (proxy_value or "").strip()
  for prefix in ("socks5://", "socks://"):
    if proxy_value.startswith(prefix):
      proxy_value = proxy_value[len(prefix) :]
      break
  if not proxy_value or ":" not in proxy_value:
    raise ValueError(
        "SOCKS5_PROXY 格式错误，应为 host:port 或 user:pass@host:port。"
    )
  return proxy_value


def wait_http_proxy_ready(port: int, timeout: int = 15):
  proxies = {
      "http": f"http://127.0.0.1:{port}",
      "https": f"http://127.0.0.1:{port}",
  }
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
  cmd = [
      "gost",
      "-L",
      f"http://127.0.0.1:{LOCAL_HTTP_PORT}",
      "-F",
      f"socks5://{normalized}",
  ]
  print("  🚀 启动 gost 代理中转...")
  proc = subprocess.Popen(
      cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
  )
  time.sleep(2)
  if proc.poll() is not None:
    raise RuntimeError("gost 启动失败，请检查 SOCKS5_PROXY 格式和 gost 安装。")
  wait_http_proxy_ready(LOCAL_HTTP_PORT)
  print(f"  ✅ gost 已启动，本地代理端口：{LOCAL_HTTP_PORT}")
  return proc


def physical_click_trusted(driver, element):
  try:
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
        element,
    )
    time.sleep(0.1)
  except Exception:
    pass

  try:
    ActionChains(driver).move_to_element(element).pause(0.1).click().perform()
    return
  except Exception:
    pass

  try:
    element.click()
    return
  except Exception:
    pass

  try:
    driver.execute_script(
        """
        const el = arguments[0];
        ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(evt => {
            el.dispatchEvent(new MouseEvent(evt, { bubbles: true, cancelable: true, view: window }));
        });
    """,
        element,
    )
  except Exception:
    pass


def dismiss_unlock_modal(driver):
  print("  🔎 扫描 Unlock 拦截弹窗...", flush=True)
  driver.switch_to.default_content()

  unlock_xpaths = [
      "//button[contains(., 'View a short ad') or contains(., '观看一则短广告')]",
      (
          "//div[contains(text(), 'Unlock more content') or contains(text(),"
          " '解锁更多内容')]/following::button[contains(., 'View a short') or"
          " contains(., '观看一则短广告')]"
      ),
      (
          "//*[contains(text(), 'Site-wide access') or contains(text(),"
          " '网站级访问权限')]/ancestor::button"
      ),
      "//*[contains(text(), 'View a short ad') or contains(text(), '观看一则短广告')]",
  ]

  clicked = False
  for _ in range(2):
    for xpath in unlock_xpaths:
      try:
        elems = driver.find_elements(By.XPATH, xpath)
        for el in elems:
          if el.is_displayed():
            print("  🚨 检测到 Unlock 全局拦截弹窗，准备击穿...", flush=True)
            physical_click_trusted(driver, el)
            print(
                "  💥 已成功点击 [View a short ad / 观看一则短广告] 按钮！",
                flush=True,
            )
            time.sleep(4)
            clicked = True
            break
      except Exception:
        pass
      if clicked:
        break
    if clicked:
      break
    time.sleep(1)


def dismiss_pwa_popups(driver):
  try:
    btns = driver.find_elements(
        By.XPATH,
        "//button[translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
        " 'abcdefghijklmnopqrstuvwxyz')='close' or contains(., 'Close') or"
        " contains(., 'Dismiss') or contains(., 'Accept')]",
    )
    for b in btns:
      if b.is_displayed():
        physical_click_trusted(driver, b)
        time.sleep(0.5)
  except Exception:
    pass


def restore_session_data(driver, credential_str: str, domain=".voer.host"):
  print("  📦 正在解析并恢复浏览器会话数据...", flush=True)
  cookie_str = credential_str
  storage_dict = {}
  try:
    data = json.loads(credential_str)
    if isinstance(data, dict):
      cookie_str = data.get("cookies", "")
      storage_dict = data.get("storage", {})
  except Exception:
    pass

  token_match = re.search(r"token=([^;\s]+)", cookie_str)
  token_val = token_match.group(1).strip() if token_match else cookie_str.strip()

  if cookie_str:
    parts = [c.strip() for c in cookie_str.split(";") if c.strip()]
    for part in parts:
      if "=" in part:
        name, val = part.split("=", 1)
        try:
          driver.add_cookie({
              "name": name.strip(),
              "value": val.strip(),
              "domain": domain,
              "path": "/",
          })
        except Exception:
          try:
            driver.add_cookie(
                {"name": name.strip(), "value": val.strip(), "path": "/"}
            )
          except Exception:
            pass
    print("  🍪 Cookie 注入完成", flush=True)

  try:
    driver.execute_script(f"""
        localStorage.setItem('token', '{token_val}');
        localStorage.setItem('auth_token', '{token_val}');
        sessionStorage.setItem('token', '{token_val}');
    """)
    if storage_dict and isinstance(storage_dict, dict):
      for k, v in storage_dict.items():
        driver.execute_script(
            "window.localStorage.setItem(arguments[0], arguments[1]);",
            k,
            str(v),
        )
    print("  💾 LocalStorage 恢复完成", flush=True)
  except Exception as e:
    print(f"  ⚠️ LocalStorage 注入异常: {e}")


def get_expire_and_progress(driver) -> tuple:
  """精准获取运行状态、倒计时和今日额度进度"""
  dismiss_pwa_popups(driver)
  raw_str = "未知"
  total_seconds = 0
  prog_str = "未知"
  server_status = "未知"

  try:
    body_text = driver.get_text("body").replace("\u00a0", " ").replace("\u202f", " ")

    status_elems = driver.find_elements(
        By.XPATH,
        "//*[contains(@class, 'badge') or contains(@class, 'status') or"
        " self::span][translate(text(), 'running', 'RUNNING')='RUNNING' or"
        " translate(text(), 'stopped', 'STOPPED')='STOPPED' or translate(text(),"
        " 'restoring', 'RESTORING')='RESTORING' or translate(text(), 'crashed',"
        " 'CRASHED')='CRASHED']",
    )
    for se in status_elems:
      txt = se.text.strip().upper()
      if txt in ("RUNNING", "STOPPED", "RESTORING", "CRASHED"):
        if txt == "RUNNING":
          server_status = "🟢 RUNNING"
        elif txt == "STOPPED":
          server_status = "🔴 STOPPED"
        elif txt == "RESTORING":
          server_status = "🟠 RESTORING"
        else:
          server_status = "💥 CRASHED"
        break

    if server_status == "未知":
      if "RUNNING" in body_text:
        server_status = "🟢 RUNNING"
      elif "STOPPED" in body_text:
        server_status = "🔴 STOPPED"
      elif "RESTORING" in body_text:
        server_status = "🟠 RESTORING"
      elif "CRASHED" in body_text:
        server_status = "💥 CRASHED"

    elems = driver.find_elements(
        By.XPATH, "//*[contains(text(), ':') and string-length(text()) <= 12]"
    )
    for elem in elems:
      txt = elem.text.strip()
      m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", txt)
      if m:
        total_seconds = (
            int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        )
        raw_str = f"剩余 {txt}"
        break

    if raw_str == "未知":
      time_match = re.search(
          r"(?i)(?:Time Remaining|remaining|expire)[\s:]*([0-9]+:[0-9]+:[0-9]+)",
          body_text,
      )
      if time_match:
        t = time_match.group(1).strip()
        p = t.split(":")
        total_seconds = int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        raw_str = f"剩余 {t}"
      elif "STOPPED" in server_status:
        raw_str = "离线待唤醒"
      elif "RESTORING" in server_status:
        raw_str = "系统恢复中"
      elif "CRASHED" in server_status:
        raw_str = "运行崩溃待恢复"

    pm = re.search(
        r"Extensions\s*today[^\d]*(\d+\s*/\s*\d+)", body_text, re.IGNORECASE
    )
    if pm:
      prog_str = pm.group(1).replace(" ", "")
    else:
      all_p = re.findall(r"(\d+\s*/\s*[1-9]\b)", body_text)
      if all_p:
        prog_str = all_p[0].replace(" ", "")

  except Exception as e:
    print(f"⚠️ 提取状态异常: {e}")

  full_status_str = f"[{server_status}] {raw_str}"
  return full_status_str, total_seconds, prog_str


def recursive_find_and_click(
    driver, xpaths, current_depth=0, max_depth=4
) -> bool:
  for xpath in xpaths:
    try:
      elems = driver.find_elements(By.XPATH, xpath)
      for el in elems:
        if el.is_displayed():
          print(
              f"  👉 在深度 {current_depth} 击中目标: {xpath}...", flush=True
          )
          physical_click_trusted(driver, el)
          return True
    except Exception:
      pass

  if current_depth >= max_depth:
    return False

  try:
    sub_frames = driver.find_elements(By.TAG_NAME, "iframe")
  except Exception:
    sub_frames = []

  for idx in range(len(sub_frames)):
    try:
      frames = driver.find_elements(By.TAG_NAME, "iframe")
      if idx >= len(frames):
        break
      driver.switch_to.frame(frames[idx])
      found = recursive_find_and_click(
          driver, xpaths, current_depth + 1, max_depth
      )
      driver.switch_to.parent_frame()
      if found:
        return True
    except Exception:
      try:
        driver.switch_to.parent_frame()
      except Exception:
        pass

  return False


def ensure_inside_ads_modal(driver):
  driver.switch_to.default_content()
  dismiss_unlock_modal(driver)

  for _ in range(15):
    body = driver.get_text("body")
    if (
        "Saving your server" in body
        or "Start is locked" in body
        or "RESTORING" in body
    ):
      print(
          "  ⏳ 服务器正在保存/解冻中，无法直接操作，等待 10 秒...", flush=True
      )
      time.sleep(10)
    else:
      break

  watch_ads_xpath = "//button[contains(., 'Watch Ads') or contains(., 'Watch ad')]"
  confirm_btns = driver.find_elements(By.XPATH, watch_ads_xpath)
  for b in confirm_btns:
    if b.is_displayed() and "watch ads" in b.text.strip().lower():
      print("  ℹ️ 处于对话框内，点击 [Watch Ads]...", flush=True)
      physical_click_trusted(driver, b)
      time.sleep(3)
      return

  extend_btns = driver.find_elements(
      By.XPATH, "//button[contains(., 'Extend') and not(@disabled)]"
  )
  if extend_btns and extend_btns[0].is_displayed():
    print("  ℹ️ 服务器运行中，点击 [+ Extend] 触发续期...", flush=True)
    physical_click_trusted(driver, extend_btns[0])
    time.sleep(2)
    c_btns = driver.find_elements(By.XPATH, watch_ads_xpath)
    for b in c_btns:
      if b.is_displayed():
        physical_click_trusted(driver, b)
        time.sleep(3)
        break
    return

  start_btns = driver.find_elements(
      By.XPATH,
      "//button[(contains(., 'Start') or contains(., '开始') or contains(.,"
      " 'Recover') or contains(., '恢复')) and not(@disabled)]",
  )
  if start_btns and start_btns[0].is_displayed():
    btn_text = start_btns[0].text.strip()
    print(
        f"  ℹ️ 服务器处于脱机状态，点击 [{btn_text}] 唤醒看广告弹窗...", flush=True
    )
    physical_click_trusted(driver, start_btns[0])
    time.sleep(4)
    dismiss_unlock_modal(driver)
    return


def click_watch_ad_everywhere(driver) -> bool:
  xpaths = [
      "//button[normalize-space(.)='Watch ad' or text()='Watch ad']",
      "//button[contains(translate(., 'AD', 'ad'), 'watch ad')]",
      "//div[contains(., 'Rewarded ad')]//button[contains(., 'Watch')]",
      "//*[contains(text(), 'Ready for Voer')]",
  ]
  driver.switch_to.default_content()
  return recursive_find_and_click(driver, xpaths, current_depth=0, max_depth=3)


def click_close_by_coordinates(driver) -> bool:
  try:
    driver.switch_to.default_content()
    ad_card = driver.execute_script("""
        const iframes = Array.from(document.querySelectorAll('iframe'));
        for (let f of iframes) {
            const rect = f.getBoundingClientRect();
            if (rect.width > 250 && rect.height > 200 && rect.top > 50) {
                return f;
            }
        }
        return null;
    """)
    if ad_card:
      ActionChains(driver).move_to_element_with_offset(
          ad_card, int(ad_card.size["width"] / 2 - 10), -12
      ).click().perform()
      print("  🎯 执行右上角物理坐标打击！", flush=True)
      return True
  except Exception:
    pass
  return False


def handle_sound_and_close_ad(driver, max_wait_sec=65) -> bool:
  print("  ⏳ 正在监控广告生命周期...", flush=True)
  start_time = time.time()

  continue_xpaths = [
      "//button[normalize-space(.)='Continue' or text()='Continue']",
      "//*[text()='Continue' or contains(text(), 'Continue')]",
      (
          "//div[contains(text(), 'play with"
          " sound')]/following::button[contains(., 'Continue')]"
      ),
      "//*[@id='continue-button']",
  ]

  close_xpaths = [
      "//*[normalize-space(.)='Close' or text()='Close']",
      "//*[translate(text(), 'CLOSE', 'close')='close']",
      "//div[text()='Close' or contains(text(), 'Close')]",
      "//span[text()='Close' or contains(text(), 'Close')]",
      "//button[contains(., 'Close') or @aria-label='Close']",
      "//div[@id='dismiss-button' or @aria-label='Close ad']",
      "//*[@id='close-button']",
  ]

  has_sound_continued = False

  while time.time() - start_time < max_wait_sec:
    elapsed = time.time() - start_time

    if not has_sound_continued and elapsed < 15:
      driver.switch_to.default_content()
      if recursive_find_and_click(
          driver, continue_xpaths, current_depth=0, max_depth=4
      ):
        print("  🎉 点击声音遮罩 [Continue] 成功！", flush=True)
        has_sound_continued = True

    if elapsed < 30:
      time.sleep(2)
      continue

    driver.switch_to.default_content()
    if recursive_find_and_click(
        driver, close_xpaths, current_depth=0, max_depth=4
    ):
      print("  🎯 成功命中并关闭广告 [Close]！", flush=True)
      driver.switch_to.default_content()
      time.sleep(3)
      return True

    if elapsed > 40:
      if click_close_by_coordinates(driver):
        time.sleep(2)
        driver.switch_to.default_content()
        return True

    time.sleep(1.5)

  driver.switch_to.default_content()
  print("  ⚠️ 本轮广告展示结束", flush=True)
  return False


def main():
  print("=== Python 任务初始化启动 ===", flush=True)

  gost_proc = None
  uc_proxy = None

  if SOCKS5_PROXY:
    try:
      gost_proc = start_gost(SOCKS5_PROXY)
      uc_proxy = f"http://127.0.0.1:{LOCAL_HTTP_PORT}"
      print("🔗 代理检测正常，已启用中转。")
    except Exception as e:
      print(f"⚠️ 代理启动失败：{e}，将尝试直连。")

  chromium_args = [
      "--disable-heavy-ad-intervention",
      "--disable-features=HeavyAdIntervention,HeavyAdInterventionWarning",
      "--autoplay-policy=no-user-gesture-required",
      "--window-size=1920,1080",
  ]
  driver = Driver(
      uc=True,
      headless=False,
      proxy=uc_proxy,
      chromium_arg=" ".join(chromium_args),
  )

  try:
    print("🔑 执行会话注入恢复...", flush=True)
    driver.uc_open_with_reconnect(BASE_URL, reconnect_time=5)
    time.sleep(2)
    restore_session_data(driver, VOER_COOKIES)
    time.sleep(1)

    print(f"🔄 打开服务器控制台: {SERVER_CONSOLE_URL} ...", flush=True)
    driver.get(SERVER_CONSOLE_URL)
    time.sleep(8)
    dismiss_pwa_popups(driver)

    if "/login" in driver.current_url.lower():
      print("❌ 会话已失效，请重新更新 VOER_COOKIES", flush=True)
      return

    expire_info_before, init_sec, init_prog = get_expire_and_progress(driver)
    print(
        f"⏳ 初始服务器状态: {expire_info_before} | 今日进度: {init_prog}",
        flush=True,
    )

    # 关键判定：如果剩余时间充裕（> 3.5 小时），直接正常退出，不刷广告
    if init_sec > 12600:
      print(
          f"💡 服务器剩余时间充裕（约 {round(init_sec / 3600, 1)} 小时），跳过本次看广告。",
          flush=True,
      )
      return

    completed = 0
    # 精准 3 轮广告
    for current_ad in range(1, 4):
      print(f"\n🎬 === 正在准备第 {current_ad}/3 个广告 ===", flush=True)

      ensure_inside_ads_modal(driver)

      clicked = False
      for sec in range(35):
        ensure_inside_ads_modal(driver)
        if click_watch_ad_everywhere(driver):
          print(
              f"  🎯 第 {sec + 1} 秒成功击发第 {current_ad} 轮的 [Watch ad]"
              " 按钮！",
              flush=True,
          )
          clicked = True
          break
        time.sleep(1)

      if not clicked:
        stuck_screenshot = f"stuck_round_{current_ad}.png"
        driver.switch_to.default_content()
        driver.save_screenshot(stuck_screenshot)
        print(
            f"  ⚠️ 未能在模态框内等到第 {current_ad} 轮的 [Watch ad] 按钮",
            flush=True,
        )
        break

      time.sleep(2)
      handle_sound_and_close_ad(driver, max_wait_sec=65)
      completed += 1
      print(f"  ✅ 第 {current_ad} 个广告闭环完成！", flush=True)
      time.sleep(3)

    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    driver.switch_to.default_content()
    final_close_btns = driver.find_elements(
        By.XPATH,
        "//button[contains(., 'Close') or contains(., 'Done') or contains(.,"
        " 'Finish')]",
    )
    for b in final_close_btns:
      if b.is_displayed():
        physical_click_trusted(driver, b)
        time.sleep(1)

    print(
        "\n⏳ 3 轮广告流程完毕，等待后台分配并刷新，稍候 30 秒以确保状态同步...",
        flush=True,
    )
    time.sleep(30)
    driver.switch_to.default_content()
    driver.refresh()
    time.sleep(8)

    dismiss_pwa_popups(driver)
    dismiss_unlock_modal(driver)

    expire_info_after, _, final_prog = get_expire_and_progress(driver)
    driver.save_screenshot("final_success.png")

    tg_send(
        f"📋 <b>VOER Host 自动续期汇总</b>\n\n"
        f"🎬 <b>观看广告：</b><code>{completed}/3</code> 轮\n"
        f"⏳ <b>到期变动：</b><code>{html.escape(expire_info_before)}</code> ➜"
        f" <code>{html.escape(expire_info_after)}</code>\n"
        f"📊 <b>今日进度：</b><code>{html.escape(final_prog)}</code>\n"
        f"⏰ <b>执行时间：</b><code>{now}</code>",
        photo_path="final_success.png",
    )
    print(
        f"\n✅ 任务执行完毕，最新状态: {expire_info_after} | 额度: {final_prog}",
        flush=True,
    )

  except Exception as e:
    err_msg = str(e)
    print(f"❌ 执行异常: {err_msg}", flush=True)
    try:
      driver.switch_to.default_content()
      driver.save_screenshot("error.png")
    except Exception:
      pass
    tg_send(
        f"🔴 <b>VOER Host 续期通知</b>\n\n❌"
        f" <b>脚本执行异常</b>：\n<code>{html.escape(err_msg)}</code>",
        photo_path="error.png",
    )
  finally:
    driver.quit()
    if gost_proc:
      gost_proc.terminate()
      print("gost 进程已终止。")


if __name__ == "__main__":
  main()
