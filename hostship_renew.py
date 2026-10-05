#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
host-ship.com (Jexactyl 面板) 免费服务器自动续期
纯 Client API Key 方案，无需浏览器 / 无需模拟登录

流程:
  1. GET  /api/client/servers/{id}   读取服务器信息 + 剩余续期天数
  2. 剩余天数 <= 阈值 -> POST /api/client/servers/{id}/renew
  3. 再次 GET 验证剩余天数变化
  4. Telegram 每轮必发通知 (成功 / 跳过 / 失败)

环境变量:
  HS_API_KEY     面板 Client API Key (ptlc_ 开头)            [必填]
  HS_SERVER_ID   服务器 identifier, 即 /server/xxx 里那段     [默认 c12fd5d9]
  HS_PANEL_URL   面板地址                                     [默认 https://panel.host-ship.com]
  RENEW_THRESHOLD 剩余天数 <= 该值时才续期                     [默认 4]
  TG_BOT_TOKEN   Telegram Bot Token                          [可选, 不填则不通知]
  TG_CHAT_ID     Telegram Chat ID                            [可选]
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

def env(key, default=''):
    """读环境变量, 空字符串视为未设置 (GitHub Actions 未配置时渲染为空串)"""
    val = os.environ.get(key, '').strip()
    return val if val else default


PANEL = env('HS_PANEL_URL', 'https://panel.host-ship.com').rstrip('/')
API_KEY = env('HS_API_KEY')
SERVER_ID = env('HS_SERVER_ID', 'c12fd5d9')
RENEW_THRESHOLD = int(env('RENEW_THRESHOLD', '4'))
TG_BOT_TOKEN = env('TG_BOT_TOKEN')
TG_CHAT_ID = env('TG_CHAT_ID')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def tg_send(html):
    """发送 Telegram 通知, 失败只打印不中断"""
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        log('TG 未配置, 跳过通知')
        return
    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage'
    payload = json.dumps({
        'chat_id': TG_CHAT_ID,
        'text': html,
        'parse_mode': 'HTML',
        'disable_web_page_preview': True,
    }).encode('utf-8')
    req = urllib.request.Request(url, data=payload,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            log(f'TG 通知已发送 (HTTP {resp.status})')
    except Exception as e:
        log(f'TG 通知发送失败: {e}')


def api(method, path, expect_json=True):
    """调用面板 Client API, 返回 (http_code, data_or_text)"""
    url = f'{PANEL}{path}'
    req = urllib.request.Request(url, method=method)
    req.add_header('Authorization', f'Bearer {API_KEY}')
    req.add_header('Accept', 'application/json')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', UA)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode('utf-8', 'replace')
            code = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'replace')
        code = e.code
    if expect_json and body:
        try:
            return code, json.loads(body)
        except json.JSONDecodeError:
            return code, body[:500]
    return code, body


def fmt_err(code, data):
    """把 API 错误响应压缩成一行"""
    if isinstance(data, dict) and data.get('errors'):
        msgs = [e.get('detail', e.get('code', '?')) for e in data['errors']]
        return f'HTTP {code}: {"; ".join(msgs)}'
    return f'HTTP {code}: {str(data)[:200]}'


def get_server():
    """返回 (code, attrs) — attrs 含 name/identifier/uuid/renewal 等"""
    code, data = api('GET', f'/api/client/servers/{SERVER_ID}')
    if code == 200 and isinstance(data, dict):
        return code, data.get('attributes', {})
    return code, data


def main():
    if not API_KEY:
        log('错误: 未设置 HS_API_KEY')
        tg_send('❌ <b>HostShip 续期失败</b>\n原因: 未配置 HS_API_KEY')
        sys.exit(1)

    log(f'面板: {PANEL}  服务器: {SERVER_ID}  阈值: ≤{RENEW_THRESHOLD} 天')

    # 1) 读取服务器状态
    code, attrs = get_server()
    if code != 200:
        msg = fmt_err(code, attrs)
        log(f'读取服务器信息失败: {msg}')
        tg_send(f'❌ <b>HostShip 续期失败</b>\n'
                f'服务器: <code>{SERVER_ID}</code>\n'
                f'读取信息失败: {msg}')
        sys.exit(1)

    name = attrs.get('name', '?')
    renewal = attrs.get('renewal', None)
    suspended = attrs.get('is_suspended', False)
    log(f'服务器: {name} | 剩余: {renewal} 天 | 暂停: {suspended}')

    if suspended:
        tg_send(f'⚠️ <b>HostShip 服务器已被暂停</b>\n'
                f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)\n'
                f'剩余: {renewal} 天\n请登录面板人工处理')
        sys.exit(1)

    # 2) 判断是否需要续期
    if renewal is not None and renewal > RENEW_THRESHOLD:
        log(f'剩余 {renewal} 天 > 阈值 {RENEW_THRESHOLD}, 本轮无需续期')
        tg_send(f'✅ <b>HostShip 巡检正常</b>\n'
                f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)\n'
                f'剩余: <b>{renewal}</b> 天 (阈值 {RENEW_THRESHOLD}, 无需续期)')
        return

    # 3) 执行续期
    log(f'剩余 {renewal} 天 ≤ {RENEW_THRESHOLD}, 执行续期...')
    rcode, rdata = api('POST', f'/api/client/servers/{SERVER_ID}/renew')
    log(f'续期响应: HTTP {rcode} {str(rdata)[:200]}')

    if rcode not in (200, 204):
        msg = fmt_err(rcode, rdata)
        tg_send(f'❌ <b>HostShip 续期失败</b>\n'
                f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)\n'
                f'剩余: {renewal} 天\n错误: {msg}')
        sys.exit(1)

    # 4) 验证续期结果
    time.sleep(2)
    vcode, vattrs = get_server()
    new_renewal = vattrs.get('renewal', '?') if vcode == 200 else '?'
    log(f'续期后剩余: {new_renewal} 天')

    tg_send(f'🎉 <b>HostShip 续期成功</b>\n'
            f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)\n'
            f'剩余: {renewal} 天 → <b>{new_renewal}</b> 天')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'未捕获异常: {e!r}')
        tg_send(f'❌ <b>HostShip 续期脚本异常</b>\n'
                f'服务器: <code>{SERVER_ID}</code>\n'
                f'<code>{e!r}</code>')
        sys.exit(1)
