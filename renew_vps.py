"""
VPSFree.es 自动续期脚本
使用 NopeCHA 自动解验证码，续期后推送 TG 通知+截图
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
MANAGER_URL = "https://manager.vpsfree.es"

# Telegram 推送配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()


def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}")


# ====================================================================
# Telegram 推送 (带详细日志)
# ====================================================================
def send_tg_text(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过发送", "WARN")
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=15)
        res_json = resp.json()
        if res_json.get("ok"):
            log("TG 文本消息已成功发送 ✅")
            return True
        else:
            log(f"TG 文本发送被拒绝: {res_json}", "ERROR")
            return False
    except Exception as e:
        log(f"TG 文本发送网络异常: {e}", "ERROR")
        return False


def send_tg_photo(photo_path, caption=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过图片发送", "WARN")
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
            log("TG 图片消息已成功发送 ✅")
            return True
        else:
            log(f"TG 图片发送被拒绝: {res_json}，尝试退回发送纯文本...", "ERROR")
            return send_tg_text(caption)
    except Exception as e:
        log(f"TG 图片发送异常: {e}", "ERROR")
        return send_tg_text(caption)


# ====================================================================
# NopeCHA API 自动人机验证
# ====================================================================
def solve_recaptcha_nopecha(website_url, site_key):
    if not NOPECHA_KEY:
        log("未配置 NOPECHA_KEY", "ERROR")
        return None

    log("正在通过 NopeCHA API 提交人机验证任务...")
    try:
        res = requests.post("https://api.nopecha.com/", json={
            "key": NOPECHA_KEY,
            "type": "recaptcha2",
            "url": website_url,
            "sitekey": site_key
        }, timeout=20).json()

        if "error" in res:
            log(f"NopeCHA 任务创建失败: {res.get('message') or res.get('error')}", "ERROR")
            return None

        task_id = res.get("data")
        log(f"NopeCHA 任务 ID: {task_id}，等待解码...")

        for _ in range(30):
            time.sleep(3)
            result = requests.get(f"https://api.nopecha.com/?key={NOPECHA_KEY}&id={task_id}", timeout=15).json()
            if "data" in result and isinstance(result["data"], str):
                log("✅ NopeCHA 解码成功！")
                return result["data"]
            elif "error" in result and result.get("error") != "incomplete":
                log(f"NopeCHA 解码失败: {result.get('error')}", "ERROR")
                return None

        log("NopeCHA 解码超时", "ERROR")
        return None
    except Exception as e:
        log(f"NopeCHA API 请求异常: {e}", "ERROR")
        return None


# ====================================================================
# 主流程
# ====================================================================
def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright", "ERROR")
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
            log("打开登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="networkidle", timeout=30000)
            time.sleep(3)

            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)
            log("已填写账号密码")

            # 提取 SiteKey
            site_key = page.evaluate("""() => {
                const selectors = ['.g-recaptcha', '[data-sitekey]', '#g-recaptcha'];
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el && el.getAttribute('data-sitekey')) return el.getAttribute('data-sitekey');
                }
                const iframes = Array.from(document.querySelectorAll('iframe'));
                for (const iframe of iframes) {
                    const src = iframe.getAttribute('src') || '';
                    if (src.includes('recaptcha')) {
                        const m = src.match(/k=([^&]+)/);
                        if (m) return m[1];
                    }
                }
                const match = document.body.innerHTML.match(/data-sitekey=["']([^"']+)["']/);
                return match ? match[1] : null;
            }""")

            if site_key:
                log(f"提取到 SiteKey: {site_key}")
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
                    log("已注入验证码 Token ✅")

            page.click("button[type='submit']")
            time.sleep(5)

            if "login" in page.url.lower():
                log("登录失败，停留在登录界面", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log("登录成功 ✅")
            return do_renew(page)

        except Exception as e:
            log(f"执行中断异常: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_renew(page):
    log("访问服务列表...")
    page.goto(f"{MANAGER_URL}/clientarea.php?action=products", wait_until="networkidle", timeout=30000)
    time.sleep(3)

    log("查找 Manage 按钮...")
    try:
        manage_btn = page.locator("text=Manage").first
        if manage_btn.is_visible():
            manage_btn.click()
            log("点击 Manage 成功 ✅")
        else:
            log("未发现 Manage 按钮，保存当前页面截图", "WARN")
            page.screenshot(path="no_manage_btn.png")
            return False
    except Exception as e:
        log(f"点击 Manage 异常: {e}", "ERROR")
        page.screenshot(path="no_manage_btn.png")
        return False

    time.sleep(3)

    log("查找续期按钮...")
    try:
        renew_btn = page.locator("text=Renew For 7 days").first
        if not renew_btn.is_visible():
            renew_btn = page.locator("text=Renew").first

        if renew_btn.is_visible():
            renew_btn.click()
            log("点击续期按钮成功 ✅")
        else:
            log("未找到续期按钮（可能已续期或未到期）", "WARN")
            page.screenshot(path="no_renew_btn.png")
            # 即便按钮没找到，截个图发给 TG
            return True
    except Exception as e:
        log(f"点击续期按钮失败: {e}", "ERROR")
        page.screenshot(path="no_renew_btn.png")
        return False

    time.sleep(3)
    try:
        confirm_btn = page.locator("text=Confirm").first
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
        log("缺少必要的 VPS_EMAIL 或 VPS_PASSWORD 环境变量！", "ERROR")
        sys.exit(1)

    log(f"正在处理账号: {EMAIL}")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    success = renew_vps()

    if success:
        log("流程完成，尝试发送 TG 成功通知...")
        caption = (
            f"✅ <b>VPSFree 自动续期成功</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🖼 详细截图见下方"
        )
        # 优先发成功的截图，没有就寻找页面最后的截图
        shot_path = "renew_success.png"
        if not os.path.exists(shot_path):
            for path in ["no_renew_btn.png", "no_manage_btn.png"]:
                if os.path.exists(path):
                    shot_path = path
                    break

        send_tg_photo(shot_path, caption)
    else:
        log("流程失败，尝试发送 TG 失败通知...", "ERROR")
        caption = (
            f"❌ <b>VPSFree 续期失败</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📧 账号: {EMAIL}\n"
            f"⏰ 时间: {now}\n"
            f"💡 请登录后台查看"
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
