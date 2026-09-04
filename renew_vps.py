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
import json
import urllib.request
import ssl
import requests
from datetime import datetime

# 强制 stdout flush，避免日志看不到
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# ========== 配置 ==========
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
# Playwright 仅支持 http/socks5。TUIC 节点需经本地 Sing-box/Clash 转发为本地 socks5 端口
PROXY_URL = os.environ.get("PROXY_URL", "socks5://127.0.0.1:10808").strip()
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
    print(f"[{t}] [{level}] {msg}", flush=True)


def solve_hcaptcha_api(sitekey, pageurl):
    """NopeCHA API 解 hCaptcha（插件失效时的兜底方案）"""
    if not NOPECHA_KEY:
        return None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        payload = json.dumps({
            "key": NOPECHA_KEY,
            "type": "hcaptcha",
            "data": {"sitekey": sitekey, "pageurl": pageurl}
        }).encode()
        proxy = urllib.request.ProxyHandler({"https": PROXY_URL, "http": PROXY_URL})
        opener = urllib.request.build_opener(proxy)
        req = urllib.request.Request(
            "https://api.nopecha.com",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"}
        )
        with opener.open(req, timeout=60) as r:
            result = json.loads(r.read())
            token = result.get("data")
            if token:
                log(f"[NopeCHA API] ✅ hCaptcha token: {str(token)[:25]}...")
                return token
            log(f"[NopeCHA API] ❌ {result}", "WARN")
    except Exception as e:
        log(f"[NopeCHA API] 异常: {e}", "WARN")
    return None


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

    if not accounts:
        single_email = os.environ.get("VPS_EMAIL", "").strip()
        single_pwd = os.environ.get("VPS_PASSWORD", "").strip()
        if single_email and single_pwd:
            accounts.append({"email": single_email, "password": single_pwd})

    return accounts


