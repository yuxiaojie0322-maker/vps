#!/usr/bin/env python3
"""
自动代理环境配置脚本:
- 若环境变量 CUSTOM_PROXY 为标准 HTTP / SOCKS5 代理，则直接供 Chromium 使用。
- 若为 hysteria2 / hy2 / tuic 协议，则自动解析参数生成 sing-box 配置并启动本地混合代理 (127.0.0.1:7890)。
- 若未配置或解析失败，使用内置备用节点。
"""

import os
import sys
import json
import urllib.parse

def main():
    custom_proxy = os.environ.get("CUSTOM_PROXY", "").strip().strip("'\"")
    github_env = os.environ.get("GITHUB_ENV")

    schema = custom_proxy.split("://")[0].lower() if "://" in custom_proxy else ""
    print(f"🔍 代理配置检测: 长度={len(custom_proxy)}, 协议={schema or '未指定'}")

    # 1. 如果是原生 HTTP/SOCKS 协议，直接使用
    if schema in ("http", "https", "socks5", "socks4"):
        print(f"✅ 检测到原生代理协议: {custom_proxy.split('@')[-1]}")
        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"PROXY_URL={custom_proxy}\n")
        return

    # 2. 否则需要 sing-box 转换
    print(f"🔄 检测到 {schema or '空'} 协议，准备配置 sing-box 本地代理 (127.0.0.1:7890)...")

    # 默认兜底 TUIC 节点参数
    outbound_cfg = {
        "type": "tuic",
        "tag": "proxy-out",
        "server": "payload.ingress.hnhost.net",
        "server_port": 10373,
        "uuid": "adf4e830-d1b1-4120-9f26-6a6b1f04d6d2",
        "password": "",
        "congestion_control": "bbr",
        "udp_relay_mode": "native",
        "tls": {
            "enabled": True,
            "server_name": "www.bing.com",
            "insecure": True,
            "alpn": ["h3"]
        }
    }

    if schema in ("hysteria2", "hy2"):
        try:
            u = urllib.parse.urlparse(custom_proxy)
            qs = urllib.parse.parse_qs(u.query)
            password = u.password if u.password else (u.username or "")
            sni = qs.get("sni", [None])[0] or u.hostname
            insecure = qs.get("insecure", ["1"])[0].lower() in ("1", "true")
            port = int(u.port or 443)

            hy2_out = {
                "type": "hysteria2",
                "tag": "proxy-out",
                "server": u.hostname,
                "server_port": port,
                "password": password,
                "tls": {
                    "enabled": True,
                    "server_name": sni,
                    "insecure": True
                }
            }
            obfs = qs.get("obfs", [None])[0]
            if obfs:
                obfs_password = qs.get("obfs-password", [""])[0]
                hy2_out["obfs"] = {
                    "type": obfs,
                    "password": obfs_password
                }
            outbound_cfg = hy2_out
            print(f"✅ 成功从 CUSTOM_PROXY 解析 Hysteria2 节点: {u.hostname}:{port}, SNI={sni}")
        except Exception as e:
            print(f"⚠️ 解析 Hysteria2 节点异常，使用备用配置: {e}")

    elif schema == "tuic":
        try:
            u = urllib.parse.urlparse(custom_proxy)
            qs = urllib.parse.parse_qs(u.query)
            if u.hostname:
                outbound_cfg["server"] = u.hostname
            if u.port:
                outbound_cfg["server_port"] = int(u.port)
            if u.username:
                outbound_cfg["uuid"] = u.username
            if u.password:
                outbound_cfg["password"] = u.password

            sni = qs.get("sni", [None])[0]
            if sni:
                outbound_cfg["tls"]["server_name"] = sni
            cc = qs.get("congestion_control", [None])[0]
            if cc:
                outbound_cfg["congestion_control"] = cc
            alpn = qs.get("alpn", [])
            if alpn:
                outbound_cfg["tls"]["alpn"] = alpn[0].split(",")
            print(f"✅ 成功从 CUSTOM_PROXY 解析 TUIC 节点: {outbound_cfg['server']}:{outbound_cfg['server_port']}")
        except Exception as e:
            print(f"⚠️ 解析 TUIC 失败，使用备用配置: {e}")

    singbox_cfg = {
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 7890
            }
        ],
        "outbounds": [outbound_cfg]
    }

    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(singbox_cfg, f, indent=2)

    print("✅ 已生成 sing-box config.json")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write("PROXY_URL=http://127.0.0.1:7890\n")

if __name__ == "__main__":
    main()
