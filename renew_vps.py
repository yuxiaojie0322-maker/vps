"""
VPSFree.es 免费面板自动续期脚本 (多账号批量续期版 - 登录无限重试直至成功)
- 支持单账号 (VPS_EMAIL/VPS_PASSWORD) 与 多账号 (VPS_ACCOUNTS)
- 登录错误自动重试，直至成功为止
- 多账号隔离会话独立执行
- 每个账号独立发送 TG 仪表盘截图与到期报告
"""

import os
import re
import sys
import time
import requests
from datetime import datetime

# ========== 配置 ==========
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
BASE_URL = "https://free.vpsfree.es"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "nopecha", "unpacked")

# 失败重试等待间隔（秒）
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "10"))

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


def get_accounts():
    """解析单账号或多账号列表"""
    accounts = []
    raw_multi = os.environ.get("VPS_ACCOUNTS", "").strip()
    
    if raw_multi:
        # 多账号模式：支持 ---- 分隔、冒号分隔、逗号分隔
        for line in raw_multi.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "----" in line:
                parts = line.split("----", 1)
            elif ":" in line:
                parts = line.split(":", 1)
            elif "," in line:
                parts = line.split(",", 1)
            else:
                parts = line.split(None, 1)

            if len(parts) == 2:
                accounts.append({"email": parts[0].strip(), "password": parts[1].strip()})
    
    # 兼容旧的单账号环境变量
    if not accounts:
        single_email = os.environ.get("VPS_EMAIL", "").strip()
        single_pwd = os.environ.get("VPS_PASSWORD", "").strip()
        if single_email and single_pwd:
            accounts.append({"email": single_email, "password": single_pwd})
            
    return accounts


