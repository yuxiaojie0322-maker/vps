# VPSFree.es 自动续期脚本

使用 Playwright 浏览器自动化，定时登录 [manager.vpsfree.es](https://manager.vpsfree.es) 自动续期 VPS。

## 📦 文件说明

| 文件 | 用途 |
|:---|:---|
| `renew_vps.py` | 续期主脚本（Playwright 自动化） |
| `.github/workflows/renew.yml` | GitHub Actions 定时任务配置 |

## 🚀 部署步骤

### 1. 创建 GitHub 仓库
- 新建一个 **私有仓库**（Private），避免暴露账号信息

### 2. 上传文件
```
你的仓库/
├── renew_vps.py
└── .github/workflows/renew.yml
```

### 3. 配置 Secrets（安全存敏感信息）
仓库 → Settings → Secrets and variables → Actions → New repository secret

| Secret 名称 | 值 |
|:---|:---|
| `VPS_EMAIL` | 你的登录邮箱（如 `yuxiaojie0322@gmail.com`） |
| `VPS_PASSWORD` | 你的登录密码 |
| `CAPTCHA_KEY` | 2captcha API Key（见下方说明） |

### 4. 获取 2captcha API Key（解决验证码）
该网站有 reCAPTCHA 验证码，需要 2captcha 自动识别：

1. 去 [2captcha.com](https://2captcha.com) 注册账号
2. 充值（约 $3 可识别 1000 次，够用很久）
3. 在仪表盘复制 API Key
4. 添加到 GitHub Secrets 的 `CAPTCHA_KEY`

### 5. 测试运行
- 仓库 → Actions → 找到 `VPSFree 自动续期` → 点 `Run workflow` 手动触发一次
- 观察日志，确认续期成功

## ⏰ 定时规则
默认 **每7天凌晨2点** 运行一次（`0 2 */7 * *`）
想改频率就编辑 `renew.yml` 里的 `cron` 表达式

## 🔧 如果续期失败
- 会自动截取页面截图，保存为 Artifact 供你查看
- 会自动创建 Issue 提醒你手动续期

## ⚠️ 注意
- 2captcha 需要付费（约 $3 起），但比手动续期省事多了
- 如果不想用付费服务，也可以在自己电脑上手动运行脚本（有头模式），手动点验证码