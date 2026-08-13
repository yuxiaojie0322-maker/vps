"""
VPSFree.es 自动续期脚本
使用 CapSolver 扩展自动解 reCAPTCHA，续期后推送 TG 通知+截图
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
MANAGER_URL = "https://manager.vpsfree.es"
COOKIE_FILE = "vpsfree_cookies.pkl"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "capsolver", "unpacked")

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
# CapSolver 扩展 Patch (注入 API Key)
# ====================================================================
def patch_capsolver(capsolver_path, api_key):
    if not api_key:
        log("未提供 CapSolver Key", "ERROR")
        return False

    config_file = os.path.join(capsolver_path, "assets", "config.js")
    if not os.path.exists(config_file):
        log(f"CapSolver 配置文件未找到: {config_file}", "ERROR")
        return False

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            content = f.read()

        # 将 apiKey 修改为传入的变量
        import re
        new_content = re.sub(r'apiKey:\s*["\'].*?["\']', f'apiKey: "{api_key}"', content)
        
        # 确保自动解决开关打开
        new_content = re.sub(r'useCapsolver:\s*false', 'useCapsolver: true', new_content)

        with open(config_file, "w", encoding="utf-8") as f:
            f.write(new_content)

        log("✅ CapSolver Key 注入成功")
        return True
    except Exception as e:
        log(f"CapSolver Patch 失败: {e}", "ERROR")
        return False


# ====================================================================
# 主流程
# ====================================================================
def renew_vps():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("请先安装 Playwright: pip install playwright && playwright install chromium", "ERROR")
        return False

    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(
        os.path.join(EXT_PATH, "manifest.json")
    )

    capsolver_key = os.environ.get("CAPSOLVER_KEY", "").strip()
    if ext_ok and capsolver_key:
        patch_capsolver(EXT_PATH, capsolver_key)
    else:
        log("未检测到有效 CapSolver 插件或未配置 CAPSOLVER_KEY", "WARN")
        ext_ok = False

    with sync_playwright() as p:
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
        if ext_ok:
            launch_args.extend([
                f"--disable-extensions-except={EXT_PATH}",
                f"--load-extension={EXT_PATH}",
            ])

        is_ci = "GITHUB_ACTIONS" in os.environ
        log(f"运行环境: {'GitHub Actions' if is_ci else '本地'}" +
            (f" + CapSolver" if ext_ok else ""))

        browser = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/playwright-data",
            headless=is_ci,
            args=launch_args,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="en-US",
            bypass_csp=True,
            ignore_https_errors=True,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            # Cookie 恢复（仅本地）
            if os.path.exists(COOKIE_FILE) and not is_ci:
                log("找到保存的登录状态，尝试直接续期...")
                with open(COOKIE_FILE, "rb") as f:
                    cookies = pickle.load(f)
                browser.add_cookies(cookies)

                page.goto(f"{MANAGER_URL}/clientarea.php?action=products",
                          wait_until="networkidle", timeout=30000)
                time.sleep(2)

                if "login" not in page.url.lower():
                    log("Cookie 有效，无需重新登录 ✅")
                    return do_renew(page, browser)

                log("Cookie 已过期，需要重新登录")

            # 登录
            log("打开登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="networkidle", timeout=30000)
            time.sleep(2)

            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)
            log("已填写邮箱和密码")

            if ext_ok:
                log("等待 CapSolver 自动解验证码...")
                for i in range(40):
                    solved = page.evaluate("""() => {
                        const ta = document.getElementById('g-recaptcha-response');
                        return ta && ta.value && ta.value.length > 0;
                    }""")
                    if solved:
                        log(f"验证码已自动解除 ✅（{i+1}秒）")
                        break
                    time.sleep(1)
                else:
                    log("CapSolver 未能在40秒内解除验证码", "WARN")

            page.click("button[type='submit']")
            time.sleep(3)

            if "login" in page.url.lower():
                log("登录失败", "ERROR")
                page.screenshot(path="login_failed.png")
                return False
            log("登录成功 ✅")

            if not is_ci:
                cookies = browser.cookies()
                with open(COOKIE_FILE, "wb") as f:
                    pickle.dump(cookies, f)

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
    log("VPSFree 自动续期脚本")
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
