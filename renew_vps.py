"""
free.vpsfree.es VPS 自动续期脚本
使用 Playwright 浏览器自动化 + 2captcha 验证码识别

部署到 GitHub Actions 定时运行

使用方法：
1. Fork 这个仓库
2. 在 Settings → Secrets → Actions 添加以下 Secrets:
   - VPS_EMAIL:    登录邮箱
   - VPS_PASSWORD: 登录密码
   - CAPTCHA_KEY:  2captcha API Key (https://2captcha.com 注册获取)
3. 手动触发一次 Workflow，或等待定时触发
"""

import os
import sys
import json
import time
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

# ========== 配置 ==========
EMAIL = os.environ.get("VPS_EMAIL", "")
PASSWORD = os.environ.get("VPS_PASSWORD", "")
CAPTCHA_KEY = os.environ.get("CAPTCHA_KEY", "")
MANAGER_URL = "https://manager.vpsfree.es"
LOGIN_URL = f"{MANAGER_URL}/login"


# ========== 日志 ==========
def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}")


# ========== 2captcha 验证码识别 ==========
def solve_recaptcha_v2(site_key, page_url):
    """
    通过 2captcha API 解决 reCAPTCHA v2
    返回: 验证码 token 字符串，或 None
    """
    api_url = "https://2captcha.com/in.php"
    data = {
        "key": CAPTCHA_KEY,
        "method": "userrecaptcha",
        "googlekey": site_key,
        "pageurl": page_url,
        "json": 1
    }

    log("正在提交验证码到 2captcha...")
    resp = requests.post(api_url, data=data, timeout=30)
    result = resp.json()

    if result.get("status") != 1:
        log(f"提交验证码失败: {result.get('request', '未知错误')}", "ERROR")
        return None

    captcha_id = result["request"]
    log(f"验证码已提交, ID: {captcha_id}, 等待识别...")

    # 轮询结果
    result_url = "https://2captcha.com/res.php"
    for i in range(60):  # 最多等 60 秒
        time.sleep(3)
        poll = requests.get(result_url, params={
            "key": CAPTCHA_KEY,
            "action": "get",
            "id": captcha_id,
            "json": 1
        }, timeout=10)
        poll_result = poll.json()

        if poll_result.get("status") == 1:
            token = poll_result["request"]
            log("验证码识别成功 ✅")
            return token
        elif poll_result.get("request") == "CAPCHA_NOT_READY":
            continue
        else:
            log(f"验证码识别失败: {poll_result.get('request', '未知错误')}", "ERROR")
            return None

    log("验证码识别超时", "ERROR")
    return None


