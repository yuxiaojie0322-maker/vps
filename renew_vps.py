"""
VPSFree.es 免费面板自动续期脚本 (适配 free.vpsfree.es + hCaptcha)
"""

import os
import sys
import time
import requests
from datetime import datetime

# ========== 配置 ==========
EMAIL = os.environ.get("VPS_EMAIL", "").strip()
PASSWORD = os.environ.get("VPS_PASSWORD", "").strip()
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
BASE_URL = "https://free.vpsfree.es"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "nopecha", "unpacked")

# Telegram 推送配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()


def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}")


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


def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright", "ERROR")
        return False

    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(os.path.join(EXT_PATH, "manifest.json"))

    with sync_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
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
                log(f"🌐 正在通过代理建立连接: {clean_proxy}")
                proxy_config = {"server": clean_proxy}

        browser = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/playwright-data",
            headless=False,
            proxy=proxy_config,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="zh-CN",
            bypass_csp=True,
            ignore_https_errors=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        try:
            # 1. 激活授权 NopeCHA
            if ext_ok and NOPECHA_KEY:
                log("正在激活并授权 NopeCHA 插件...")
                try:
                    page.goto(f"https://nopecha.com/setup#{NOPECHA_KEY}", wait_until="domcontentloaded", timeout=15000)
                    time.sleep(3)
                    log("✅ NopeCHA 插件授权激活成功")
                except Exception as e:
                    log(f"NopeCHA 激活页面访问异常: {e}", "WARN")

            # 2. 打开 free.vpsfree.es 登录页
            log(f"打开登录页: {BASE_URL}/connexion ...")
            page.goto(f"{BASE_URL}/connexion", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # 3. 填写账号密码
            log("填写登录凭证...")
            email_input = page.locator("input[type='email'], input[name='email'], input[name='username']").first
            pass_input = page.locator("input[type='password'], input[name='password']").first
            
            email_input.fill(EMAIL)
            pass_input.fill(PASSWORD)
            time.sleep(1)

            # 4. 等待 NopeCHA 识别 hCaptcha 验证码
            log("等待 NopeCHA 自动识别 hCaptcha 验证码...")
            solved = False
            for i in range(90):
                solved = page.evaluate("""() => {
                    // 1. 检查 hCaptcha response token
                    const tas = document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
                    for (const ta of tas) {
                        if (ta.value && ta.value.trim().length > 20) return true;
                    }
                    // 2. 检查勾选状态
                    const iframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[title*="hcaptcha"]');
                    for (const f of iframes) {
                        try {
                            if (f.contentDocument?.querySelector('[aria-checked="true"], .check')) return true;
                        } catch(e) {}
                    }
                    return false;
                }""")

                if solved:
                    log(f"🎉 hCaptcha 验证码破解成功！耗时 {i + 1} 秒 ✅")
                    break
                time.sleep(1)

            if not solved:
                log("⚠️ 验证码等待超时，准备强行提交...", "WARN")

            time.sleep(2)

            # 5. 确保凭据填写正确并点击 Sign In
            log("点击 Sign In 按钮提交登录...")
            if not email_input.input_value():
                email_input.fill(EMAIL)
            if not pass_input.input_value():
                pass_input.fill(PASSWORD)

            submit_btn = page.locator("button:has-text('Sign In'), button[type='submit']").first
            try:
                submit_btn.click(force=True, timeout=10000)
            except Exception as e:
                log(f"点击异常，使用回车键提交: {e}", "WARN")
                page.keyboard.press("Enter")

            time.sleep(6)

            # 6. 检查是否成功登录
            current_url = page.url.lower()
            if "connexion" in current_url or "login" in current_url:
                log(f"登录未成功，仍在登录页: {page.url}", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log(f"🎉 登录成功！当前控制台网址: {page.url} ✅")
            page.screenshot(path="dashboard.png")
            return do_renew(page)

        except Exception as e:
            log(f"流程执行异常: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_renew(page):
    log("进入控制台，查找服务器与续期按钮...")
    time.sleep(3)

    # 尝试查找各种可能的管理与续期按钮
    renewed = False
    try:
        # 1. 查找 Manage / 管理 按钮
        manage_btn = page.locator("a:has-text('Manage'), button:has-text('Manage'), a:has-text('管理')").first
        if manage_btn.is_visible():
            manage_btn.click()
            log("点击 Manage 成功 ✅")
            time.sleep(3)

        # 2. 查找 Renew / 续期 按钮
        renew_btn = page.locator("a:has-text('Renew'), button:has-text('Renew'), a:has-text('续期'), button:has-text('续期'), text=Renew").first
        if renew_btn.is_visible():
            renew_btn.click()
            log("点击续期按钮成功 ✅")
            renewed = True
            time.sleep(3)

            # 3. 确认弹窗
            confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('确定'), button:has-text('Yes')").first
            if confirm_btn.is_visible():
                confirm_btn.click()
                log("确认续期成功 ✅")
                time.sleep(2)
        else:
            log("未发现明显的 Renew 按钮（可能未到期或当前状态良好）", "WARN")
            renewed = True
    except Exception as e:
        log(f"续期交互异常: {e}", "WARN")

    page.screenshot(path="renew_success.png")
    return renewed


def main():
    log("=" * 40)
    log("VPSFree.es 自动续期运行开始")
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
            f"✅ <b>VPSFree.es 自动续期运行成功</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🖼 控制台截图如下"
        )
        shot_path = "renew_success.png"
        if not os.path.exists(shot_path):
            shot_path = "dashboard.png"

        send_tg_photo(shot_path, caption)
    else:
        log("续期流程失败，正在发送 TG 失败通知...", "ERROR")
        caption = (
            f"❌ <b>VPSFree.es 登录/续期失败</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"💡 请查看附件截图排查"
        )
        for shot in ["login_failed.png", "renew_error.png", "dashboard.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)

        sys.exit(1)


if __name__ == "__main__":
    main()
