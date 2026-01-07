# AnyRouter 自动签到脚本

自动化签到脚本，使用 Playwright 无头浏览器模拟真实用户行为，支持多账号批量操作。

## 功能特点

- ✅ 使用 Playwright 无头浏览器，自动处理阿里云 CDN JavaScript 验证
- ✅ 支持多账号批量签到
- ✅ 支持分批执行（不同配置文件）
- ✅ 自动跳过无效/占位符账号
- ✅ 支持代理配置（全局和单账号）
- ✅ 随机延迟防检测（可配置）
- ✅ 完整的日志记录
- ✅ 支持 Linux Cron 定时任务

## 项目结构

```
anyrouter-checkin/
├── checkin_playwright.py          # 主程序 (Playwright 版本)
├── requirements.txt               # Python 依赖
├── README.md                      # 说明文档
├── config/
│   ├── accounts.example.json     # 配置示例
│   ├── accounts.json             # 默认配置文件
│   ├── batch1.json               # 批次1配置（可选）
│   └── batch2.json               # 批次2配置（可选）
├── logs/                          # 日志目录（自动创建）
│   ├── checkin_YYYYMMDD.log      # 每日日志
│   ├── batch1.log                # 批次1 cron 日志
│   └── batch2.log                # 批次2 cron 日志
└── screenshots/                   # 截图目录（调试用，自动创建）
```

## 快速开始

### 1. 环境准备

创建 conda 环境并安装依赖：

```bash
# 创建新的 conda 环境
mamba create -n anyrouter python=3.11 -y

# 激活环境
conda activate anyrouter

# 进入项目目录
cd /home/teng/anyrouter/anyrouter-checkin

# 安装 Python 依赖
pip install playwright

# 安装浏览器（Chromium）
playwright install chromium
```

### 2. 配置账号

复制配置模板并编辑：

```bash
cp config/accounts.example.json config/accounts.json
nano config/accounts.json  # 或使用其他编辑器
```

配置格式：

```json
{
  "settings": {
    "min_delay": 300,
    "max_delay": 600,
    "headless": true,
    "proxy": "http://127.0.0.1:7890"
  },
  "accounts": [
    {"username": "用户名1", "password": "密码1"},
    {"username": "用户名2", "password": "密码2"},
    {"username": "用户名3", "password": "密码3", "proxy": "http://other-proxy:port"}
  ]
}
```

**配置说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `min_delay` | 账号间最小延迟（秒） | 60 |
| `max_delay` | 账号间最大延迟（秒） | 180 |
| `headless` | 是否使用无头模式 | true |
| `proxy` | 全局代理地址 | null |

**代理格式支持：**
- `http://ip:port`
- `http://user:pass@ip:port`
- `socks5://ip:port`

**账号级代理：** 单个账号可以设置自己的 `proxy`，会覆盖全局设置。

### 3. 测试运行

```bash
# 激活环境
conda activate anyrouter

# 使用默认配置运行
python checkin_playwright.py

# 使用指定配置文件运行
python checkin_playwright.py -c config/batch1.json
```

**首次测试建议：**
1. 先用一个账号测试，确保登录和签到都正常
2. 可以设置 `"headless": false` 查看浏览器操作过程
3. 确认无误后再添加更多账号

### 4. 分批配置（可选）

如果账号较多（20-30个），建议分成多个批次执行：

**config/batch1.json** - 第一批账号（早上执行）
```json
{
  "settings": {
    "min_delay": 300,
    "max_delay": 600,
    "headless": true,
    "proxy": "http://127.0.0.1:7890"
  },
  "accounts": [
    {"username": "账号1", "password": "密码1"},
    {"username": "账号2", "password": "密码2"}
  ]
}
```

**config/batch2.json** - 第二批账号（晚上执行）
```json
{
  "settings": {
    "min_delay": 300,
    "max_delay": 600,
    "headless": true,
    "proxy": "http://127.0.0.1:7890"
  },
  "accounts": [
    {"username": "账号16", "password": "密码16"},
    {"username": "账号17", "password": "密码17"}
  ]
}
```

### 5. 设置定时任务

使用 Linux Cron 定时执行：

```bash
# 编辑 crontab
crontab -e
```

添加以下内容：

```bash
# AnyRouter 自动签到 - 第一批（每天早上9点）
0 9 * * * cd /home/teng/anyrouter/anyrouter-checkin && /home/teng/anaconda3/envs/anyrouter/bin/python checkin_playwright.py -c config/batch1.json >> logs/batch1.log 2>&1

# AnyRouter 自动签到 - 第二批（每天晚上8点）
0 20 * * * cd /home/teng/anyrouter/anyrouter-checkin && /home/teng/anaconda3/envs/anyrouter/bin/python checkin_playwright.py -c config/batch2.json >> logs/batch2.log 2>&1
```

