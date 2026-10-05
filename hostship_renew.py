#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
host-ship.com (Jexactyl 面板) 免费服务器自动续期
纯 Client API Key 方案，无需浏览器 / 无需模拟登录

流程:
  1. GET  /api/client/servers/{id}   读取服务器信息 + 剩余续期天数
  2. 剩余天数 <= 阈值 -> POST /api/client/servers/{id}/renew
  3. 再次 GET 验证剩余天数变化
  4. Pillow 生成状态卡片 -> Telegram sendPhoto 每轮必发 (带图)

环境变量:
  HS_API_KEY       面板 Client API Key (ptlc_ 开头)          [必填]
  HS_SERVER_ID     服务器 identifier, 即 /server/xxx 里那段   [默认 c12fd5d9]
  HS_PANEL_URL     面板地址                                   [默认 https://panel.host-ship.com]
  RENEW_THRESHOLD  剩余天数 <= 该值时才续期                    [默认 4]
  TG_BOT_TOKEN     Telegram Bot Token                        [可选, 不填则不通知]
  TG_CHAT_ID       Telegram Chat ID                          [可选]
"""

import io
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid


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


# ---------------- Telegram ----------------

def _tg_post(method, fields, files=None):
    """发送 multipart/form-data 到 Telegram Bot API"""
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f'--{boundary}\r\n'.encode())
        body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        body.write(f'{v}\r\n'.encode())
    for k, (fname, data, mime) in (files or {}).items():
        body.write(f'--{boundary}\r\n'.encode())
        body.write(f'Content-Disposition: form-data; name="{k}"; '
                   f'filename="{fname}"\r\n'.encode())
        body.write(f'Content-Type: {mime}\r\n\r\n'.encode())
        body.write(data)
        body.write(b'\r\n')
    body.write(f'--{boundary}--\r\n'.encode())

    url = f'https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}'
    req = urllib.request.Request(url, data=body.getvalue(), headers={
        'Content-Type': f'multipart/form-data; boundary={boundary}',
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.status


def tg_notify(caption_html, photo_png=None):
    """发送 TG 通知, 有图走 sendPhoto, 无图/失败回退 sendMessage"""
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        log('TG 未配置, 跳过通知')
        return
    try:
        if photo_png:
            code = _tg_post('sendPhoto', {
                'chat_id': TG_CHAT_ID,
                'caption': caption_html,
                'parse_mode': 'HTML',
            }, files={'photo': ('status.png', photo_png, 'image/png')})
            log(f'TG 图片通知已发送 (HTTP {code})')
            return
    except Exception as e:
        log(f'sendPhoto 失败, 回退纯文本: {e}')
    try:
        code = _tg_post('sendMessage', {
            'chat_id': TG_CHAT_ID,
            'text': caption_html,
            'parse_mode': 'HTML',
            'disable_web_page_preview': 'true',
        })
        log(f'TG 文本通知已发送 (HTTP {code})')
    except Exception as e:
        log(f'TG 通知发送失败: {e}')


# ---------------- 状态卡片 ----------------

def render_card(name, status, detail, days_before, days_after=None):
    """生成深色状态卡片 PNG, 返回 bytes; Pillow 不可用返回 None"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        log('Pillow 未安装, 跳过卡片生成')
        return None
    try:
        W, H = 900, 420
        colors = {
            'ok':   ('#22c55e', 'OK'),
            'renew': ('#22c55e', 'RENEWED'),
            'skip': ('#38bdf8', 'NO ACTION'),
            'warn': ('#f59e0b', 'WARNING'),
            'fail': ('#ef4444', 'FAILED'),
        }
        accent, label = colors.get(status, colors['ok'])
        img = Image.new('RGB', (W, H), '#0b1220')
        d = ImageDraw.Draw(img)

        def font(size, bold=False):
            for p in (
                '/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf' % ('-Bold' if bold else ''),
                'C:/Windows/Fonts/%s' % ('arialbd.ttf' if bold else 'arial.ttf'),
            ):
                if os.path.exists(p):
                    return ImageFont.truetype(p, size)
            return ImageFont.load_default()

        f_title = font(30, True)
        f_big = font(110, True)
        f_mid = font(28)
        f_small = font(22)

        # 顶部条 + 标题
        d.rectangle([0, 0, W, 6], fill=accent)
        d.text((40, 30), 'HostShip Auto Renew', font=f_title, fill='#e2e8f0')
        d.rounded_rectangle([W - 220, 28, W - 40, 72], 10, outline=accent, width=2)
        tw = d.textlength(label, font=f_small)
        d.text((W - 130 - tw / 2, 40), label, font=f_small, fill=accent)

        # 大数字: 剩余天数
        days_now = days_after if days_after is not None else days_before
        days_txt = '--' if days_now is None else str(days_now)
        d.text((40, 110), days_txt, font=f_big, fill=accent)
        dw = d.textlength(days_txt, font=f_big)
        d.text((60 + dw, 190), 'days left', font=f_mid, fill='#94a3b8')

        # 进度条 (满刻度 30 天)
        bar_x, bar_y, bar_w, bar_h = 40, 270, W - 80, 26
        d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], 13, fill='#1e293b')
        if isinstance(days_now, (int, float)):
            frac = max(0.0, min(1.0, days_now / 30.0))
            if frac > 0:
                d.rounded_rectangle([bar_x, bar_y, bar_x + int(bar_w * frac), bar_y + bar_h],
                                    13, fill=accent)

        # 信息行
        info = f'Server: {name} ({SERVER_ID})'
        if days_after is not None:
            info += f'   |   {days_before} -> {days_after} days'
        d.text((40, 320), info, font=f_small, fill='#cbd5e1')
        d.text((40, 355), detail[:90], font=f_small, fill='#64748b')
        d.text((40, 385), time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
               font=f_small, fill='#475569')

        buf = io.BytesIO()
        img.save(buf, 'PNG')
        return buf.getvalue()
    except Exception as e:
        log(f'卡片生成失败: {e}')
        return None


