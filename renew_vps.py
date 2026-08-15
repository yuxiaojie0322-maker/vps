"""
VPSFree.es 免费面板自动续期脚本 (深度适配 Instance Management 详情页)
- 登录 free.vpsfree.es (hCaptcha)
- 进入项目 Manage -> 点击 Manage VPS 进入实例详情
- 抓取 CPU/内存/磁盘占用、运行天数、到期时间、续期倒计时
- 自动检测并点击 "Renew for 7 days" 进行续期
- 发送全信息 Telegram 仪表盘截图与报告
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
            log("TG 仪表盘截图已成功发送 ✅")
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
            viewport={"width": 1440, "height": 900},
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

            # 6. 检查登录状态
            if "connexion" in page.url.lower() or "login" in page.url.lower():
                log(f"登录未成功，仍在登录页: {page.url}", "ERROR")
                page.screenshot(path="login_failed.png")
                return False

            log(f"🎉 登录成功！进入控制台主页: {page.url} ✅")
            time.sleep(3)
            return do_instance_manage_and_renew(page)

        except Exception as e:
            log(f"流程执行异常: {e}", "ERROR")
            try:
                page.screenshot(path="renew_error.png")
            except:
                pass
            return False
        finally:
            browser.close()


def do_instance_manage_and_renew(page):
    # 步骤 1: 点击项目列表中的 Manage 按钮
    log("正在进入项目服务列表...")
    try:
        manage_btn = page.locator("a:has-text('Manage'), button:has-text('Manage')").first
        if manage_btn.is_visible():
            manage_btn.click()
            time.sleep(4)
    except Exception as e:
        log(f"点击项目 Manage 异常: {e}", "WARN")

    # 步骤 2: 点击列表右侧的 "Manage VPS" 按钮进入实例详情页
    log("正在点击 'Manage VPS' 进入实例详情页...")
    try:
        manage_vps_btn = page.locator("a:has-text('Manage VPS'), button:has-text('Manage VPS')").first
        if manage_vps_btn.is_visible():
            manage_vps_btn.click()
            log("成功点击 Manage VPS 按钮 👆")
            time.sleep(4)
        else:
            log("未在当前页找到 Manage VPS 按钮，尝试查找链接", "WARN")
    except Exception as e:
        log(f"点击 Manage VPS 异常: {e}", "WARN")

    log(f"当前所在详情页网址: {page.url}")
    time.sleep(2)

    # 步骤 3: 从详情页提取关键运行状态数据
    body_text = page.locator("body").inner_text()

    # 1. 到期时间
    expires_str = "未获取到"
    m_exp = re.search(r"Expires:\s*([^\n\r]+)", body_text)
    if m_exp:
        expires_str = m_exp.group(1).strip()

    # 2. 开放续期倒计时
    renewal_countdown = "已开放"
    m_open = re.search(r"Renewal opens in\s*([^\n\r]+)", body_text)
    if m_open:
        renewal_countdown = f"Renewal opens in {m_open.group(1).strip()}"

    # 3. 运行时间
    uptime_str = "正常运行中"
    m_uptime = re.search(r"(Running since[^\n\r]+|Uptime[^\n\r]+)", body_text)
    if m_uptime:
        uptime_str = m_uptime.group(1).strip()

    # 4. 资源占用率
    cpu_str, mem_str, disk_str = "0.0%", "0.0%", "0.0%"
    m_cpu = re.search(r"([\d.]+%)\s*CPU", body_text, re.I)
    if m_cpu: cpu_str = m_cpu.group(1)
    m_mem = re.search(r"([\d.]+%)\s*MEMORY", body_text, re.I)
    if m_mem: mem_str = m_mem.group(1)
    m_disk = re.search(r"([\d.]+%)\s*DISK", body_text, re.I)
    if m_disk: disk_str = m_disk.group(1)

    log(f"📋 状态汇总: 到期={expires_str} | 倒计时={renewal_countdown} | 资源={cpu_str}/{mem_str}/{disk_str}")

    # 步骤 4: 尝试点击 "Renew for 7 days" 按钮进行续期
    action_result = "⏸ 暂未开放（仅到期前24小时内可点）"
    try:
        renew_btn = page.locator("button:has-text('Renew for 7 days'), a:has-text('Renew for 7 days'), button:has-text('Renew')").first
        
        # 判断按钮是否可见且未被禁用 (disabled)
        if renew_btn.is_visible():
            is_disabled = renew_btn.get_attribute("disabled") is not None
            if not is_disabled and "opens in" not in renewal_countdown:
                log("🎯 发现可用续期按钮，正在点击续期... 👆")
                renew_btn.click()
                time.sleep(3)

                # 检查确认弹窗
                try:
                    confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('确定'), button:has-text('Yes')").first
                    if confirm_btn.is_visible():
                        confirm_btn.click()
                        time.sleep(2)
                except:
                    pass

                action_result = "🎉 <b>成功完成 7 天续期！</b>"
                log("续期操作完成 ✅")
            else:
                log(f"续期按钮当前锁定中（{renewal_countdown}）", "INFO")
        else:
            log("未找到可用的续期按钮", "INFO")
    except Exception as e:
        log(f"续期点击处理异常: {e}", "WARN")

    time.sleep(2)
    # 保存实例管理页面截图
    instance_shot = "instance_dashboard.png"
    page.screenshot(path=instance_shot)

    # 步骤 5: 构建 Telegram 仪表盘通知
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    caption = (
        f"🖥 <b>VPSFree.es 实例运行与续期报告</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📧 <b>账号:</b> <code>{EMAIL}</code>\n"
        f"📊 <b>资源:</b> CPU: {cpu_str} | 内存: {mem_str} | 硬盘: {disk_str}\n"
        f"⏱ <b>运行:</b> {uptime_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>到期时间:</b> <code>{expires_str}</code>\n"
        f"🔄 <b>续期状态:</b> <code>{renewal_countdown}</code>\n"
        f"⚡ <b>执行结果:</b> {action_result}\n"
        f"⏰ <b>检测时间:</b> {now_str}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💡 <i>规则：仅在到期前最后24小时内开放续期按钮</i>"
    )

    send_tg_photo(instance_shot, caption)
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
        for shot in ["login_failed.png", "renew_error.png"]:
            if os.path.exists(shot):
                send_tg_photo(shot, caption)
                break
        else:
            send_tg_text(caption)
        sys.exit(1)


if __name__ == "__main__":
    main()
