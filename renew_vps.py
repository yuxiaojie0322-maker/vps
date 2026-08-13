import os
import requests

def log(level: str, message: str):
    print(f"[{level}] {message}")

# ────────────────────────────────────────────────────────
# 1. NopeCHA 额度与试用资格检查
# ────────────────────────────────────────────────────────
def check_nopecha_availability(proxy: str = None) -> tuple[str, str | None]:
    """
    检查环境变量中的 NopeCHA Key 是否有效且有余额。
    若无有效 Key，自动降级测试当前 IP 是否具备试用资格。
    
    :param proxy: 可选代理地址 (例如: socks5://127.0.0.1:1080)
    :return: (valid_key, error_message)
    """
    raw_key = os.getenv("NOPECHA_KEY", "").strip()
    keys = [k.strip() for k in raw_key.splitlines() if k.strip()] if raw_key else []

    # 规范化代理 Scheme (SOCKS 自动转换为 SOCKS5h 以防本地 DNS 污染)
    proxies = None
    if proxy:
        proxy_url = proxy.replace("socks5://", "socks5h://").replace("socks://", "socks5h://")
        proxies = {"http": proxy_url, "https": proxy_url}

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    # 1. 遍历验证配置的所有 KEY
    for idx, key in enumerate(keys, start=1):
        try:
            resp = session.get(f"https://api.nopecha.com/v1/status?key={key}", timeout=15)
            data = resp.json()
            credit = data.get("credit", 0)
            if "error" not in data and credit > 0:
                log("INFO", f"✅ 已选择有效 KEY #{idx} (剩余额度: {credit})")
                return key, None
            else:
                err_msg = data.get("error", "额度已用尽")
                log("INFO", f"KEY #{idx} 无效或已消耗完: {err_msg}")
        except Exception as e:
            log("WARN", f"KEY #{idx} 状态查询网络失败: {e}")

    if keys:
        log("INFO", "所有配置的 Key 均无可用额度，尝试回退测试 IP 试用资格...")

    # 2. 降级：测试当前 IP 试用资格
    try:
        resp = session.get("https://api.nopecha.com/v1/status", timeout=15)
        data = resp.json()
        credit = data.get("credit", 0)
        if "error" not in data and credit > 0:
            log("INFO", f"✅ 当前 IP 具备试用资格 (剩余试用额度: {credit})")
            return "", None
        else:
            msg = "当前 IP 不具备试用资格" if "error" in data else "试用额度已用尽"
            log("ERROR", msg)
    except Exception as e:
        msg = f"试用状态查询失败: {e}"
        log("ERROR", msg)

    # 3. 错误总结返回
    if keys:
        return "", "已尝试全部 NopeCHA Key，但均无可用额度；且当前 IP 不具备试用资格。"
    return "", "未配置 NopeCHA Key，且当前 IP 不具备试用资格。"


# ────────────────────────────────────────────────────────
# 2. NopeCHA 插件 API Key 动态注入补丁
# ────────────────────────────────────────────────────────
def patch_nopecha(nopecha_path: str, api_key: str) -> bool:
    """
    将 API Key 强行注入到解压好的 NopeCHA 扩展后台文件中。
    
    :param nopecha_path: 扩展解压根目录
    :param api_key: 要注入的 NopeCHA 密钥
    :return: 是否注入/准备就绪成功
    """
    if not api_key:
        log("INFO", "✅ 当前处于免费试用模式（无需注入 Key）")
        return True  # 试用模式下流程正常，返回 True 以便继续启动浏览器

    # 自动探测插件后台脚本入口文件路径
    candidate_files = [
        os.path.join(nopecha_path, "assets", "qrmm9f.js"),
        os.path.join(nopecha_path, "background.js"),
        os.path.join(nopecha_path, "manifest.json")  # 兜底查找
    ]

    bg_path = None
    for path in candidate_files[:-1]:
        if os.path.exists(path):
            bg_path = path
            break

    if not bg_path:
        log("ERROR", f"未找到可注入的背景 JavaScript 文件，搜索路径: {candidate_files[:-1]}")
        return False

    try:
        # 读取背景脚本
        with open(bg_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查防重复注入标记
        if "// NopeCHA-Inject" in content:
            log("INFO", "NopeCHA 扩展已处理过注入，跳过重复写入")
            return True

        # 构建安全的嵌入逻辑，确保 chrome.storage 挂载后再覆盖配置
        inject_code = f"""// NopeCHA-Inject
(function(){{
    const s = {{ enabled: true, key: "{api_key}", auto_solve_hcaptcha: true, auto_solve_recaptcha: true }};
    function applyKey() {{
        try {{
            if (typeof chrome !== 'undefined' && chrome.storage) {{
                if (chrome.storage.local) chrome.storage.local.set({{ settings: s, key: "{api_key}" }});
                if (chrome.storage.sync) chrome.storage.sync.set({{ settings: s, key: "{api_key}" }});
            }}
        }} catch(e) {{}}
    }}
    applyKey();
    setTimeout(applyKey, 500);
    setTimeout(applyKey, 2000);
}})();\n"""

        # 写入文件
        with open(bg_path, "w", encoding="utf-8") as f:
            f.write(inject_code + content)

        log("INFO", "✅ 成功将 NopeCHA API Key 补丁注入至插件脚本中")
        return True

    except Exception as e:
        log("ERROR", f"Patch 文件写入过程失败: {e}")
        return False