def process_single_account(p, email, password, acc_index, total_accs):
    log(f"▶️ 开始处理账号 [{acc_index}/{total_accs}]: {email}")
    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(os.path.join(EXT_PATH, "manifest.json"))

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
            proxy_config = {"server": clean_proxy}

    attempt = 0
    # ========== 循环重试直到登录并处理成功 ==========
    for attempt in range(1, 6):  # 最多重试5次
        log(f"[{email}] === 第 {attempt} 次尝试 ===")
        attempt += 1
        log(f"[{email}] 🔄 [第 {attempt} 次尝试] 正在启动独立会话...")
        browser = None

        try:
            # 每个账号使用独立的临时数据目录，防止 Cookie 互相污染
            user_data_dir = f"/tmp/playwright-user-{acc_index}"
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
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
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            # 1. 激活 NopeCHA
            if ext_ok and NOPECHA_KEY:
                try:
                    page.goto(f"https://nopecha.com/setup#{NOPECHA_KEY}", wait_until="domcontentloaded", timeout=15000)
                    time.sleep(3)
                except Exception:
                    pass

            # 2. 打开登录页
            log(f"[{email}] [第 {attempt} 次] 打开登录页: {BASE_URL}/connexion ...")
            page.goto(f"{BASE_URL}/connexion", wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)

            # 3. 输入账号密码
            email_input = page.locator("input[type='email'], input[name='email'], input[name='username']").first
            pass_input = page.locator("input[type='password'], input[name='password']").first
            email_input.fill(email)
            pass_input.fill(password)
            time.sleep(1)

            # 4. 等待打码
            log(f"[{email}] [第 {attempt} 次] 等待 NopeCHA 自动识别 hCaptcha 验证码...")
            captcha_solved = False
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
                    captcha_solved = True
                    log(f"[{email}] 🎉 验证码破解成功（耗时 {i + 1} 秒）✅")
                    break
                time.sleep(1)

            if not captcha_solved:
                log(f"[{email}] ⚠️ 验证码识别超时(120s)，准备重新尝试...", "WARN")

            time.sleep(2)

            # 5. 提交登录
            if not email_input.input_value():
                email_input.fill(email)
            if not pass_input.input_value():
                pass_input.fill(password)

            submit_btn = page.locator("button:has-text('Sign In'), button[type='submit']").first
            try:
                submit_btn.click(force=True, timeout=10000)
            except Exception:
                page.keyboard.press("Enter")

            time.sleep(6)

            # 检查登录结果
            current_url = page.url.lower()
            if "connexion" in current_url or "login" in current_url:
                log(f"[{email}] ❌ [第 {attempt} 次] 登录失败，留在登录页。将在 {RETRY_DELAY} 秒后重新尝试...", "WARN")
                err_shot = f"login_failed_{acc_index}.png"
                try:
                    page.screenshot(path=err_shot)
                except Exception:
                    pass
                time.sleep(RETRY_DELAY)
                continue  # 重试

            log(f"[{email}] 🎉 登录成功！正在进入实例详情页...")
            time.sleep(3)

            # 6. 进入 Manage -> Manage VPS
            try:
                manage_btn = page.locator("a:has-text('Manage'), button:has-text('Manage')").first
                if manage_btn.is_visible():
                    manage_btn.click()
                    time.sleep(4)
            except Exception:
                pass

            try:
                manage_vps_btn = page.locator("a:has-text('Manage VPS'), button:has-text('Manage VPS')").first
                if manage_vps_btn.is_visible():
                    manage_vps_btn.click()
                    time.sleep(4)
            except Exception:
                pass

            # 7. 提取状态信息
            body_text = page.locator("body").inner_text()
            expires_str = "未获取到"
            m_exp = re.search(r"Expires:\s*([^\n\r]+)", body_text)
            if m_exp: expires_str = m_exp.group(1).strip()

            renewal_countdown = "已开放"
            m_open = re.search(r"Renewal opens in\s*([^\n\r]+)", body_text)
            if m_open: renewal_countdown = f"Renewal opens in {m_open.group(1).strip()}"

            uptime_str = "正常运行中"
            m_uptime = re.search(r"(Running since[^\n\r]+|Uptime[^\n\r]+)", body_text)
            if m_uptime: uptime_str = m_uptime.group(1).strip()

            cpu_str, mem_str, disk_str = "0.0%", "0.0%", "0.0%"
            m_cpu = re.search(r"([\d.]+%)\s*CPU", body_text, re.I)
            if m_cpu: cpu_str = m_cpu.group(1)
            m_mem = re.search(r"([\d.]+%)\s*MEMORY", body_text, re.I)
            if m_mem: mem_str = m_mem.group(1)
            m_disk = re.search(r"([\d.]+%)\s*DISK", body_text, re.I)
            if m_disk: disk_str = m_disk.group(1)

            # 8. 自动点击续期
            action_result = "⏸ 暂未开放（仅到期前24小时内可点）"
            try:
                renew_btn = page.locator("button:has-text('Renew for 7 days'), a:has-text('Renew for 7 days'), button:has-text('Renew')").first
                if renew_btn.is_visible():
                    is_disabled = renew_btn.get_attribute("disabled") is not None
                    if not is_disabled and "opens in" not in renewal_countdown:
                        renew_btn.click()
                        time.sleep(3)
                        try:
                            confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('确定'), button:has-text('Yes')").first
                            if confirm_btn.is_visible(): confirm_btn.click()
                        except Exception:
                            pass
                        action_result = "🎉 <b>成功完成 7 天续期！</b>"
                        log(f"[{email}] 续期完成 ✅")
            except Exception as e:
                action_result = f"点击异常: {e}"

            time.sleep(2)
            shot_path = f"instance_{acc_index}.png"
            page.screenshot(path=shot_path)

            # 9. 发送该账号的独立报告
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            caption = (
                f"🖥 <b>VPSFree.es 实例运行报告 [{acc_index}/{total_accs}]</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📧 <b>账号:</b> <code>{email}</code>\n"
                f"🔢 <b>尝试次数:</b> 共尝试 {attempt} 次后成功\n"
                f"📊 <b>资源:</b> CPU: {cpu_str} | 内存: {mem_str} | 硬盘: {disk_str}\n"
                f"⏱ <b>运行:</b> {uptime_str}\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⏳ <b>到期时间:</b> <code>{expires_str}</code>\n"
                f"🔄 <b>续期状态:</b> <code>{renewal_countdown}</code>\n"
                f"⚡ <b>执行结果:</b> {action_result}\n"
                f"⏰ <b>检测时间:</b> {now_str}\n"
            )
            send_tg_photo(shot_path, caption)
            log(f"[{email}] ✅ 账号处理成功完成！")
            return True  # 成功，跳出当前账号的无限重试循环

        except Exception as e:
            log(f"[{email}] ❌ [第 {attempt} 次] 处理流程异常: {e}，将在 {RETRY_DELAY} 秒后重试...", "ERROR")
            time.sleep(RETRY_DELAY)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


    log(f"[{email}] ❌ 5次尝试后仍失败，跳过此账号", "ERROR")
    return False

def main():
    log("=" * 40)
    log("VPSFree.es 自动续期运行开始")
    log("=" * 40)

    accounts = get_accounts()
    if not accounts:
        log("未找到任何账号配置！请设置 VPS_ACCOUNTS 或 VPS_EMAIL/VPS_PASSWORD 环境变量！", "ERROR")
        sys.exit(1)

    total = len(accounts)
    log(f"共检测到 {total} 个账号待处理...")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        for idx, acc in enumerate(accounts, start=1):
            process_single_account(p, acc["email"], acc["password"], idx, total)
            if idx < total:
                log("等待 5 秒后处理下一个账号...")
                time.sleep(5)

    log("🎉 所有账号处理完毕！")


if __name__ == "__main__":
    main()
