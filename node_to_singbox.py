#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 把 NODE_LINK (vmess:// / vless://) 转成 sing-box 配置并打印本地代理地址
# 用法: python node_to_singbox.py  (读环境变量 NODE_LINK, 写 singbox_config.json)
# 输出最后一行: 本地代理地址 http://127.0.0.1:1080
import base64
import json
import os
import sys
from urllib.parse import urlparse, parse_qs, unquote

LISTEN_PORT = 1080


def b64pad(s):
    return s + "=" * (-len(s) % 4)


def mask(s, keep=4):
    """脱敏输出, 只保留首尾各 keep 位"""
    if not s or len(s) <= keep * 2 + 3:
        return "***"
    return f"{s[:keep]}...{s[-keep:]}"


def parse_vmess(link):
    raw = link[len("vmess://"):]
    raw = raw.split("#")[0].strip()
    cfg = json.loads(base64.b64decode(b64pad(raw)).decode("utf-8"))

    # 标准字段映射
    host = cfg.get("host") or cfg.get("add") or ""
    path = cfg.get("path") or ""
    # sing-box 的 security: auto 兼容性最好
    scy = (cfg.get("scy") or cfg.get("security") or "auto").strip()
    if not scy or scy.lower() == "auto":
        scy = "auto"

    out = {
        "type": "vmess",
        "tag": "proxy",
        "server": cfg["add"],
        "server_port": int(cfg["port"]),
        "uuid": cfg["id"],
        "security": scy,
        "alter_id": int(cfg.get("aid") or 0),
    }

    tls = (cfg.get("tls") or "").lower()
    if tls in ("tls", "reality"):
        out["tls"] = {
            "enabled": True,
            "server_name": cfg.get("sni") or host or cfg["add"],
            "insecure": False,
        }
    elif tls in ("none", "", "0"):
        out["tls"] = {"enabled": False}

    net = (cfg.get("net") or "tcp").lower()
    fake_type = (cfg.get("type") or "none").lower()

    if net == "ws":
        tr = {"type": "ws"}
        if path:
            tr["path"] = path if path.startswith("/") else "/" + path
        if host:
            tr["headers"] = {"Host": host}
        out["transport"] = tr
        print(f"  transport=ws path={path or '(空)'} host={host or '(空)'}", file=sys.stderr)
    elif net == "grpc":
        out["transport"] = {
            "type": "grpc",
            "service_name": path or "",
        }
    elif net == "tcp" and fake_type == "http":
        # HTTP 伪装 (常见于 VMess 订阅)
        tr = {
            "type": "http",
            "host": [host] if host else [],
            "path": path or "/",
        }
        out["transport"] = tr
        print(f"  transport=http host={host or '(空)'} path={path or '(空)'}", file=sys.stderr)
    elif net == "tcp":
        if host or path:
            # 兜底: 有 host/path 但没显式声明 type, 也走 HTTP 伪装
            tr = {
                "type": "http",
                "host": [host] if host else [],
                "path": path or "/",
            }
            out["transport"] = tr
            print(f"  transport=http(兜底) host={host or '(空)'} path={path or '(空)'}", file=sys.stderr)

    print(f"  vmess server={cfg['add']}:{cfg['port']} security={scy} "
          f"net={net} type={fake_type} tls={tls or 'none'}", file=sys.stderr)
    return out


def parse_vless(link):
    u = urlparse(link)
    q = parse_qs(u.query)
    out = {
        "type": "vless",
        "tag": "proxy",
        "server": u.hostname,
        "server_port": u.port or 443,
        "uuid": unquote(u.username or ""),
    }
    flow = (q.get("flow") or [""])[0]
    if flow:
        out["flow"] = flow

    security = (q.get("security") or ["none"])[0].lower()
    if security in ("tls", "reality"):
        tls_cfg = {
            "enabled": True,
            "server_name": (q.get("sni") or [u.hostname])[0],
            "insecure": False,
        }
        if (q.get("fp") or [""])[0]:
            tls_cfg["utls"] = {"enabled": True,
                               "fingerprint": q["fp"][0]}
        if security == "reality":
            tls_cfg["reality"] = {
                "enabled": True,
                "public_key": (q.get("pbk") or [""])[0],
                "short_id": (q.get("sid") or [""])[0],
            }
        out["tls"] = tls_cfg

    net = (q.get("type") or ["tcp"])[0].lower()
    if net == "ws":
        tr = {"type": "ws"}
        p = (q.get("path") or [""])[0]
        if p:
            tr["path"] = p if p.startswith("/") else "/" + p
        h = (q.get("host") or [""])[0]
        if h:
            tr["headers"] = {"Host": h}
        out["transport"] = tr
    elif net == "grpc":
        out["transport"] = {
            "type": "grpc",
            "service_name": (q.get("serviceName") or [""])[0],
        }
    elif net == "tcp":
        # VLESS tcp 可能是 HTTP 伪装
        header_type = (q.get("headerType") or [""])[0]
        p = (q.get("path") or [""])[0]
        h = (q.get("host") or [""])[0]
        if header_type == "http" or h or p:
            out["transport"] = {
                "type": "http",
                "host": [h] if h else [],
                "path": p or "/",
            }

    print(f"  vless server={u.hostname}:{u.port or 443} "
          f"security={security} type={net}", file=sys.stderr)
    return out


def main():
    link = (os.environ.get("NODE_LINK") or "").strip()
    if not link:
        print("❌ NODE_LINK 为空", file=sys.stderr)
        sys.exit(1)

    print(f"  NODE_LINK={mask(link, 8)}", file=sys.stderr)

    if link.startswith("vmess://"):
        outbound = parse_vmess(link)
    elif link.startswith("vless://"):
        outbound = parse_vless(link)
    else:
        print(f"❌ 不支持的节点格式: {link[:20]}...", file=sys.stderr)
        sys.exit(1)

    config = {
        "log": {"level": "warn"},
        "inbounds": [{
            "type": "mixed",
            "tag": "in",
            "listen": "127.0.0.1",
            "listen_port": LISTEN_PORT,
        }],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
    }

    with open("singbox_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"✅ 已生成 sing-box 配置 → singbox_config.json", file=sys.stderr)
    print(f"http://127.0.0.1:{LISTEN_PORT}")


if __name__ == "__main__":
    main()
