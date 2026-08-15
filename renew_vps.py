"""
VPSFree.es 免费面板自动续期脚本 (精准适配 Manage 详情与 Renew 7 days)
- 登录 free.vpsfree.es (hCaptcha)
- 点击项目卡片 Manage 进入详情
- 抓取 Renewal 红框信息（到期时间与开放倒计时）
- 自动点击 "Renew 7 days" 续期按钮
- 发送 Telegram 详情图文推送
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
            log("TG 图文消息已成功发送 ✅")
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

            # 4. 等待 NopeCHA 自动识别 hCaptcha 验证码
            log("等待 NopeCHA 自动识别 hCaptcha 验证码...")
            solved = False
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

            # 5. 点击 Sign In
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

            # 6. 检查是否成功登录
            current_url = page.url.lower()
            if "connexion" in current_url or "login" in current_url:
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
    page.screenshot(path="dashboard_projets.png")

    # 1. 查找并点击项目卡片上的 Manage 按钮
    try:
        manage_btn = page.locator("a:has-text('Manage'), button:has-text('Manage')").first
        if manage_btn.is_visible():
            manage_btn.click()
            log("成功点击项目 Manage 按钮，正在进入服务管理页... 👆")
            time.sleep(5)
        else:
            log("未直接找到 Manage 按钮，尝试直接刷新当前页", "WARN")
    except Exception as e:
        log(f"点击项目 Manage 按钮异常: {e}", "WARN")

    log(f"当前所在页面: {page.url}")
    time.sleep(2)

    # 2. 提取红框中的续期及到期信息 (Renewal 信息)
    log("正在提取红框中的续期与到期状态...")
    info_data = page.evaluate("""() => {
        const result = {
            renewal_text: "未抓取到到期信息",
            details_text: "",
            service_name: ""
        };

        // 查找包含 Expires 的单元格
        const allCells = Array.from(document.querySelectorAll('td, div, p'));
        for (const el of allCells) {
            const txt = el.innerText || "";
            if (txt.includes("Expires:") && (txt.includes("Renewal") || txt.includes("open"))) {
                result.renewal_text = txt.trim();
                break;
            }
        }

        // 查找 IP 和系统详情
        for (const el of allCells) {
            const txt = el.innerText || "";
            if (txt.includes("IPv4") || txt.includes("IPv6") || txt.includes("Debian") || txt.includes("Ubuntu")) {
                result.details_text = txt.trim();
                break;
            }
        }

        // 查找服务名 (如 VPS#1003)
        for (const el of allCells) {
            const txt = el.innerText || "";
            if (txt.includes("VPS#")) {
                result.service_name = txt.trim();
                break;
            }
        }

        return result;
    }""")

    renewal_info = info_data.get("renewal_text", "无数据")
    details_info = info_data.get("details_text", "")
    service_name = info_data.get("service_name", "VPSFree 服务")

    log(f"📋 抓取到的红框状态:\n{renewal_info}")

    # 3. 查找并点击 "Renew 7 days" 按钮
    renew_action_result = "未触发续期（可能未到开放时间）"
    try:
        renew_btn = page.locator("a:has-text('Renew 7 days'), button:has-text('Renew 7 days'), text=/Renew 7 days/i").first
        if renew_btn.is_visible():
            log("找到 'Renew 7 days' 按钮，正在执行点击... 👆")
            renew_btn.click()
            time.sleep(3)

            # 检查是否有确认弹窗 (Confirm / Yes / 确定)
            try:
                confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('确定'), button:has-text('Yes'), a:has-text('Confirm')").first
                if confirm_btn.is_visible():
                    confirm_btn.click()
                    log("成功点击确认续期弹窗 ✅")
                    time.sleep(2)
            except:
                pass

            renew_action_result = "🎉 已成功点击 Renew 7 days 进行续期！"
            log("续期操作完成 ✅")
        else:
            log("⚠️ 当前未找到可点击的 'Renew 7 days' 按钮（可能尚未进入开放续期窗口）", "WARN")
            renew_action_result = "⏸ 暂未开放续期（请留意下方倒计时）"
    except Exception as e:
        log(f"点击续期按钮异常: {e}", "WARN")
        renew_action_result = f"续期点击异常: {e}"

    time.sleep(2)
    # 保存最终的服务管理页截图
    final_shot = "renew_detail_success.png"
    page.screenshot(path=final_shot)

    # 4. 构建并发送 Telegram 图文通知
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    caption = (
        f"✅ <b>VPSFree.es 自动续期运行报告</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📧 <b>账号:</b> <code>{EMAIL}</code>\n"
        f"🖥 <b>服务:</b> <code>{service_name}</code>\n"
    )
    if details_info:
        caption += f"🌐 <b>节点详情:</b>\n<code>{details_info}</code>\n"

    caption += (
        f"━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>续期与到期状态（红框信息）:</b>\n"
        f"<blockquote>{renewal_info}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>操作结果:</b> {renew_action_result}\n"
        f"⏰ <b>检测时间:</b> {now_str}\n"
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
            f"💡 请检查附件截图排查"
        )
        for shot in ["login_failed.png", "renew_error.png", "dashboard_projets.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)
        sys.exit(1)


if __name__ == "__main__":
    main()
