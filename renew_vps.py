"""
VPSFree.es 自动续期脚本 (NopeCHA 官方标准激活版)
- 采用 nopecha.com/setup 官方标准协议激活插件
- 全自动等待并识别 reCAPTCHA 九宫格验证码
- 自动确认续期并发送 Telegram 截图通知
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
# 主流程
# ====================================================================
def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright", "ERROR")
        return False

    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(os.path.join(EXT_PATH, "manifest.json"))
    if not ext_ok:
        log(f"⚠️ 未找到插件目录: {EXT_PATH}", "WARN")

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
            locale="en-US",
            bypass_csp=True,
            ignore_https_errors=True,
        )

        page = browser.pages[0] if browser.pages else browser.new_page()

        # 伪装抹除 webdriver
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        try:
            # 1. 官方标准激活 NopeCHA Key
            if ext_ok and NOPECHA_KEY:
                log("正在激活并授权 NopeCHA 插件...")
                try:
                    page.goto(f"https://nopecha.com/setup#{NOPECHA_KEY}", wait_until="domcontentloaded", timeout=15000)
                    time.sleep(3)
                    log("✅ NopeCHA 插件授权激活成功")
                except Exception as e:
                    log(f"NopeCHA 激活页面访问异常: {e}", "WARN")

            # 2. 打开 VPSFree 登录页
            log("打开 VPSFree 登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # 3. 输入账号密码
            log("填写登录凭证...")
            page.locator("input[name='username'], #inputEmail").first.fill(EMAIL)
            page.locator("input[name='password'], #inputPassword").first.fill(PASSWORD)
            time.sleep(1)

            # 4. 等待 NopeCHA 自动识别验证码（NopeCHA 会自动点开并自动消灭九宫格图片）
            log("等待 NopeCHA 自动破解 reCAPTCHA 验证码（最长等待 120 秒）...")
            solved = False
            for i in range(120):
                # 检查验证码是否通过
                solved = page.evaluate("""() => {
                    // 1. 检查 token
                    const tas = document.querySelectorAll('textarea[name="g-recaptcha-response"], #g-recaptcha-response');
                    for (const ta of tas) {
                        if (ta.value && ta.value.trim().length > 20) return true;
                    }
                    // 2. 检查勾选标记
                    const iframes = document.querySelectorAll('iframe[title*="reCAPTCHA"]');
                    for (const f of iframes) {
                        try {
                            if (f.contentDocument?.querySelector('.recaptcha-checkbox-checked, [aria-checked="true"]')) return true;
                        } catch(e) {}
                    }
                    return false;
                }""")

                if solved:
                    log(f"🎉 验证码成功破解！耗时 {i + 1} 秒 ✅")
                    break
                time.sleep(1)

            if not solved:
                log("⚠️ 验证码识别超时，保存截图准备强制点击提交...", "WARN")
                page.screenshot(path="login_failed.png")

            time.sleep(2)

            # 5. 点击登录按钮
            log("提交登录表单...")
            submit_btn = page.locator("button#login, button[type='submit'], input[type='submit']").first
            
            try:
                # 尝试普通点击或强制点击
                submit_btn.click(force=True, timeout=10000)
            except Exception as e:
                log(f"点击登录按钮异常，尝试键盘回车: {e}", "WARN")
                page.keyboard.press("Enter")

            time.sleep(5)

            # 6. 验证是否登录成功
            if "login" in page.url.lower():
                log(f"登录失败，仍在登录页: {page.url}", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log(f"登录成功 ✅，当前进入后台页面: {page.url}")
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
            log("未发现 Manage 按钮", "WARN")
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
            log("未找到续期按钮（可能已续期或未到期）", "WARN")
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
        for shot in ["login_failed.png", "renew_error.png", "no_manage_btn.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)

        sys.exit(1)


if __name__ == "__main__":
    main()
