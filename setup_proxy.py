#!/usr/bin/env python3
"""
自动代理环境配置脚本:
- 若环境变量 CUSTOM_PROXY 为标准 HTTP / SOCKS5 代理，则直接供 Chromium 使用。
- 若为 tuic:// 协议或未配置，则自动解析参数生成 sing-box 配置并启动本地混合代理 (127.0.0.1:7890)。
"""

import os
import sys
import json
import urllib.parse

def main():
    custom_proxy = os.environ.get("CUSTOM_PROXY", "").strip()
    github_env = os.environ.get("GITHUB_ENV")

    # 1. 如果是原生 HTTP/SOCKS 协议，直接使用
    if any(custom_proxy.startswith(p) for p in ("http://", "https://", "socks5://", "socks4://")):
        print(f"✅ 检测到原生代理协议: {custom_proxy.split('@')[-1]}")
        if github_env:
            with open(github_env, "a", encoding="utf-8") as f:
                f.write(f"PROXY_URL={custom_proxy}\n")
                f.write("NEED_SINGBOX=0\n")
        return

    # 2. 否则需要 sing-box 转换 (tuic:// 或默认内置 TUIC)
    print("🔄 检测到 TUIC 协议或未配置外部代理，准备启动 sing-box 本地代理 (127.0.0.1:7890)...")

    # 默认兜底 TUIC 节点参数
    outbound_cfg = {
        "type": "tuic",
        "tag": "tuic-out",
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

    if custom_proxy.startswith("tuic://"):
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
            print(f"⚠️ 解析 CUSTOM_PROXY 失败，使用内置默认节点: {e}")

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
            f.write("NEED_SINGBOX=1\n")

if __name__ == "__main__":
    main()