**如果只有一个配置文件：**

```bash
# 每天早上9点执行
0 9 * * * cd /home/teng/anyrouter/anyrouter-checkin && /home/teng/anaconda3/envs/anyrouter/bin/python checkin_playwright.py >> logs/cron.log 2>&1
```

**验证 crontab 设置：**

```bash
crontab -l
```

### 6. 查看日志

```bash
# 查看今天的签到日志
cat logs/checkin_$(date +%Y%m%d).log

# 实时查看日志
tail -f logs/checkin_$(date +%Y%m%d).log

# 查看批次日志
tail -f logs/batch1.log
tail -f logs/batch2.log
```

## 常用命令速查

```bash
# 激活环境
conda activate anyrouter

# 运行签到（默认配置）
python checkin_playwright.py

# 运行签到（指定配置）
python checkin_playwright.py -c config/batch1.json

# 查看今日日志
cat logs/checkin_$(date +%Y%m%d).log

# 查看 crontab
crontab -l

# 编辑 crontab
crontab -e
```

## 输出示例

```
============================================================
AnyRouter 自动签到脚本 (Playwright 版本)
运行时间: 2026-01-07 16:30:00
配置文件: config/accounts.json
============================================================
⏭️  跳过 14 个无效账号: 账号2, 账号3, ...
共加载 1 个有效账号
账号间延迟: 300-600 秒
无头模式: 是
全局代理: http://127.0.0.1:7890

==================================================
开始处理账号: ghx
==================================================
2026-01-07 16:30:01 [INFO] 正在启动浏览器...
2026-01-07 16:30:02 [INFO] ✅ 浏览器启动成功 (视口: 1456x892)
2026-01-07 16:30:02 [INFO] 使用代理: http://127.0.0.1:7890
2026-01-07 16:30:05 [INFO] 正在登录账号: ghx
2026-01-07 16:30:10 [INFO] ✅ 登录成功: ghx
2026-01-07 16:30:12 [INFO]    用户ID: 104536
2026-01-07 16:30:12 [INFO]    当前额度: 1000000
2026-01-07 16:30:15 [INFO] 正在执行签到...
2026-01-07 16:30:16 [INFO] ✅ API 签到成功! 获得 500 额度
2026-01-07 16:30:17 [INFO]    额度变化: 1000000 → 1000500 (+500)
2026-01-07 16:30:17 [INFO] 浏览器已关闭

============================================================
签到完成!
总计: 1 个账号
成功: 1 个
失败: 0 个
============================================================
```

## 无效账号自动跳过

脚本会自动跳过以下类型的占位符账号：
- 用户名或密码为空
- 包含 "账号"、"密码"、"username"、"password"、"your_" 等占位符

这样你可以在配置文件中保留模板格式，只需要填入真实账号即可。

## 反检测机制

### 当前实现
- ✅ Playwright 无头浏览器执行 JavaScript
- ✅ 隐藏 webdriver 特征
- ✅ 随机视口大小
- ✅ 随机 User-Agent
- ✅ 模拟真实打字速度
- ✅ 账号间随机延迟（可配置 300-600 秒）
- ✅ 操作间随机延迟
- ✅ 支持代理配置

### 降低风险建议
- 账号分批执行，避免同一时间大量登录
- 适当增加账号间延迟时间
- 如有条件，使用代理分散请求

## 故障排查

### 登录失败
1. 检查用户名密码是否正确
2. 检查网络连接和代理设置
3. 查看 `screenshots/` 目录下的截图
4. 尝试设置 `"headless": false` 查看浏览器操作

### 签到失败
1. 检查是否已经签到过（会提示"今日已签到"）
2. 检查账号状态是否正常
3. 查看日志文件了解详细错误

### Cron 不执行
1. 确保使用完整的 Python 路径
2. 检查 crontab 语法是否正确
3. 查看 cron 日志: `grep CRON /var/log/syslog`
4. 确保 logs 目录存在

### 浏览器启动失败
1. 确保已安装 Chromium: `playwright install chromium`
2. 检查系统依赖是否完整

## 安全建议

1. **不要将配置文件提交到 Git**
   ```bash
   echo "config/accounts.json" >> .gitignore
   echo "config/batch*.json" >> .gitignore
   ```

2. **保护配置文件权限**
   ```bash
   chmod 600 config/*.json
   ```

3. **定期检查日志**，及时发现异常

4. **适度使用**，避免过于频繁的操作

## 免责声明

本项目仅供学习交流使用，请遵守网站服务条款。使用本脚本产生的任何后果由使用者自行承担。

## License

MIT License
