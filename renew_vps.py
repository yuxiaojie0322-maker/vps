"""
VPSFree.es 免费面板自动续期脚本 (优化增强版)
- 优化 Cloudflare challenge 检测：检测到表单就绪立即放行，彻底消除 30s 死等
- 修复 hCaptcha 误判：严禁匹配通用 .check 类名，防止 1 秒误判导致首次登录必败
- 优化表单提交：精准匹配 form 提交按钮，回车改用 page.keyboard.press 避免超 Detached 异常
- 优化 DOM 文本提取：使用 page.evaluate() 瞬时获取，杜绝 body.inner_text 15s 超时
- 优化续期按钮探测：单次聚合选择器 + disabled 判定，彻底消除 18s 逐项探测延迟
- 启用 NopeCHA API 兜底：插件打码超时时自动调用 API 求解
- 账号隔离与 Telegram 独立报告推送
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

# 强制 stdout / stderr 实时刷新
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# ========== 配置 ==========
NOPECHA_KEY = os.environ.get("NOPECHA_KEY", "").strip()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
BASE_URL = "https://free.vpsfree.es"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "nopecha", "unpacked")

# 失败重试等待间隔（秒）
RETRY_DELAY = int(os.environ.get("RETRY_DELAY", "5"))

# Telegram 推送配置
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()


def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}", flush=True)


def solve_hcaptcha_api(sitekey, pageurl):
    """NopeCHA HTTP API 解 hCaptcha（插件失效或超时时的兜底方案）"""
    if not NOPECHA_KEY or not sitekey:
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
        handlers = []
        if PROXY_URL:
            proxy_clean = PROXY_URL if PROXY_URL.startswith(("http://", "https://")) else "http://127.0.0.1:7890"
            handlers.append(urllib.request.ProxyHandler({"https": proxy_clean, "http": proxy_clean}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(
            "https://api.nopecha.com",
            data=payload, method="POST",
            headers={"Content-Type": "application/json"}
        )
        with opener.open(req, timeout=45) as r:
            result = json.loads(r.read())
            token = result.get("data")
            if token:
                log(f"[NopeCHA API 兜底] ✅ hCaptcha token 获取成功: {str(token)[:25]}...")
                return token
            log(f"[NopeCHA API 兜底] ❌ {result}", "WARN")
    except Exception as e:
        log(f"[NopeCHA API 兜底] 异常: {e}", "WARN")
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
    log(f"[{email}] NopeCHA 插件路径: {EXT_PATH}，可用={ext_ok}")

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
            log(f"[{email}] 代理协议不受 Chromium 原生支持 ({clean_proxy[:15]}...)，回退到本地 sing-box 代理 http://127.0.0.1:7890", "INFO")
            proxy_config = {"server": "http://127.0.0.1:7890"}

    # 单账号最多尝试 3 次（原 5 次过长，容易耗尽工作流 40 分钟上限）
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log(f"[{email}] === 第 {attempt}/{max_attempts} 次尝试 ===")
        browser = None

        try:
            user_data_dir = f"/tmp/playwright-user-{acc_index}-{attempt}"
            t_launch = time.time()
            browser = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=False,
                proxy=proxy_config,
                args=launch_args,
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1440, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-US",
                bypass_csp=True,
                ignore_https_errors=True,
            )
            log(f"[{email}] ✅ Chromium 启动完成 (耗时 {time.time()-t_launch:.1f}s)")

            page = browser.pages[0] if browser.pages else browser.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

            # 1. 激活 NopeCHA 插件
            if ext_ok and NOPECHA_KEY:
                try:
                    log(f"[{email}] 配置 NopeCHA API Key...")
                    page.goto(f"https://nopecha.com/setup#{NOPECHA_KEY}", wait_until="commit", timeout=15000)
                    time.sleep(2)
                except Exception as e:
                    log(f"[{email}] NopeCHA setup 提示（不影响主流程）: {e}", "WARN")

            # 2. 打开登录页（超时 90s）
            log(f"[{email}] 打开登录页: {BASE_URL}/connexion ...")
            try:
                page.goto(f"{BASE_URL}/connexion", wait_until="commit", timeout=90000)
                log(f"[{email}] ✅ 页面提交请求完成")
            except Exception as e:
                log(f"[{email}] ❌ 页面加载超时(90s): {e}", "WARN")

            # 2.5 快速检测 Cloudflare challenge / 表单就绪状态
            log(f"[{email}] 等待 Cloudflare 验证通过及登录表单就绪...")
            form_ready = False
            email_locator = page.locator("input[type='email'], input[name='email'], input[name='username']").first
            pass_locator = page.locator("input[type='password'], input[name='password']").first

            for w in range(1, 46):
                try:
                    cur_title = page.title()
                    if email_locator.is_visible():
                        log(f"[{email}] ✅ 登录表单已就绪（耗时 {w}s, Title='{cur_title}'）")
                        form_ready = True
                        break
                    
                    if "just a moment" in cur_title.lower() or "challenge" in cur_title.lower():
                        if w % 10 == 0:
                            log(f"[{email}] Cloudflare 挑战通过中... (已等待 {w}s)")
                        # 尝试点击 Turnstile 勾选框
                        try:
                            cf_frame = page.frame_locator("iframe[src*='challenges.cloudflare.com']").first
                            chk = cf_frame.locator("input[type='checkbox'], .ctp-checkbox-label").first
                            if chk.is_visible():
                                chk.click(timeout=1000)
                                log(f"[{email}] 已点击 Turnstile 验证框")
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(1)

            if not form_ready:
                cur_title = page.title()
                log(f"[{email}] ⚠️ 等待45s未检测到表单: URL={page.url}, Title='{cur_title}'", "WARN")

            # 3. 输入账号密码
            log(f"[{email}] 填写账号与密码...")
            try:
                email_locator.wait_for(state="visible", timeout=15000)
                email_locator.fill(email)
                pass_locator.fill(password)
                time.sleep(1)
            except Exception as e:
                snippet = ""
                try:
                    snippet = page.evaluate("() => document.body ? document.body.innerText.slice(0, 200) : ''").replace("\n", " ")
                except Exception:
                    pass
                log(f"[{email}] ❌ 输入框定位失败: {e} | URL={page.url}, Title='{page.title()}', 页面内容='{snippet}'", "WARN")
                page.screenshot(path=f"input_failed_{acc_index}.png")
                continue

            # 4. 等待打码完成（精准判断，杜绝 false-positive 误判）
            log(f"[{email}] 等待 hCaptcha 识别与校验...")
            captcha_solved = False
            for i in range(1, 91):
                try:
                    solved = page.evaluate("""() => {
                        // 1. 检查响应 textarea（最严谨的已解出标准，长度通常大于 30）
                        const tas = document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
                        for (const ta of tas) {
                            if (ta.value && ta.value.trim().length > 30) return true;
                        }
                        // 2. 检查 hcaptcha 对象
                        if (window.hcaptcha && typeof window.hcaptcha.getResponse === 'function') {
                            const resp = window.hcaptcha.getResponse();
                            if (resp && resp.length > 30) return true;
                        }
                        // 3. 检查 iframe 勾选属性（仅匹配明确的 aria-checked="true"，禁止匹配通配 .check）
                        const iframes = document.querySelectorAll('iframe[src*="hcaptcha"], iframe[title*="hcaptcha"]');
                        for (const f of iframes) {
                            try {
                                const doc = f.contentDocument || f.contentWindow?.document;
                                if (doc && doc.querySelector('[aria-checked="true"]')) return true;
                            } catch(e) {}
                        }
                        return false;
                    }""")
                    if solved:
                        captcha_solved = True
                        log(f"[{email}] 🎉 hCaptcha 验证码破解成功（耗时 {i} 秒）✅")
                        break
                except Exception:
                    pass

                # 若插件打码在 40 秒内未成功，尝试通过 NopeCHA API 兜底一次
                if i == 40 and not captcha_solved and NOPECHA_KEY:
                    log(f"[{email}] 插件打码耗时较长，触发 NopeCHA API 兜底请求...")
                    try:
                        sitekey = page.evaluate("""() => {
                            const el = document.querySelector('[data-sitekey]');
                            return el ? el.getAttribute('data-sitekey') : '';
                        }""")
                        if sitekey:
                            api_token = solve_hcaptcha_api(sitekey, page.url)
                            if api_token:
                                page.evaluate("""(tok) => {
                                    const tas = document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]');
                                    tas.forEach(t => { t.value = tok; });
                                }""", api_token)
                                captcha_solved = True
                                log(f"[{email}] 🎉 API 兜底填入 token 成功！")
                                break
                    except Exception as api_err:
                        log(f"[{email}] API 兜底异常: {api_err}", "WARN")

                time.sleep(1)

            if not captcha_solved:
                log(f"[{email}] ⚠️ 验证码识别超时(90s)，继续尝试提交...", "WARN")

            # 再次确认输入框值完整
            try:
                if not email_locator.input_value():
                    email_locator.fill(email)
                if not pass_locator.input_value():
                    pass_locator.fill(password)
            except Exception:
                pass

            # 5. 精准点击提交按钮（避免遍历 14 个 selector 浪费 30+ 秒）
            submit_clicked = False
            try:
                # 优先匹配所属 form 内的提交按钮
                form_btn = page.locator("form button[type='submit'], form input[type='submit'], form button.btn-primary").first
                if form_btn.is_visible(timeout=1000):
                    form_btn.click(timeout=3000)
                    log(f"[{email}] ✅ 点击表单提交按钮")
                    submit_clicked = True
            except Exception:
                pass

            if not submit_clicked:
                for sel in ["button:has-text('Sign In')", "button:has-text('Connexion')", "button:has-text('Login')", "button.btn-primary", "button[type='submit']"]:
                    try:
                        btn = page.locator(sel).first
                        if btn.is_visible(timeout=500):
                            btn.click(timeout=2000)
                            log(f"[{email}] ✅ 点击按钮: {sel}")
                            submit_clicked = True
                            break
                    except Exception:
                        continue

            if not submit_clicked:
                log(f"[{email}] 触发键盘回车提交登录...")
                page.keyboard.press("Enter")

            # 等待登录导航或结果（最多 12s）
            time.sleep(4)
            try:
                page.wait_for_url(lambda u: "connexion" not in u.lower() and "login" not in u.lower(), timeout=8000)
            except Exception:
                pass

            # 检查登录结果
            current_url = page.url.lower()
            if "connexion" in current_url or "login" in current_url:
                err_hint = ""
                try:
                    err_hint = page.evaluate("() => document.querySelector('.alert, .error, .toast, .text-danger')?.innerText || ''")
                except Exception:
                    pass
                log(f"[{email}] ❌ [第 {attempt} 次] 登录未成功，仍停留在登录页。错误提示: '{err_hint.strip()}'。将在 {RETRY_DELAY} 秒后重试...", "WARN")
                page.screenshot(path=f"login_failed_{acc_index}.png")
                time.sleep(RETRY_DELAY)
                continue

            log(f"[{email}] 🎉 登录成功！当前 URL: {page.url}")

            # 6. 进入实例详情页
            current_url = page.url.lower()
            if any(k in current_url for k in ["/instance", "/vps", "/serveur", "/vm", "/server"]):
                log(f"[{email}] ✅ 已在实例详情页")
            elif "/order" in current_url or "commande" in current_url:
                log(f"[{email}] ⚠️ 检测到 Order 页面，账号可能无实例或已达上限", "WARN")
                action_result = "⛔ 账号在 Order 页面（无实例或已达项目上限），跳过"
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                page.screenshot(path=f"instance_{acc_index}.png")
                caption = (
                    f"⚠️ <b>VPSFree.es 账号提示 [{acc_index}/{total_accs}]</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"📧 <b>账号:</b> <code>{email}</code>\n"
                    f"⚡ <b>状态:</b> {action_result}\n"
                    f"🔗 <b>页面:</b> <code>{current_url}</code>\n"
                    f"⏰ <b>检测时间:</b> {now_str}\n"
                )
                send_tg_photo(f"instance_{acc_index}.png", caption)
                browser.close()
                return True
            else:
                log(f"[{email}] 正在点击 Manage 进入实例详情...")
                try:
                    manage_btn = page.locator("a:has-text('Manage'):not([href*='order']):not([href*='new']):not([href*='create']), button:has-text('Manage'):not(:has-text('New'))").first
                    if manage_btn.is_visible(timeout=3000):
                        manage_btn.click(timeout=5000)
                        log(f"[{email}] 已点击 Manage")
                        time.sleep(3)
                except Exception as e:
                    log(f"[{email}] Manage 导航尝试: {e}", "WARN")

            # 7. 提取页面状态文本（使用 evaluate 瞬时提取，杜绝 inner_text 超时）
            time.sleep(2)
            try:
                body_text = page.evaluate("() => document.body ? document.body.innerText : ''")
                log(f"[{email}] 页面文本提取成功，长度: {len(body_text)} 字符")
            except Exception as e:
                log(f"[{email}] 获取 body 文本异常: {e}", "WARN")
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

            # 8. 探测并点击续期（单次聚合查询，耗时从 18s 降低至 1s）
            action_result = "⏸ 暂未开放（仅到期前24小时内可点）"
            try:
                detail_url = page.url.lower()
                if "/order" in detail_url or "commande" in detail_url:
                    log(f"[{email}] ⚠️ 当前在 Order 页面，跳过续期")
                else:
                    renew_btn = page.locator("button:has-text('Renew for 7 days'), a:has-text('Renew for 7 days'), button:has-text('Renew'):not(:has-text('New')):not(:has-text('Order')), button:has-text('Renouveler')").first
                    if renew_btn.is_visible(timeout=1000):
                        is_disabled = renew_btn.get_attribute("disabled")
                        btn_cls = renew_btn.get_attribute("class") or ""
                        if is_disabled is not None or "disabled" in btn_cls:
                            log(f"[{email}] 续期按钮存在但处于禁用状态（未到 24h 窗口）")
                            action_result = "⏸ 按钮存在但被禁用（未到 24h 续期窗口）"
                        else:
                            log(f"[{email}] 发现可点击续期按钮，正在执行续期...")
                            renew_btn.click(timeout=5000)
                            time.sleep(2)
                            confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('Confirmer'), button:has-text('Yes'), button:has-text('Valider')").first
                            if confirm_btn.is_visible(timeout=2000):
                                confirm_btn.click(timeout=3000)
                                log(f"[{email}] 点击确认按钮成功")
                            action_result = "🎉 <b>成功完成续期！</b>"
                            log(f"[{email}] 续期操作完成 ✅")
                    else:
                        log(f"[{email}] 未找到续期按钮（未到 24h 窗口期）")
                        action_result = "⏸ 未找到续期按钮（正常：未到 24h 窗口期）"
            except Exception as e:
                action_result = f"续期操作异常: {e}"
                log(f"[{email}] 续期操作异常: {e}", "WARN")

            time.sleep(2)
            shot_path = f"instance_{acc_index}.png"
            try:
                page.screenshot(path=shot_path)
            except Exception:
                pass

            # 9. 发送该账号的独立报告
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            caption = (
                f"🖥 <b>VPSFree.es 实例运行报告 [{acc_index}/{total_accs}]</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"📧 <b>账号:</b> <code>{email}</code>\n"
                f"🔢 <b>尝试次数:</b> 第 {attempt} 次成功\n"
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
            log(f"[{email}] ❌ [第 {attempt}/{max_attempts} 次] 流程异常: {e}，将在 {RETRY_DELAY} 秒后重试...", "ERROR")
            time.sleep(RETRY_DELAY)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    log(f"[{email}] ❌ 重试耗尽，跳过此账号", "ERROR")
    return False


def main():
    log("=" * 40)
    log("VPSFree.es 自动续期运行开始 (优化增强版)")
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
                log("等待 3 秒后处理下一个账号...")
                time.sleep(3)

    log("🎉 所有账号处理完毕！")
    summary = f"🖥 <b>VPSFree.es 续期巡检汇总</b>\n━━━━━━━━━━━━━━━━\n✅ 处理账号总数: {total}\n⏰ 完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    send_tg_text(summary)
    log("✅ 汇总已推送至 TG")


if __name__ == "__main__":
    main()
