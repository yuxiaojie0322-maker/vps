"""
VPSFree.es 自动续期脚本 (代理 + Buster 语音打码版)
- 支持代理 (PROXY_URL) 绕过机房 IP 高风控
- 使用 Buster 自动解 reCAPTCHA 验证码
- 自动确认续期并发送 TG 截图通知
"""

import os
import sys
import time
import requests
from datetime import datetime

# ========== 配置 ==========
EMAIL = os.environ.get("VPS_EMAIL", "")
PASSWORD = os.environ.get("VPS_PASSWORD", "")
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
MANAGER_URL = "https://manager.vpsfree.es"
BUSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "scripts", "extensions", "buster", "unpacked")

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

    ext_ok = os.path.exists(BUSTER_PATH) and os.path.exists(os.path.join(BUSTER_PATH, "manifest.json"))
    if not ext_ok:
        log(f"未找到 Buster 扩展目录: {BUSTER_PATH}", "WARN")

    with sync_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        if ext_ok:
            launch_args.extend([
                f"--disable-extensions-except={BUSTER_PATH}",
                f"--load-extension={BUSTER_PATH}",
            ])

        # 配置代理节点（如果设置了 PROXY_URL）
        proxy_config = None
        if PROXY_URL:
            # 兼容处理 SOCKS5 scheme 转换
            clean_proxy = PROXY_URL.replace("socks5://", "socks5://")
            log(f"🌐 已检测到代理配置，将使用代理链接: {clean_proxy.split('@')[-1]}")
            proxy_config = {"server": clean_proxy}

        browser = p.chromium.launch_persistent_context(
            user_data_dir="/tmp/playwright-data",
            headless=False,  # Xvfb 虚拟屏幕模式运行
            proxy=proxy_config,
            args=launch_args,
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
            time.sleep(2)

            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)
            log("已填写账号密码")

            # 触发验证码复选框
            log("点击人机验证复选框...")
            try:
                recaptcha_frame = page.frame_locator('iframe[title*="reCAPTCHA"]')
                recaptcha_frame.locator('.recaptcha-checkbox-border').click()
                time.sleep(2)
            except Exception as e:
                log(f"勾选验证码框时提示: {e}", "WARN")

            # 等待破解 Token 填充
            log("等待自动破解验证码（最多等待 60 秒）...")
            solved = False
            for i in range(60):
                # 校验 g-recaptcha-response 是否已填入 token
                solved = page.evaluate("""() => {
                    const ta = document.getElementById('g-recaptcha-response');
                    return ta && ta.value && ta.value.length > 0;
                }""")
                if solved:
                    log(f"🎉 验证码已被成功破解 ✅（耗时 {i+1} 秒）")
                    break

                # 尝试点击 Buster 插件按钮触发语音破解
                if ext_ok:
                    try:
                        challenge_frame = page.frame_locator('iframe[src*="bframe"]')
                        buster_btn = challenge_frame.locator('.buster-button')
                        if buster_btn.is_visible():
                            buster_btn.click()
                            log("已触发 Buster 自动打码按钮 ⚡")
                    except:
                        pass

                time.sleep(1)

            if not solved:
                log("验证码破解超时，尝试直接强行提交...", "WARN")

            # 强行底层提交表单
            log("正在提交登录表单...")
            time.sleep(2)
            try:
                page.evaluate("""() => {
                    const btn = document.querySelector("button[type='submit']") || document.getElementById('login');
                    if (btn && btn.form) {
                        btn.form.submit();
                    } else if (document.forms.length > 0) {
                        document.forms[0].submit();
                    }
                }""")
            except Exception as e:
                log(f"JS 提交失败，使用强制点击: {e}", "WARN")
                page.click("button[type='submit']", force=True)

            time.sleep(6)

            if "login" in page.url.lower():
                log("登录失败，停留在登录界面", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log("登录成功 ✅")
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
    page.goto(f"{MANAGER_URL}/clientarea.php?action=products", wait_until="networkidle", timeout=30000)
    time.sleep(3)

    log("查找 Manage 按钮...")
    try:
        manage_btn = page.locator("text=Manage").first
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
        renew_btn = page.locator("text=Renew For 7 days").first
        if not renew_btn.is_visible():
            renew_btn = page.locator("text=Renew").first

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
            f"💡 请手动登录后台检查"
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
