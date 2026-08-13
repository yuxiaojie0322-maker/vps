"""
VPSFree.es 自动续期脚本
使用 NopeCHA 扩展自动解 reCAPTCHA，支持 API Key 和试用模式
适用于 GitHub Actions 或本地运行
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
EMAIL = os.environ.get("VPS_EMAIL", "yuxiaojie0322@gmail.com")
PASSWORD = os.environ.get("VPS_PASSWORD", "YxJ223512@")
MANAGER_URL = "https://manager.vpsfree.es"
COOKIE_FILE = "vpsfree_cookies.pkl"
EXT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "scripts", "extensions", "nopecha", "unpacked")


# ========== 日志 ==========
def log(msg, level="INFO"):
    t = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{t}] [{level}] {msg}")


# ====================================================================
# NopeCHA 额度检查
# ====================================================================
def check_nopecha_availability():
    """
    检查 NopeCHA API Key 是否有额度，无 Key 则尝试试用模式
    返回: (api_key, error_msg)
    """
    raw_key = os.environ.get("NOPECHA_KEY", "").strip()
    keys = [k.strip() for k in raw_key.splitlines() if k.strip()] if raw_key else []

    session = requests.Session()

    # 逐个检查 Key
    for idx, key in enumerate(keys, start=1):
        try:
            resp = session.get(f"https://api.nopecha.com/v1/status?key={key}", timeout=15)
            data = resp.json()
            if "error" not in data and data.get("credit", 0) > 0:
                log(f"✅ 找到有效 Key #{idx}，额度: {data['credit']}")
                return key, None
            else:
                log(f"Key #{idx} 无额度或无效")
        except Exception as e:
            log(f"Key #{idx} 状态查询失败: {e}", "WARN")

    # 无有效 Key，尝试试用
    if keys:
        log("所有 Key 均无可用额度，回退到试用模式")

    try:
        resp = session.get("https://api.nopecha.com/v1/status", timeout=15)
        data = resp.json()
        if "error" not in data and data.get("credit", 0) > 0:
            log("✅ 当前 IP 具备试用资格，使用试用模式")
            return "", None
        else:
            msg = "当前 IP 不具备试用资格" if "error" in data else "试用额度已用尽"
            log(msg, "ERROR")
    except Exception as e:
        log(f"试用状态查询失败: {e}", "ERROR")

    if keys:
        return "", "已尝试全部 NopeCHA Key 但均无额度，且当前 IP 不符合试用资格"
    return "", "未配置 NopeCHA Key，且当前 IP 不具备试用资格"


# ====================================================================
# NopeCHA 扩展 Patch — 注入 API Key
# ====================================================================
def patch_nopecha(nopecha_path, api_key):
    """
    将 API Key 注入 NopeCHA 扩展的 background.js，
    使其启动时自动配置好，无需手动操作
    """
    if not api_key:
        log("使用试用模式，无需 Patch")
        return False

    bg = os.path.join(nopecha_path, "assets", "qrmm9f.js")
    if not os.path.exists(bg):
        bg = os.path.join(nopecha_path, "background.js")
    if not os.path.exists(bg):
        log(f"background.js 不存在: {bg}", "ERROR")
        return False

    try:
        with open(bg, encoding="utf-8") as f:
            content = f.read()

        if "NopeCHA-Inject" in content:
            log("NopeCHA 已注入过，跳过")
            return True

        inject = f"""// NopeCHA-Inject
(function(){{
    const s={{enabled:true,key:"{api_key}",auto_solve_hcaptcha:true,auto_solve_recaptcha:true}};
    function w(){{
        chrome.storage.local.set({{settings:s,key:"{api_key}"}});
        chrome.storage.sync&&chrome.storage.sync.set({{settings:s,key:"{api_key}"}});
    }}
    w();setTimeout(w,1000);setTimeout(w,3000);
}})();\n"""

        with open(bg, "w", encoding="utf-8") as f:
            f.write(inject + content)

        log("✅ NopeCHA Key 注入成功")
        return True
    except Exception as e:
        log(f"Patch 失败: {e}", "ERROR")
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

    # 检查扩展是否存在
    ext_ok = os.path.exists(EXT_PATH) and os.path.exists(
        os.path.join(EXT_PATH, "manifest.json")
    )

    # 检查 NopeCHA 额度
    nopecha_key = None
    if ext_ok:
        nopecha_key, err = check_nopecha_availability()
        if err:
            log(f"NopeCHA 不可用: {err}", "ERROR")
            log("将尝试无扩展模式运行（仅本地有头模式可用）", "WARN")
            ext_ok = False
        else:
            patch_nopecha(EXT_PATH, nopecha_key)

    with sync_playwright() as p:
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
        if ext_ok:
            launch_args.extend([
                f"--disable-extensions-except={EXT_PATH}",
                f"--load-extension={EXT_PATH}",
            ])

        is_ci = "GITHUB_ACTIONS" in os.environ
        log(f"运行环境: {'GitHub Actions' if is_ci else '本地'}" +
            (f" + NopeCHA" if ext_ok else ""))

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
            # ====== Cookie 恢复（仅本地） ======
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

            # ====== 登录 ======
            log("打开登录页...")
            page.goto(f"{MANAGER_URL}/login", wait_until="networkidle", timeout=30000)
            time.sleep(2)

            page.fill("input[name='username']", EMAIL)
            page.fill("input[name='password']", PASSWORD)
            log("已填写邮箱和密码")

            # 等待 NopeCHA 自动解验证码
            if ext_ok:
                log("等待 NopeCHA 自动解验证码...")
                for i in range(30):
                    solved = page.evaluate("""() => {
                        const ta = document.getElementById('g-recaptcha-response');
                        return ta && ta.value && ta.value.length > 0;
                    }""")
                    if solved:
                        log(f"验证码已自动解除 ✅（{i+1}秒）")
                        break
                    time.sleep(1)
                else:
                    log("NopeCHA 未能在30秒内解除验证码", "WARN")

            # 点击登录
            page.click("button[type='submit']")
            time.sleep(3)

            if "login" in page.url.lower():
                log("登录失败", "ERROR")
                page.screenshot(path="login_failed.png")
                return False
            log("登录成功 ✅")

            # 保存 Cookie（本地）
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

    # 访问服务列表
    log("访问服务列表...")
    page.goto(f"{MANAGER_URL}/clientarea.php?action=products",
              wait_until="networkidle", timeout=30000)
    time.sleep(2)

    # 点击 Manage
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

    # 点击 Renew For 7 days
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

    # 确认续期
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


def main():
    log("=" * 40)
    log("VPSFree 自动续期脚本")
    log("=" * 40)

    if not EMAIL or not PASSWORD:
        log("错误：未设置邮箱或密码", "ERROR")
        sys.exit(1)

    log(f"账号: {EMAIL}")

    success = renew_vps()
    if success:
        log("✅ 续期成功！")
        sys.exit(0)
    else:
        log("❌ 续期失败，请手动处理", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
