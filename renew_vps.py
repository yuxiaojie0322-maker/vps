"""
VPSFree.es 自动续期脚本
使用 NopeCHA API 自动通过 reCAPTCHA，续期后推送 TG 通知+截图
"""

import os
import sys
import json
import time
import pickle
import requests
from datetime import datetime
from pathlib import Path

# ========== 配置 ==========
EMAIL = os.environ.get("VPS_EMAIL", "")
PASSWORD = os.environ.get("VPS_PASSWORD", "")
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
MANAGER_URL = "https://manager.vpsfree.es"
COOKIE_FILE = "vpsfree_cookies.pkl"

# Telegram 推送配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")


# ========== 日志 ==========
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
        requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=15)
        log("TG 文本消息已发送 ✅")
        return True
    except Exception as e:
        log(f"TG 发送失败: {e}", "WARN")
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
            requests.post(url, files=files, data=data, timeout=30)
        log("TG 图片消息已发送 ✅")
        return True
    except Exception as e:
        log(f"TG 图片发送失败: {e}", "WARN")
        return send_tg_text(caption)


# ====================================================================
# NopeCHA API 解码 reCAPTCHA v2
# ====================================================================
def solve_recaptcha_nopecha(website_url, site_key):
    if not NOPECHA_KEY:
        log("未配置 NOPECHA_KEY，无法调用 API 解码", "ERROR")
        return None

    log("正在通过 NopeCHA API 提交人机验证任务...")
    post_url = "https://api.nopecha.com/"
    payload = {
        "key": NOPECHA_KEY,
        "type": "recaptcha2",
        "url": website_url,
        "sitekey": site_key
    }

    try:
        res = requests.post(post_url, json=payload, timeout=20).json()
        if "error" in res:
            log(f"NopeCHA 创建任务失败: {res.get('message') or res.get('error')}", "ERROR")
            return None

        task_id = res.get("data")
        log(f"NopeCHA 任务已提交，ID: {task_id}，等待解码...")

        # 轮询获取结果
        get_url = f"https://api.nopecha.com/?key={NOPECHA_KEY}&id={task_id}"
        for i in range(30):
            time.sleep(3)
            result = requests.get(get_url, timeout=15).json()
            if "data" in result and isinstance(result["data"], str):
                log("✅ NopeCHA 人机验证解码成功！")
                return result["data"]
            elif "error" in result and result.get("error") != "incomplete":
                log(f"NopeCHA 解码失败: {result.get('error')}", "ERROR")
                return None

        log("NopeCHA 人机验证超时", "ERROR")
        return None
    except Exception as e:
        log(f"请求 NopeCHA API 出错: {e}", "ERROR")
        return None


# ====================================================================
# 主流程
# ====================================================================
def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright && playwright install chromium", "ERROR")
        return False

    is_ci = "GITHUB_ACTIONS" in os.environ
    log(f"运行环境: {'GitHub Actions' if is_ci else '本地'}")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/playwright-data",
            headless=is_ci,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            bypass_csp=True,
            ignore_https_errors=True,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            # 打开登录页
            log("打开登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="networkidle", timeout=30000)
            time.sleep(3)

            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)
            log("已填写邮箱和密码")

            # 多重定位抓取 SiteKey
            site_key = page.evaluate("""() => {
                const selectors = ['.g-recaptcha', '[data-sitekey]', '#g-recaptcha'];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el && el.getAttribute('data-sitekey')) {
                        return el.getAttribute('data-sitekey');
                    }
                }
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const iframe of iframes) {
                    const src = iframe.getAttribute('src') || '';
                    if (src.includes('recaptcha')) {
                        const match = src.match(/k=([^&]+)/);
                        if (match) return match[1];
                    }
                }
                const html = document.body.innerHTML;
                const match = html.match(/data-sitekey=["']([^"']+)["']/);
                return match ? match[1] : null;
            }""")

            if site_key:
                log(f"检测到 reCAPTCHA，成功提取 SiteKey: {site_key}")
                token = solve_recaptcha_nopecha(page.url, site_key)
                if token:
                    page.evaluate(f"""(token) => {{
                        let el = document.getElementById('g-recaptcha-response');
                        if (!el) {{
                            el = document.createElement('textarea');
                            el.id = 'g-recaptcha-response';
                            el.name = 'g-recaptcha-response';
                            el.style.display = 'none';
                            document.forms[0].appendChild(el);
                        }}
                        el.innerHTML = token;
                        el.value = token;
                    }}""", token)
                    log("已注入 reCAPTCHA Token 响应 ✅")
                else:
                    log("未能获取到验证码 Token，尝试直接提交...", "WARN")
            else:
                log("未在页面上提取到 sitekey", "WARN")

            time.sleep(1)
            page.click("button[type='submit']")
            time.sleep(5)

            if "login" in page.url.lower():
                log("登录失败", "ERROR")
                page.screenshot(path="login_failed.png")
                return False
            log("登录成功 ✅")

            return do_renew(page, browser)

        except Exception as e:
            log(f"出错: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_renew(page, browser):
    log("=" * 40)
    log("开始续期流程")
    log("=" * 40)

    log("访问服务列表...")
    page.goto(f"{MANAGER_URL}/clientarea.php?action=products",
              wait_until="networkidle", timeout=30000)
    time.sleep(2)

    log("查找 Manage 按钮...")
    try:
        btn = page.locator("text=Manage").first
        if btn:
            btn.click()
            log("已点击 Manage ✅")
        else:
            log("未找到 Manage 按钮", "ERROR")
            page.screenshot(path="no_manage_btn.png")
            return False
    except Exception as e:
        log(f"点击 Manage 失败: {e}", "ERROR")
        return False
    time.sleep(3)

    log("查找 Renew For 7 days 按钮...")
    try:
        btn = page.locator("text=Renew For 7 days").first
        if btn:
            btn.click()
            log("已点击 Renew For 7 days ✅")
        else:
            btn = page.locator("text=Renew").first
            if btn:
                btn.click()
                log("已点击 Renew ✅")
            else:
                log("未找到续期按钮", "ERROR")
                page.screenshot(path="no_renew_btn.png")
                return False
    except:
        log("未找到续期按钮", "ERROR")
        page.screenshot(path="no_renew_btn.png")
        return False
    time.sleep(3)

    try:
        btn = page.locator("text=Confirm").first
        if btn:
            btn.click()
            log("已确认续期 ✅")
            time.sleep(2)
    except:
        pass

    log("🎉 续期完成！")
    page.screenshot(path="renew_success.png")
    return True


# ====================================================================
# 主函数
# ====================================================================
def main():
    log("=" * 40)
    log("VPSFree 自动续期脚本 (NopeCHA API 版)")
    log("=" * 40)

    if not EMAIL or not PASSWORD:
        log("错误：未设置环境变量 VPS_EMAIL 或 VPS_PASSWORD", "ERROR")
        sys.exit(1)

    log(f"账号: {EMAIL}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    success = renew_vps()

    if success:
        log("✅ 续期成功！")
        caption = (
            f"✅ <b>VPSFree 续期成功</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"🔁 下次续期: 7天后\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🖼 下方为续期后页面截图"
        )
        send_tg_photo("renew_success.png", caption)
        sys.exit(0)
    else:
        log("❌ 续期失败", "ERROR")
        caption = (
            f"❌ <b>VPSFree 续期失败</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"💡 请手动登录 https://manager.vpsfree.es 续期\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🖼 下方为失败时页面截图"
        )
        for shot in ["renew_error.png", "no_renew_btn.png", "no_manage_btn.png", "login_failed.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)
        sys.exit(1)


if __name__ == "__main__":
    main()
