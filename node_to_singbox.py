#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 把 NODE_LINK (vmess:// 或 vless://) 转成 sing-box 配置并打印本地代理地址
# 用法: python node_to_singbox.py  (读环境变量 NODE_LINK, 写 config.json)
# 输出最后一行: 本地代理地址 http://127.0.0.1:1080
import base64
import json
import os
import sys
from urllib.parse import urlparse, parse_qs, unquote

LISTEN_PORT = 1080


def b64pad(s):
    return s + "=" * (-len(s) % 4)


def parse_vmess(link):
    raw = link[len("vmess://"):]
    # vmess 链接可能带 #备注
    raw = raw.split("#")[0].strip()
    cfg = json.loads(base64.b64decode(b64pad(raw)).decode("utf-8"))
    out = {
        "type": "vmess",
        "tag": "proxy",
        "server": cfg["add"],
        "server_port": int(cfg["port"]),
        "uuid": cfg["id"],
        "security": cfg.get("scy") or cfg.get("security") or "auto",
        "alter_id": int(cfg.get("aid") or 0),
    }
    tls = (cfg.get("tls") or "").lower()
    if tls in ("tls", "reality"):
        out["tls"] = {
            "enabled": True,
            "server_name": cfg.get("sni") or cfg.get("host") or cfg["add"],
            "insecure": False,
        }
    net = (cfg.get("net") or "tcp").lower()
    if net == "ws":
        tr = {"type": "ws"}
        if cfg.get("path"):
            tr["path"] = cfg["path"]
        if cfg.get("host"):
            tr["headers"] = {"Host": cfg["host"]}
        out["transport"] = tr
    elif net == "grpc":
        out["transport"] = {"type": "grpc",
                            "service_name": cfg.get("path") or ""}
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
        if (q.get("path") or [""])[0]:
            tr["path"] = q["path"][0]
        if (q.get("host") or [""])[0]:
            tr["headers"] = {"Host": q["host"][0]}
        out["transport"] = tr
    elif net == "grpc":
        out["transport"] = {"type": "grpc",
                            "service_name": (q.get("serviceName") or [""])[0]}
    return out


def main():
    link = (os.environ.get("NODE_LINK") or "").strip()
    if not link:
        print("❌ NODE_LINK 为空", file=sys.stderr)
        sys.exit(1)
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
    print(f"✅ 已生成 sing-box 配置: {outbound['server']}:{outbound['server_port']}")
    print(f"http://127.0.0.1:{LISTEN_PORT}")


if __name__ == "__main__":
    main()
