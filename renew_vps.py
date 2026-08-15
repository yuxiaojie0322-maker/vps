"""
VPSFree.es 自动续期脚本 (防检测增强 + NopeCHA 修复版)
- 增加了浏览器指纹伪装，去除 navigator.webdriver 特征
- 修复 reCAPTCHA 完成状态的多重检测（name 属性与 iframe 勾选状态）
- 优化 NopeCHA 插件 Key 注入兼容性
- 采用真实事件模拟点击登录按钮（避免 WHMCS CSRF/Token 丢失）
- 增加关键阶段截图与错误日志
"""

import os
import sys
import time
import requests
from datetime import datetime

# ========== 配置 ==========
EMAIL = os.environ.get("VPS_EMAIL", "")
PASSWORD = os.environ.get("VPS_PASSWORD", "")
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
MANAGER_URL = "https://manager.vpsfree.es"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "nopecha", "unpacked")

# Telegram 推送配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()


def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}")


# ====================================================================
# Telegram 推送
# ====================================================================
def send_tg_text(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("未配置 TG 推送，跳过", "WARN")
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=15)
        return resp.json().get("ok", False)
    except Exception as e:
        log(f"TG 发送异常: {e}", "ERROR")
        return False


def send_tg_photo(photo_path, caption=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("未配置 TG 推送，跳过", "WARN")
        return False
    if not os.path.exists(photo_path):
        log(f"截图不存在: {photo_path}", "WARN")
        return send_tg_text(caption)
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": TG_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            resp = requests.post(url, files=files, data=data, timeout=30)
        res_json = resp.json()
        if res_json.get("ok"):
            log("TG 图片消息已成功发送 ✅")
            return True
        else:
            log(f"TG 图片发送失败: {res_json}，改发纯文本...", "WARN")
            return send_tg_text(caption)
    except Exception as e:
        log(f"TG 发送异常: {e}", "ERROR")
        return send_tg_text(caption)


# ====================================================================
# Patch NopeCHA 扩展 (增强版多入口匹配)
# ====================================================================
def patch_nopecha(nopecha_path, api_key):
    if not api_key:
        log("未设置 NOPECHA_KEY，使用试用模式", "WARN")
        return False

    # 遍历可能存在的入口文件
    possible_files = [
        os.path.join(nopecha_path, "assets", "qrmm9f.js"),
        os.path.join(nopecha_path, "background.js"),
        os.path.join(nopecha_path, "service_worker.js"),
        os.path.join(nopecha_path, "dist", "background.js"),
    ]

    target_file = None
    for f in possible_files:
        if os.path.exists(f):
            target_file = f
            break

    # 如果都没找到，搜索根目录下所有 js
    if not target_file:
        for root, _, files in os.walk(nopecha_path):
            for file in files:
                if file.endswith(".js") and ("bg" in file or "background" in file or "service" in file):
                    target_file = os.path.join(root, file)
                    break
            if target_file:
                break

    if not target_file:
        log(f"未找到 NopeCHA 入口 JS 文件，跳过注入", "WARN")
        return False

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()

        if "// NopeCHA-Inject" in content:
            log("NopeCHA 插件已注入过 Key，跳过")
            return True

        inject = f"""// NopeCHA-Inject
(function(){{
    const s={{
        enabled: true,
        key: "{api_key}",
        auto_solve_hcaptcha: true,
        auto_solve_recaptcha: true,
        recaptcha_solve_method: "image"
    }};
    function applySettings(){{
        if (typeof chrome !== 'undefined' && chrome.storage) {{
            if (chrome.storage.local) chrome.storage.local.set({{settings:s, key:"{api_key}"}});
            if (chrome.storage.sync) chrome.storage.sync.set({{settings:s, key:"{api_key}"}});
        }}
    }}
    applySettings();
    setTimeout(applySettings, 1000);
    setTimeout(applySettings, 3000);
}})();\n"""
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(inject + content)

        log(f"✅ NopeCHA API Key 成功注入插件文件: {os.path.basename(target_file)}")
        return True
    except Exception as e:
        log(f"Patch 失败: {e}", "ERROR")
        return False


# ====================================================================
# 主流程
# ====================================================================
def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright", "ERROR")
        return False

    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(os.path.join(EXT_PATH, "manifest.json"))
    if ext_ok and NOPECHA_KEY:
        patch_nopecha(EXT_PATH, NOPECHA_KEY)

    with sync_playwright() as p:
        # 防检测启动参数
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",  # 关键：移除自动化受控标志
            "--disable-infobars",
            "--no-first-run",
            "--disable-background-networking",
        ]
        if ext_ok:
            launch_args.extend([
                f"--disable-extensions-except={EXT_PATH}",
                f"--load-extension={EXT_PATH}",
            ])

        proxy_config = None
        if PROXY_URL:
            clean_proxy = PROXY_URL.split("#")[0].strip()
            if clean_proxy.startswith(("http://", "https://", "socks5://", "socks4://")):
                log(f"🌐 正在通过代理建立连接: {clean_proxy.split('@')[-1]}")
                proxy_config = {"server": clean_proxy}
            else:
                log("⚠️ PROXY_URL 格式非标准，降级为直连网络", "WARN")

        # 启动持久化上下文
        browser = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/playwright-data",
            headless=False,
            proxy=proxy_config,
            args=launch_args,
            ignore_default_args=["--enable-automation"],  # 关键：移除自动化提示栏
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-US",
            bypass_csp=True,
            ignore_https_errors=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # 注入 Stealth 伪装脚本，彻底抹除 webdriver 痕迹
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
        """)

        try:
            log("打开登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 填充用户名和密码
            log("输入登录凭证...")
            page.locator("input[name='username'], #inputEmail").first.fill(EMAIL)
            page.locator("input[name='password'], #inputPassword").first.fill(PASSWORD)

            # 等待 reCAPTCHA 渲染
            log("检查验证码状态...")
            time.sleep(2)

            # 仅在复选框未勾选时轻柔触发一次
            try:
                recaptcha_frame = page.frame_locator('iframe[title*="reCAPTCHA"], iframe[src*="recaptcha"]').first
                checkbox = recaptcha_frame.locator('#recaptcha-anchor, .recaptcha-checkbox').first
                if checkbox.is_visible():
                    aria_checked = checkbox.get_attribute("aria-checked")
                    if aria_checked != "true":
                        log("点击验证码勾选框以触发识别...")
                        checkbox.click()
            except Exception as e:
                log(f"未主动点击验证码框（插件可能已自动接管）: {e}", "DEBUG")

            log("等待 NopeCHA 插件识别验证码（最长 90 秒）...")
            solved = False
            for i in range(90):
                # 增强的多重检测：检查 name/id/value 以及 iframe 的 aria-checked 属性
                solved = page.evaluate("""() => {
                    // 1. 检查页面上任意 reCAPTCHA response textarea
                    const textareas = document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response');
                    for (const ta of textareas) {
                        if (ta.value && ta.value.trim().length > 10) return true;
                    }
                    // 2. 检查 iframe 勾选框是否已被勾选 (checked)
                    const iframes = document.querySelectorAll('iframe[title*="reCAPTCHA"]');
                    for (const f of iframes) {
                        try {
                            const anchor = f.contentDocument?.querySelector('.recaptcha-checkbox-checked, [aria-checked="true"]');
                            if (anchor) return true;
                        } catch(e) {}
                    }
                    return false;
                }""")

                if solved:
                    log(f"🎉 验证码成功通过！耗时 {i + 1} 秒 ✅")
                    break
                time.sleep(1)

            if not solved:
                log("⚠️ 验证码未能在限定时间内完成识别，截图并尝试点击提交...", "WARN")
                page.screenshot(path="captcha_timeout.png")

            time.sleep(2)

            # 模拟真实点击提交按钮（触发 Lagom/WHMCS 表单事件）
            log("正在点击登录按钮...")
            submit_btn = page.locator("button#login, button[type='submit'], input[type='submit']").first
            
            with page.expect_navigation(timeout=15000, wait_until="domcontentloaded"):
                submit_btn.click()

            time.sleep(4)

            # 检查是否成功登录
            current_url = page.url.lower()
            if "login" in current_url:
                # 尝试检查页面上的错误提示
                error_msg = ""
                try:
                    alert = page.locator(".alert-danger, .alert-error, .login-error").first
                    if alert.is_visible():
                        error_msg = alert.inner_text().strip()
                except:
                    pass

                log(f"登录失败，仍停留在登录页面。页面提示: {error_msg}", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log(f"登录成功 ✅，当前跳转页面: {page.url}")
            return do_renew(page)

        except Exception as e:
            log(f"运行流程异常: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_renew(page):
    log("访问服务列表...")
    page.goto(f"{MANAGER_URL}/clientarea.php?action=products", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    log("查找 Manage 按钮...")
    try:
        manage_btn = page.locator("text=Manage, a:has-text('Manage')").first
        if manage_btn.is_visible():
            manage_btn.click()
            log("点击 Manage 成功 ✅")
        else:
            log("未发现 Manage 按钮，尝试查找产品条目", "WARN")
            page.screenshot(path="no_manage_btn.png")
            return False
    except Exception as e:
        log(f"点击 Manage 失败: {e}", "ERROR")
        page.screenshot(path="no_manage_btn.png")
        return False

    time.sleep(3)

    log("查找续期按钮...")
    try:
        renew_btn = page.locator("text=Renew For 7 days, text=Renew").first
        if renew_btn.is_visible():
            renew_btn.click()
            log("点击续期按钮成功 ✅")
        else:
            log("未找到续期按钮（可能已处于最新续期状态或未到期）", "WARN")
            page.screenshot(path="no_renew_btn.png")
            return True
    except Exception as e:
        log(f"点击续期按钮异常: {e}", "ERROR")
        page.screenshot(path="no_renew_btn.png")
        return False

    time.sleep(3)
    try:
        confirm_btn = page.locator("button:has-text('Confirm'), a:has-text('Confirm'), text=Confirm").first
        if confirm_btn.is_visible():
            confirm_btn.click()
            log("确认续期成功 ✅")
            time.sleep(2)
    except:
        pass

    log("🎉 续期完成！")
    page.screenshot(path="renew_success.png")
    return True


def main():
    log("=" * 40)
    log("VPSFree 自动续期运行开始")
    log("=" * 40)

    if not EMAIL or not PASSWORD:
        log("缺少 VPS_EMAIL 或 VPS_PASSWORD 环境变量！", "ERROR")
        sys.exit(1)

    log(f"正在处理账号: {EMAIL}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    success = renew_vps()

    if success:
        log("续期流程完成，正在发送 TG 成功通知...")
        caption = (
            f"✅ <b>VPSFree 自动续期成功</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"🔁 下次续期: 7天后\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🖼 页面截图如下"
        )
        shot_path = "renew_success.png"
        if not os.path.exists(shot_path):
            for p in ["no_renew_btn.png", "no_manage_btn.png"]:
                if os.path.exists(p):
                    shot_path = p
                    break

        send_tg_photo(shot_path, caption)
    else:
        log("续期流程失败，正在发送 TG 失败通知...", "ERROR")
        caption = (
            f"❌ <b>VPSFree 续期失败</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"💡 请查看附件截图排查"
        )
        for shot in ["login_failed.png", "captcha_timeout.png", "renew_error.png", "no_manage_btn.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)

        sys.exit(1)


if __name__ == "__main__":
    main()