# ---------------- 面板 API ----------------

def api(method, path):
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
    if body:
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


def notify(status, title, lines, days_before, days_after=None, detail=''):
    caption = f'{title}\n' + '\n'.join(lines)
    card = render_card(SERVER_NAME, status, detail or title,
                       days_before, days_after)
    tg_notify(caption, card)


SERVER_NAME = '?'


def main():
    global SERVER_NAME
    if not API_KEY:
        log('错误: 未设置 HS_API_KEY')
        tg_notify('❌ <b>HostShip 续期失败</b>\n原因: 未配置 HS_API_KEY')
        sys.exit(1)

    log(f'面板: {PANEL}  服务器: {SERVER_ID}  阈值: ≤{RENEW_THRESHOLD} 天')

    # 1) 读取服务器状态
    code, attrs = get_server()
    if code != 200:
        msg = fmt_err(code, attrs)
        log(f'读取服务器信息失败: {msg}')
        notify('fail', '❌ <b>HostShip 续期失败</b>',
               [f'服务器: <code>{SERVER_ID}</code>', f'读取信息失败: {msg}'],
               None, detail=msg)
        sys.exit(1)

    name = attrs.get('name', '?')
    SERVER_NAME = name
    renewal = attrs.get('renewal', None)
    suspended = attrs.get('is_suspended', False)
    log(f'服务器: {name} | 剩余: {renewal} 天 | 暂停: {suspended}')

    if suspended:
        notify('warn', '⚠️ <b>HostShip 服务器已被暂停</b>',
               [f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)',
                f'剩余: {renewal} 天', '请登录面板人工处理'],
               renewal, detail='Server suspended')
        sys.exit(1)

    # 2) 判断是否需要续期
    if renewal is not None and renewal > RENEW_THRESHOLD:
        log(f'剩余 {renewal} 天 > 阈值 {RENEW_THRESHOLD}, 本轮无需续期')
        notify('skip', '✅ <b>HostShip 巡检正常</b>',
               [f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)',
                f'剩余: <b>{renewal}</b> 天 (阈值 {RENEW_THRESHOLD}, 无需续期)'],
               renewal, detail='No renewal needed')
        return

    # 3) 执行续期
    log(f'剩余 {renewal} 天 ≤ {RENEW_THRESHOLD}, 执行续期...')
    rcode, rdata = api('POST', f'/api/client/servers/{SERVER_ID}/renew')
    log(f'续期响应: HTTP {rcode} {str(rdata)[:200]}')

    if rcode not in (200, 204):
        msg = fmt_err(rcode, rdata)
        notify('fail', '❌ <b>HostShip 续期失败</b>',
               [f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)',
                f'剩余: {renewal} 天', f'错误: {msg}'],
               renewal, detail=msg)
        sys.exit(1)

    # 4) 验证续期结果
    time.sleep(2)
    vcode, vattrs = get_server()
    new_renewal = vattrs.get('renewal', None) if vcode == 200 else None
    log(f'续期后剩余: {new_renewal} 天')

    notify('renew', '🎉 <b>HostShip 续期成功</b>',
           [f'服务器: <b>{name}</b> (<code>{SERVER_ID}</code>)',
            f'剩余: {renewal} 天 → <b>{new_renewal}</b> 天'],
           renewal, new_renewal, detail='Renewal successful')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'未捕获异常: {e!r}')
        tg_notify(f'❌ <b>HostShip 续期脚本异常</b>\n'
                  f'服务器: <code>{SERVER_ID}</code>\n<code>{e!r}</code>')
        sys.exit(1)