def process_single_account(p, email, password, acc_index, total_accs):
    log(f"▶️ 开始处理账号 [{acc_index}/{total_accs}]: {email}")
    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(os.path.join(EXT_PATH, "manifest.json"))
    log(f"[{email}] NopeCHA 插件路径: {EXT_PATH}，存在={ext_ok}")

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
        else:
            log(f"[{email}] 代理协议不受 Chromium 支持，请转为 socks5/http: {clean_proxy}", "WARN")

    # 最多重试5次
    for attempt in range(1, 6):
        log(f"[{email}] === 第 {attempt} 次尝试 ===")
        log(f"[{email}] 🔄 正在启动独立会话...")
        browser = None

        try:
            user_data_dir = f"/tmp/playwright-user-{acc_index}"
            t_launch = time.time()
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
            log(f"[{email}] ✅ Chromium 启动完成 (耗时 {time.time()-t_launch:.1f}s)")

            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            # 1. 激活 NopeCHA（插件模式，仅当插件加载成功时）
            if ext_ok and NOPECHA_KEY:
                try:
                    log(f"[{email}] 激活 NopeCHA 插件...")
                    page.goto(f"https://nopecha.com/setup#{NOPECHA_KEY}", wait_until="commit", timeout=30000)
                    time.sleep(3)
                except Exception as e:
                    log(f"[{email}] NopeCHA setup 失败（不影响主流程）: {e}", "WARN")

            # 1.5 代理连通性预检测
            log(f"[{email}] 预检测代理...")
            try:
                req = urllib.request.Request(BASE_URL, headers={"User-Agent": "Mozilla/5.0"})
                proxy = urllib.request.ProxyHandler({"https": PROXY_URL, "http": PROXY_URL})
                opener = urllib.request.build_opener(proxy)
                opener.open(req, timeout=15)
                log(f"[{email}] ✅ 代理可达")
            except Exception as e:
                log(f"[{email}] ⚠️ 代理预检: {e}", "WARN")

            # 2. 打开登录页（CF 经常要 40-60s，timeout 改 120s）
            log(f"[{email}] [第 {attempt} 次] 打开登录页: {BASE_URL}/connexion ...")
            try:
                page.goto(f"{BASE_URL}/connexion", wait_until="commit", timeout=120000)
                log(f"[{email}] ✅ 页面提交请求完成")
            except Exception as e:
                log(f"[{email}] ❌ 页面加载超时(120s): {e}", "WARN")
                try:
                    page.screenshot(path=f"goto_timeout_{acc_index}.png")
                except Exception:
                    pass
            time.sleep(5)

            # 2.5 等待 Cloudflare challenge 完成（最多 30s，够用即可）
            log(f"[{email}] [第 {attempt} 次] 等待 Cloudflare challenge 通过...")
            cf_passed = False
            for cf_wait in range(30):
                try:
                    page_content = page.content()
                    if "Just a moment" not in page_content and "cloudflare" not in page_content.lower():
                        log(f"[{email}] ✅ Cloudflare challenge 已通过（等待 {cf_wait}s）")
                        cf_passed = True
                        break
                    if "cdn-cgi" in page_content and "status" in page_content:
                        status_match = re.search(r'"status":"(\w+)"', page_content)
                        if status_match and status_match.group(1) == "ok":
                            log(f"[{email}] ✅ Cloudflare challenge 已通过")
                            cf_passed = True
                            break
                except Exception:
                    pass
                time.sleep(1)

            if not cf_passed:
                log(f"[{email}] ⚠️ Cloudflare challenge 等待超时(30s)，继续尝试...", "WARN")
                try:
                    page.screenshot(path=f"cf_challenge_{acc_index}.png")
                except Exception:
                    pass

            # 3. 输入账号密码
            log(f"[{email}] 填写账号密码...")
            try:
                email_input = page.locator("input[type='email'], input[name='email'], input[name='username']").first
                pass_input = page.locator("input[type='password'], input[name='password']").first
                email_input.fill(email)
                pass_input.fill(password)
                time.sleep(1)
            except Exception as e:
                log(f"[{email}] ❌ 找不到输入框: {e}", "WARN")
                continue

            # 4. 等待打码（120s）
            log(f"[{email}] [第 {attempt} 次] 等待 NopeCHA 自动识别 hCaptcha 验证码...")
            captcha_solved = False
            for i in range(120):
                try:
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
                except Exception:
                    pass
                time.sleep(1)

            if not captcha_solved:
                log(f"[{email}] ⚠️ 验证码识别超时(120s)，准备重新尝试...", "WARN")

            time.sleep(2)

            # 5. 重新确认账号密码
            try:
                if not email_input.input_value():
                    email_input.fill(email)
                if not pass_input.input_value():
                    pass_input.fill(password)
            except Exception:
                pass

            # 点击提交按钮
            submit_clicked = False
            for selector in [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Sign In')",
                "button:has-text('Sign in')",
                "button:has-text('Login')",
                "button:has-text('Log In')",
                "button:has-text('Se connecter')",
                "button:has-text('Connexion')",
                "button:has-text('Entrer')",
                "button:has-text('Valider')",
                "button:has-text('Submit')",
                "button.btn-primary",
                "button.btn",
                "form button",
            ]:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        btn.click(force=True, timeout=5000)
                        log(f"[{email}] 点击按钮: {selector}", "INFO")
                        submit_clicked = True
                        break
                except Exception:
                    continue

            if not submit_clicked:
                log(f"[{email}] 未找到提交按钮，按回车", "WARN")
                try:
                    pass_input.press("Enter", timeout=10000)
                except Exception:
                    log(f"[{email}] 按回车超时（页面状态异常），跳过此尝试", "WARN")

            time.sleep(6)

            # 检查登录结果
            current_url = page.url.lower()
            if "connexion" in current_url or "login" in current_url:
                log(f"[{email}] ❌ [第 {attempt} 次] 登录失败，留在登录页。将在 {RETRY_DELAY} 秒后重新尝试...", "WARN")
                try:
                    page.screenshot(path=f"login_failed_{acc_index}.png")
                except Exception:
                    pass
                time.sleep(RETRY_DELAY)
                continue

            log(f"[{email}] 🎉 登录成功！正在进入实例详情页...")
            time.sleep(3)

            # 6. 先检查当前页面——防止已在实例详情页或意外在 Order 页面
            current_url = page.url.lower()
            log(f"[{email}] 当前页面: {current_url}")
            # 如果已经在实例详情页（含有 instance/vps/serveur 等关键字），跳过导航
            if any(k in current_url for k in ["/instance", "/vps", "/serveur", "/vm", "/server"]):
                log(f"[{email}] ✅ 已在实例详情页，跳过 Manage 导航")
            elif "/order" in current_url or "commande" in current_url:
                # 在 Order 页面：说明账号无实例（已达1项目上限）
                log(f"[{email}] ⚠️ 检测到 Order 页面，账号可能已达项目上限，无法新建", "WARN")
                action_result = "⛔ 账号在 Order 页面（已达项目上限或无实例），跳过"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                page.screenshot(path=f"instance_{acc_index}.png")
                caption = (
                    f"⚠️ <b>VPSFree.es 账号异常 [{acc_index}/{total_accs}]</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📧 <b>账号:</b> <code>{email}</code>\n"
                    f"⚡ <b>状态:</b> {action_result}\n"
                    f"🔗 <b>页面:</b> <code>{current_url}</code>\n"
                    f"⏰ <b>检测时间:</b> {now_str}\n"
                )
                send_tg_photo(f"instance_{acc_index}.png", caption)
                browser.close()
                log(f"[{email}] 账号跳过完成（Order 页面）")
                return True
            else:
                # 需要导航到实例详情页
                log(f"[{email}] 正在点击 Manage 进入实例详情...")
                try:
                    # 优先找明确的实例列表/卡片（排除 Order）
                    for manage_selector in [
                        # 实例列表中的 Manage
                        "table a:has-text('Manage'):not([href*='order']):not([href*='new'])",
                        "a[href*='/instance/']:has-text('Manage')",
                        "a[href*='/vps/']:has-text('Manage')",
                        "a[href*='/vm/']:has-text('Manage')",
                        # 通用的 Manage（排除会跳到 Order 的）
                        "a:has-text('Manage'):not([href*='order']):not([href*='new']):not([href*='create'])",
                        "button:has-text('Manage'):not(:has-text('New')):not(:has-text('Order'))",
                    ]:
                        try:
                            btn = page.locator(manage_selector).first
                            if btn.is_visible(timeout=3000):
                                btn.click(timeout=5000)
                                log(f"[{email}] 点击: {manage_selector}")
                                time.sleep(3)
                                break
                        except Exception:
                            continue
                except Exception as e:
                    log(f"[{email}] Manage 导航失败: {e}", "WARN")

                # 检查是否到了 Manage VPS 子页面
                current_url = page.url.lower()
                if "manage" not in current_url and "/instance" not in current_url and "/vps" not in current_url:
                    try:
                        for sub_selector in [
                            "a:has-text('Manage VPS'):not([href*='order'])",
                            "a:has-text('Gérer le VPS')",
                            "button:has-text('Manage VPS')",
                        ]:
                            try:
                                btn = page.locator(sub_selector).first
                                if btn.is_visible(timeout=3000):
                                    btn.click(timeout=5000)
                                    log(f"[{email}] 点击子级: {sub_selector}")
                                    time.sleep(3)
                                    break
                            except Exception:
                                continue
                    except Exception as e:
                        log(f"[{email}] Manage VPS 导航失败: {e}", "WARN")

            # 7. 提取状态信息（先等页面稳定，再取文本）
            time.sleep(2)  # 等导航动画完成
            try:
                # 等待页面有实质内容
                page.wait_for_selector("body", state="attached", timeout=15000)
                body_text = page.locator("body").inner_text(timeout=15000)
                log(f"[{email}] 页面文本已获取，长度: {len(body_text)} 字符")
            except Exception as e:
                log(f"[{email}] body inner_text 超时: {e}，使用空字符串继续", "WARN")
                body_text = ""
            expires_str = "未获取到"
            m_exp = re.search(r"Expires:\s*([^\n\r]+)", body_text)
            if m_exp:
                expires_str = m_exp.group(1).strip()

            renewal_countdown = "已开放"
            m_open = re.search(r"Renewal opens in\s*([^\n\r]+)", body_text)
            if m_open:
                renewal_countdown = f"Renewal opens in {m_open.group(1).strip()}"

            uptime_str = "正常运行中"
            m_uptime = re.search(r"(Running since[^\n\r]+|Uptime[^\n\r]+)", body_text)
            if m_uptime:
                uptime_str = m_uptime.group(1).strip()

            cpu_str, mem_str, disk_str = "0.0%", "0.0%", "0.0%"
            m_cpu = re.search(r"([\d.]+%)\s*CPU", body_text, re.I)
            if m_cpu:
                cpu_str = m_cpu.group(1)
            m_mem = re.search(r"([\d.]+%)\s*MEMORY", body_text, re.I)
            if m_mem:
                mem_str = m_mem.group(1)
            m_disk = re.search(r"([\d.]+%)\s*DISK", body_text, re.I)
            if m_disk:
                disk_str = m_disk.group(1)

            # 8. 自动点击续期（更精准的 selector，排除 Order 页面元素）
            action_result = "⏸ 暂未开放（仅到期前24小时内可点）"
            try:
                # 先确认当前页是实例详情页
                detail_url = page.url.lower()
                if "/order" in detail_url or "commande" in detail_url:
                    log(f"[{email}] ⚠️ 当前在 Order 页面，跳过续期")
                else:
                    # 续期按钮精确 selector
                    for renew_selector in [
                        # 7天续期按钮
                        "button:has-text('Renew for 7 days')",
                        "a:has-text('Renew for 7 days')",
                        # 通用 Renew（排除 New/Order）
                        "button:has-text('Renew'):not(:has-text('New')):not(:has-text('Order'))",
                        "a:has-text('Renew'):not([href*='order']):not([href*='new'])",
                        # 法语
                        "button:has-text('Renouveler')",
                        "a:has-text('Renouveler')",
                    ]:
                        try:
                            renew_btn = page.locator(renew_selector).first
                            if renew_btn.is_visible(timeout=3000):
                                is_disabled = renew_btn.get_attribute("disabled")
                                if is_disabled is not None:
                                    log(f"[{email}] 续期按钮存在但被禁用（disabled），说明未到续期窗口")
                                    action_result = "⏸ 按钮存在但被禁用（未到续期窗口）"
                                    break
                                log(f"[{email}] 发现续期按钮: {renew_selector}")
                                renew_btn.click(timeout=10000)
                                time.sleep(3)
                                # 找确认按钮
                                for confirm_selector in [
                                    "button:has-text('Confirm')",
                                    "button:has-text('Confirmer')",
                                    "button:has-text('Yes')",
                                    "button:has-text('Valider')",
                                ]:
                                    try:
                                        confirm_btn = page.locator(confirm_selector).first
                                        if confirm_btn.is_visible(timeout=2000):
                                            confirm_btn.click(timeout=5000)
                                            log(f"[{email}] 点击确认按钮")
                                            break
                                    except Exception:
                                        continue
                                action_result = "🎉 <b>成功完成续期！</b>"
                                log(f"[{email}] 续期完成 ✅")
                                break
                        except Exception:
                            continue
                    else:
                        log(f"[{email}] 未找到续期按钮（可能未到 24h 窗口期）")
                        action_result = "⏸ 未找到续期按钮（正常：未到 24h 窗口期）"
            except Exception as e:
                action_result = f"续期操作异常: {e}"
                log(f"[{email}] 续期异常: {e}", "WARN")

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
            return True

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
    for i, acc in enumerate(accounts, 1):
        log(f"  {i}. {acc['email']}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        for idx, acc in enumerate(accounts, start=1):
            try:
                process_single_account(p, acc["email"], acc["password"], idx, total)
            except Exception as e:
                log(f"[{acc['email']}] 主流程异常: {e}", "ERROR")
            if idx < total:
                log("等待 5 秒后处理下一个账号...")
                time.sleep(5)

    log("🎉 所有账号处理完毕！")
    # 汇总报告
    summary = f"🖥 <b>VPSFree.es 续期汇总</b>\n━━━━━━━━━━━━━━━━\n处理账号数: {total}\n⏰ 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    send_tg_text(summary)
    log("✅ 汇总已推送至 TG")


if __name__ == "__main__":
    main()
