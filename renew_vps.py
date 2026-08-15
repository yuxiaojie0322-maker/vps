"""
VPSFree.es 免费面板自动续期脚本 (精准截图 + 极简图文推送版)
"""

import os
import re
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


def send_tg_photo(photo_path, caption=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("未配置 TG 推送，跳过", "WARN")
        return False
    if not os.path.exists(photo_path):
        log(f"截图文件不存在: {photo_path}", "WARN")
        return send_tg_text(caption)
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": TG_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            resp = requests.post(url, files=files, data=data, timeout=30)
        res_json = resp.json()
        if res_json.get("ok"):
            log("TG 截图消息已成功发送 ✅")
            return True
        else:
            log(f"TG 图片发送失败: {res_json}，改发纯文本...", "WARN")
            return send_tg_text(caption)
    except Exception as e:
        log(f"TG 发送异常: {e}", "ERROR")
        return send_tg_text(caption)


def send_tg_text(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
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
        log(f"TG 纯文本发送异常: {e}", "ERROR")
        return False


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
            viewport={"width": 1400, "height": 900},
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
                    log(f"NopeCHA 激活异常: {e}", "WARN")

            # 2. 打开登录页
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

            # 4. 等待 NopeCHA 自动识别 hCaptcha
            log("等待 NopeCHA 自动识别 hCaptcha 验证码...")
            for i in range(120):
                solved = page.evaluate("""() => {
                    const tas = document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
                    for (const ta of tas) {
                        if (ta.value && ta.value.trim().length > 20) return true;
                    }
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

            time.sleep(2)

            # 5. 点击 Sign In 登录
            log("点击 Sign In 按钮提交登录...")
            if not email_input.input_value():
                email_input.fill(EMAIL)
            if not pass_input.input_value():
                pass_input.fill(PASSWORD)

            submit_btn = page.locator("button:has-text('Sign In'), button[type='submit']").first
            try:
                submit_btn.click(force=True, timeout=10000)
            except Exception as e:
                page.keyboard.press("Enter")

            time.sleep(6)

            # 6. 检查登录状态
            if "connexion" in page.url.lower() or "login" in page.url.lower():
                log(f"登录未成功，仍在登录页: {page.url}", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log(f"🎉 登录成功！进入控制台主页: {page.url} ✅")
            time.sleep(3)
            return do_manage_and_renew(page)

        except Exception as e:
            log(f"流程执行异常: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_manage_and_renew(page):
    log("正在定位项目卡片中的 Manage 按钮...")

    # 1. 点击项目 Manage 进入服务管理详情
    try:
        manage_btn = page.locator("a:has-text('Manage'), button:has-text('Manage')").first
        if manage_btn.is_visible():
            manage_btn.click()
            log("已点击 Manage 按钮，进入服务管理详情页... 👆")
            time.sleep(4)
    except Exception as e:
        log(f"点击 Manage 异常: {e}", "WARN")

    time.sleep(2)
    body_text = page.locator("body").inner_text()

    # 2. 精准提取红框里的两行关键状态
    expires_str = "未获取到"
    m_exp = re.search(r"Expires:\s*([^\n\r]+)", body_text)
    if m_exp:
        expires_str = m_exp.group(1).strip()

    renewal_str = "未获取到"
    m_ren = re.search(r"Renewal opens in\s*([^\n\r]+)", body_text)
    if m_ren:
        renewal_str = f"Renewal opens in {m_ren.group(1).strip()}"
    elif "Renew 7 days" in body_text:
        renewal_str = "已开放续期"

    log(f"📋 提取状态 -> 到期时间: {expires_str} | 续期状态: {renewal_str}")

    # 3. 尝试点击 "Renew 7 days" 按钮
    action_result = "⏸ 暂未开放续期"
    try:
        renew_btn = page.get_by_text("Renew 7 days", exact=False).first
        if renew_btn.is_visible():
            log("找到 'Renew 7 days' 按钮，正在执行点击... 👆")
            renew_btn.click()
            time.sleep(3)

            # 确认弹窗
            try:
                confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('确定'), button:has-text('Yes')").first
                if confirm_btn.is_visible():
                    confirm_btn.click()
                    time.sleep(2)
            except:
                pass

            action_result = "✅ 成功点击 Renew 7 days 续期！"
            log("续期点击操作完成 ✅")
        else:
            log("当前未到续期开放时间（按钮不可用或未开放）", "INFO")
    except Exception as e:
        log(f"续期点击处理异常: {e}", "WARN")
        action_result = f"续期点击异常: {e}"

    time.sleep(2)
    # 保存服务管理详情截图
    final_shot = "renew_detail.png"
    page.screenshot(path=final_shot)

    # 4. 构建干净漂亮的 Telegram 图文推送
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    caption = (
        f"✅ <b>VPSFree.es 自动续期运行报告</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📧 <b>账号:</b> <code>{EMAIL}</code>\n"
        f"⏳ <b>到期时间:</b> <code>{expires_str}</code>\n"
        f"🔄 <b>续期状态:</b> <code>{renewal_str}</code>\n"
        f"⚡ <b>执行结果:</b> {action_result}\n"
        f"⏰ <b>检测时间:</b> {now_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🖼 <i>服务管理截图如下</i>"
    )

    send_tg_photo(final_shot, caption)
    return True


def main():
    log("=" * 40)
    log("VPSFree.es 自动续期运行开始")
    log("=" * 40)

    if not EMAIL or not PASSWORD:
        log("缺少 VPS_EMAIL 或 VPS_PASSWORD 环境变量！", "ERROR")
        sys.exit(1)

    log(f"正在处理账号: {EMAIL}")
    success = renew_vps()

    if not success:
        log("运行失败，发送失败通知...", "ERROR")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        caption = (
            f"❌ <b>VPSFree.es 续期脚本运行异常</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: <code>{EMAIL}</code>\n"
            f"⏰ 时间: {now_str}\n"
            f"💡 请查看截图排查"
        )
        for shot in ["login_failed.png", "renew_error.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)
        sys.exit(1)


if __name__ == "__main__":
    main()