def renew_vps():
    """
    主流程：登录 → 找到服务 → 续期 7 天
    """
    if not EMAIL or not PASSWORD:
        log("错误：未设置 VPS_EMAIL 和 VPS_PASSWORD 环境变量", "ERROR")
        return False

    if not CAPTCHA_KEY:
        log("错误：未设置 CAPTCHA_KEY (2captcha API Key)", "ERROR")
        return False

    log(f"账号: {EMAIL}")
    log("启动浏览器...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = context.new_page()

        try:
            # ====== 第1步：打开登录页 ======
            log(f"正在打开登录页 {LOGIN_URL} ...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            time.sleep(2)

            # ====== 第2步：填写登录表单 ======
            log("填写登录信息...")
            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)

            # ====== 第3步：识别 reCAPTCHA ======
            log("检测 reCAPTCHA...")

            # 获取 site key
            site_key = page.evaluate("""() => {
                const gc = document.querySelector('.g-recaptcha');
                if (!gc) return null;
                const iframe = gc.querySelector('iframe');
                if (!iframe) return null;
                const match = iframe.src.match(/[&?]k=([^&]+)/);
                return match ? match[1] : null;
            }""")

            if not site_key:
                log("未检测到 reCAPTCHA，尝试直接登录...", "WARN")
            else:
                log(f"检测到 reCAPTCHA, site key: {site_key[:10]}...")
                captcha_token = solve_recaptcha_v2(site_key, LOGIN_URL)

                if not captcha_token:
                    log("验证码识别失败，无法登录", "ERROR")
                    return False

                # 注入验证码 token
                page.evaluate(f"""() => {{
                    const textarea = document.getElementById('g-recaptcha-response');
                    if (textarea) {{
                        textarea.style.display = 'block';
                        textarea.value = '{captcha_token}';
                        textarea.style.display = 'none';
                    }}
                    // 触发回调
                    if (typeof ___grecaptcha_cfg !== 'undefined') {{
                        const clients = ___grecaptcha_cfg.clients;
                        for (const id in clients) {{
                            const client = clients[id];
                            for (const key in client) {{
                                if (client[key] && typeof client[key].callback === 'function') {{
                                    client[key].callback('{captcha_token}');
                                }}
                            }}
                        }}
                    }}
                }}""")
                time.sleep(1)

            # ====== 第4步：点击登录 ======
            log("正在登录...")
            page.click("button[type='submit']")
            time.sleep(3)

            # 检查是否登录成功
            current_url = page.url
            if "login" in current_url.lower():
                log("登录失败，可能账号密码错误或验证码未通过", "ERROR")
                # 截图保存现场
                page.screenshot(path="/tmp/login_failed.png")
                return False
            log("登录成功 ✅")

            # ====== 第5步：找到服务列表 ======
            log("正在访问服务列表...")
            page.goto(f"{MANAGER_URL}/clientarea.php?action=products",
                       wait_until="networkidle", timeout=30000)
            time.sleep(2)

            # ====== 第6步：点击 "Manage VPS" 按钮 ======
            log("查找 'Manage VPS' 按钮...")
            try:
                # 尝试多种方式找到 Manage 按钮
                manage_btn = page.locator("text=/Manage/i").first
                if manage_btn:
                    manage_btn.click()
                    log("已点击 Manage 按钮 ✅")
                else:
                    log("未找到 Manage 按钮，尝试其他方式...", "WARN")
                    page.screenshot(path="/tmp/manage_not_found.png")
                    return False
            except Exception as e:
                log(f"点击 Manage 按钮失败: {e}", "ERROR")
                page.screenshot(path="/tmp/manage_error.png")
                return False

            time.sleep(3)

            # ====== 第7步：点击 "Renew For 7 days" 按钮 ======
            log("查找 'Renew For 7 days' 按钮...")
            try:
                renew_btn = page.locator("text=/Renew For 7 days/i").first
                if renew_btn:
                    renew_btn.click()
                    log("已点击 Renew For 7 days 按钮 ✅")
                else:
                    # 尝试包含 Renew 的按钮
                    renew_btn = page.locator("text=/Renew/i").first
                    if renew_btn:
                        renew_btn.click()
                        log("已点击 Renew 按钮 ✅")
                    else:
                        log("未找到续期按钮", "WARN")
                        page.screenshot(path="/tmp/renew_not_found.png")
                        return False
            except Exception as e:
                log(f"点击续期按钮失败: {e}", "ERROR")
                page.screenshot(path="/tmp/renew_error.png")
                return False

            time.sleep(3)

            # ====== 第8步：确认续期 ======
            # 有些页面续期后需要确认，检查是否有确认按钮
            try:
                confirm_btn = page.locator("text=/Confirm/i").first
                if confirm_btn:
                    confirm_btn.click()
                    log("已确认续期 ✅")
                    time.sleep(2)
            except:
                pass  # 可能不需要确认

            log("🎉 续期流程完成！")
            page.screenshot(path="/tmp/renew_success.png")
            return True

        except Exception as e:
            log(f"执行出错: {e}", "ERROR")
            try:
                page.screenshot(path="/tmp/renew_error.png")
            except:
                pass
            return False

        finally:
            browser.close()


# ========== 主函数 ==========
def main():
    log("=" * 40)
    log("VPSFree 自动续期脚本启动")
    log("=" * 40)

    import requests  # 用于 2captcha API 调用

    success = renew_vps()

    if success:
        log("✅ 续期成功！")
        sys.exit(0)
    else:
        log("❌ 续期失败，请检查日志或手动处理", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()