#!/usr/bin/env python3
"""
AnyRouter 自动签到脚本 (Playwright 版本)

使用无头浏览器完全模拟真实用户行为，自动处理：
- 阿里云 CDN JavaScript 验证
- Cookie 管理
- 登录和签到流程

用法：
    conda activate anyrouter
    python checkin_playwright.py                          # 使用默认配置
    python checkin_playwright.py -c config/batch1.json    # 指定配置文件
"""

import json
import time
import random
import logging
import argparse
import csv
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext

# 配置日志
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"checkin_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class AnyRouterCheckin:
    """AnyRouter 签到类 (Playwright 版本)"""

    def __init__(self, headless: bool = True, proxy: str = None):
        """
        初始化

        Args:
            headless: 是否使用无头模式（不显示浏览器窗口）
            proxy: 代理服务器地址，格式如：
                   - http://ip:port
                   - http://user:pass@ip:port
                   - socks5://ip:port
        """
        self.base_url = "https://anyrouter.top"
        self.headless = headless
        self.proxy = proxy
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def _parse_proxy(self) -> Optional[Dict]:
        """解析代理配置"""
        if not self.proxy:
            return None

        proxy_config = {"server": self.proxy}

        # 解析带认证的代理 http://user:pass@ip:port
        if "@" in self.proxy:
            # 提取认证信息
            protocol_end = self.proxy.find("://") + 3
            auth_end = self.proxy.rfind("@")
            auth_part = self.proxy[protocol_end:auth_end]

            if ":" in auth_part:
                username, password = auth_part.split(":", 1)
                proxy_config["username"] = username
                proxy_config["password"] = password

            # 重建服务器地址（不含认证）
            proxy_config["server"] = self.proxy[:protocol_end] + self.proxy[auth_end + 1:]

        return proxy_config

    def start_browser(self):
        """启动浏览器"""
        logger.info("正在启动浏览器...")

        self.playwright = sync_playwright().start()

        # 启动 Chromium，使用随机的视口大小模拟不同设备
        viewport_width = random.randint(1280, 1920)
        viewport_height = random.randint(720, 1080)

        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',  # 隐藏自动化特征
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        # 解析代理配置
        proxy_config = self._parse_proxy()

        # 创建浏览器上下文，模拟真实浏览器
        context_options = {
            'viewport': {'width': viewport_width, 'height': viewport_height},
            'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'locale': 'zh-CN',
            'timezone_id': 'Asia/Shanghai',
        }

        # 添加代理配置
        if proxy_config:
            context_options['proxy'] = proxy_config
            logger.info(f"使用代理: {proxy_config['server']}")

        self.context = self.browser.new_context(**context_options)
        self.page = self.context.new_page()

        # 隐藏 webdriver 特征
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        logger.info(f"✅ 浏览器启动成功 (视口: {viewport_width}x{viewport_height})")

    def close_browser(self):
        """关闭浏览器"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("浏览器已关闭")

    def random_delay(self, min_sec: float = 1, max_sec: float = 3):
        """随机延迟，模拟人类操作"""
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def save_screenshot(self, name: str = "debug"):
        """保存截图用于调试"""
        try:
            screenshot_dir = Path(__file__).parent / "screenshots"
            screenshot_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            path = screenshot_dir / f"{name}_{timestamp}.png"
            self.page.screenshot(path=str(path))
            logger.info(f"截图已保存: {path}")
        except Exception as e:
            logger.debug(f"保存截图失败: {str(e)}")

    def close_modal(self):
        """关闭可能出现的模态框/弹窗"""
        try:
            # 常见的关闭按钮选择器
            close_selectors = [
                '.semi-modal-close',
                '[aria-label="close"]',
                '[aria-label="Close"]',
                'button:has-text("关闭")',
                'button:has-text("Close")',
                'button:has-text("确定")',
                'button:has-text("OK")',
                'button:has-text("我知道了")',
                'button:has-text("知道了")',
                '.modal-close',
                '.close-btn',
                '[class*="close"]',
            ]

            for selector in close_selectors:
                try:
                    close_btn = self.page.locator(selector).first
                    if close_btn.is_visible(timeout=1000):
                        close_btn.click()
                        logger.info("已关闭弹窗")
                        self.random_delay(0.5, 1)
                        return True
                except:
                    continue

            # 尝试点击模态框外部来关闭
            try:
                modal_mask = self.page.locator('.semi-modal-mask, .modal-mask, .overlay').first
                if modal_mask.is_visible(timeout=1000):
                    # 点击页面左上角来关闭模态框
                    self.page.mouse.click(10, 10)
                    self.random_delay(0.5, 1)
                    return True
            except:
                pass

            # 尝试按 ESC 键关闭
            try:
                self.page.keyboard.press('Escape')
                self.random_delay(0.5, 1)
            except:
                pass

            return False

        except Exception as e:
            logger.debug(f"关闭弹窗时出错: {str(e)}")
            return False

    def login(self, username: str, password: str) -> bool:
        """
        登录账号

        Args:
            username: 用户名
            password: 密码

        Returns:
            登录是否成功
        """
        try:
            logger.info(f"正在登录账号: {username}")

            # 访问登录页面
            self.page.goto(f"{self.base_url}/login", wait_until="networkidle")
            self.random_delay(2, 4)

            # 尝试关闭任何可能的弹窗
            self.close_modal()
            self.random_delay(0.5, 1)

            # 等待登录表单加载
            self.page.wait_for_selector('input[name="username"], input[type="text"]', timeout=10000)

            # 查找并填写用户名
            username_input = self.page.locator('input[name="username"], input[placeholder*="用户名"], input[placeholder*="账号"]').first
            username_input.fill("")  # 先清空
            self.random_delay(0.3, 0.8)
            username_input.type(username, delay=random.randint(50, 150))  # 模拟打字速度

            self.random_delay(0.5, 1)

            # 查找并填写密码
            password_input = self.page.locator('input[name="password"], input[type="password"]').first
            password_input.fill("")  # 先清空
            self.random_delay(0.3, 0.8)
            password_input.type(password, delay=random.randint(50, 150))

            self.random_delay(1, 2)

            # 再次检查并关闭可能的弹窗
            self.close_modal()

            # 点击登录按钮 - 尝试多种选择器
            login_selectors = [
                'button[type="submit"]',
                'button:has-text("登录")',
                'button:has-text("Login")',
                'button:has-text("登 录")',
                '.login-btn',
                '[class*="login"] button',
            ]

            clicked = False
            for selector in login_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.is_visible(timeout=2000):
                        # 使用 force=True 强制点击，忽略遮挡检查
                        btn.click(force=True)
                        clicked = True
                        logger.debug(f"点击了登录按钮: {selector}")
                        break
                except Exception as e:
                    logger.debug(f"尝试点击 {selector} 失败: {str(e)}")
                    continue

            if not clicked:
                # 最后尝试：直接提交表单
                try:
                    self.page.keyboard.press('Enter')
                    clicked = True
                except:
                    pass

            if not clicked:
                logger.error(f"❌ 无法找到或点击登录按钮")
                return False

            # 等待登录完成（检查 URL 变化或元素出现）
            try:
                self.page.wait_for_url(f"{self.base_url}/console**", timeout=15000)
                logger.info(f"✅ 登录成功: {username}")
                return True
            except:
                # 保存截图用于调试
                self.save_screenshot("login_failed")

                # 检查是否有错误消息
                error_msg = self.page.locator('.error, .alert-error, [class*="error"]').first
                if error_msg.is_visible():
                    logger.error(f"❌ 登录失败: {username} - {error_msg.text_content()}")
                else:
                    logger.error(f"❌ 登录失败: {username} - 登录超时或未知错误")
                return False

        except Exception as e:
            self.save_screenshot("login_exception")
            logger.error(f"❌ 登录异常: {username} - {str(e)}")
            return False

    def checkin(self) -> bool:
        """
        执行签到

        Returns:
            签到是否成功
        """
        try:
            logger.info("正在执行签到...")

            # 确保在控制台页面
            if "/console" not in self.page.url:
                self.page.goto(f"{self.base_url}/console", wait_until="networkidle")
                self.random_delay(2, 4)

            # 查找签到按钮（尝试多种选择器）
            checkin_selectors = [
                'button:has-text("签到")',
                'button:has-text("Sign")',
                'button:has-text("Check")',
                '[class*="checkin"]',
                '[class*="sign"]',
            ]

            checkin_button = None
            for selector in checkin_selectors:
                try:
                    btn = self.page.locator(selector).first
                    if btn.is_visible():
                        checkin_button = btn
                        break
                except:
                    continue

            if not checkin_button:
                # 如果找不到签到按钮，尝试通过 API 直接签到
                logger.info("未找到签到按钮，尝试 API 签到...")
                return self.api_checkin()

            # 点击签到按钮
            checkin_button.click()
            self.random_delay(2, 4)

            # 检查签到结果
            # 尝试查找成功/失败消息
            success_indicators = [
                '签到成功',
                '已签到',
                'success',
                '获得',
            ]

            page_content = self.page.content().lower()
            for indicator in success_indicators:
                if indicator.lower() in page_content:
                    logger.info(f"✅ 签到成功!")
                    return True

            logger.info("ℹ️  签到完成（无法确认结果）")
            return True

        except Exception as e:
            logger.error(f"❌ 签到异常: {str(e)}")
            return False

    def api_checkin(self) -> bool:
        """
        通过 API 直接签到（在浏览器上下文中）

        Returns:
            签到是否成功
        """
        try:
            # 在浏览器中执行 API 请求
            result = self.page.evaluate("""
                async () => {
                    const response = await fetch('/api/user/sign_in', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                    });
                    return await response.json();
                }
            """)

            if result.get('success'):
                message = result.get('message', '')
                logger.info(f"✅ API 签到成功! {message}")
                return True
            else:
                message = result.get('message', '未知错误')
                if '已签到' in message or '已经签到' in message:
                    logger.info(f"ℹ️  今日已签到")
                    return True
                else:
                    logger.warning(f"⚠️  签到失败: {message}")
                    return False

        except Exception as e:
            logger.error(f"❌ API 签到异常: {str(e)}")
            return False

    def get_user_info(self) -> Optional[Dict]:
        """获取用户信息"""
        try:
            result = self.page.evaluate("""
                async () => {
                    // 从 localStorage 获取用户 ID
                    const userStr = localStorage.getItem('user');
                    const user = userStr ? JSON.parse(userStr) : null;
                    const userId = user ? user.id : '';

                    const response = await fetch('/api/user/self', {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                            'new-api-user': String(userId)
                        },
                    });
                    return await response.json();
                }
            """)

            if result.get('success'):
                return result.get('data', {})
            else:
                logger.warning(f"获取用户信息失败: {result.get('message', '未知错误')}")
            return None

        except Exception as e:
            logger.warning(f"获取用户信息异常: {str(e)}")
            return None

    def get_tokens(self) -> List[Dict]:
        """获取令牌列表"""
        try:
            result = self.page.evaluate("""
                async () => {
                    // 从 localStorage 获取用户 ID
                    const userStr = localStorage.getItem('user');
                    const user = userStr ? JSON.parse(userStr) : null;
                    const userId = user ? user.id : '';

                    const response = await fetch('/api/token/?p=0&size=100', {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                            'new-api-user': String(userId)
                        },
                    });
                    return await response.json();
                }
            """)

            if result.get('success'):
                return result.get('data', [])
            else:
                logger.warning(f"获取令牌列表失败: {result.get('message', '未知错误')}")
            return []

        except Exception as e:
            logger.warning(f"获取令牌列表异常: {str(e)}")
            return []

    def process_account(self, account: Dict) -> Dict:
        """
        处理单个账号的签到流程

        Args:
            account: 账号信息字典

        Returns:
            包含账号信息的字典，包括：
            - username: 用户名
            - success: 签到是否成功
            - user_id: 用户ID
            - quota: 账户余额
            - tokens: 令牌列表
        """
        username = account.get('username')
        password = account.get('password')

        result = {
            'username': username,
            'success': False,
            'user_id': None,
            'quota': 0,
            'tokens': []
        }

        if not username or not password:
            logger.error("❌ 账号配置错误: 缺少用户名或密码")
            return result

        logger.info(f"\n{'='*50}")
        logger.info(f"开始处理账号: {username}")
        logger.info(f"{'='*50}")

        try:
            # 启动浏览器
            self.start_browser()

            # 登录
            if not self.login(username, password):
                return result

            self.random_delay(2, 4)

            # 获取用户信息
            user_info = self.get_user_info()
            if user_info:
                result['user_id'] = user_info.get('id')
                result['quota'] = user_info.get('quota', 0)
                quota_usd = result['quota'] / 500000  # 转换为美元 (500000 = $1)
                logger.info(f"   用户ID: {result['user_id']}")
                logger.info(f"   账户余额: ${quota_usd:.2f}")

            # 获取令牌列表
            tokens = self.get_tokens()
            if tokens:
                result['tokens'] = tokens
                for token in tokens:
                    token_name = token.get('name', '未命名')
                    token_key = token.get('key', '')
                    token_quota = token.get('remain_quota', 0) / 500000  # 转换为美元
                    # 脱敏显示
                    masked_key = f"sk-{token_key[:4]}****{token_key[-4:]}" if len(token_key) > 8 else f"sk-{token_key}"
                    logger.info(f"   令牌: {token_name} (余额: ${token_quota:.2f}, 密钥: {masked_key})")

            # 签到
            checkin_success = self.checkin()

            # 签到后再次获取用户信息，查看额度变化
            if checkin_success:
                self.random_delay(1, 2)
                new_info = self.get_user_info()
                if new_info and user_info:
                    old_quota = user_info.get('quota', 0)
                    new_quota = new_info.get('quota', 0)
                    result['quota'] = new_quota  # 更新为最新余额
                    if new_quota > old_quota:
                        diff = (new_quota - old_quota) / 500000  # 转换为美元
                        logger.info(f"   签到奖励: +${diff:.2f}")

            result['success'] = checkin_success
            return result

        finally:
            # 确保浏览器被关闭
            self.close_browser()


def is_valid_account(account: Dict) -> bool:
    """
    检查账号是否有效

    跳过以下情况：
    - 用户名或密码为空
    - 用户名或密码是占位符（如 "账号1", "your_username" 等）
    """
    username = account.get('username', '').strip()
    password = account.get('password', '').strip()

    # 检查是否为空
    if not username or not password:
        return False

    # 常见的占位符关键词
    placeholders = [
        '账号', '密码', 'username', 'password', 'your_',
        'example', 'test', 'xxx', 'user', 'pass',
        '用户名', '你的'
    ]

    # 检查是否包含占位符
    username_lower = username.lower()
    password_lower = password.lower()

    for placeholder in placeholders:
        if placeholder in username_lower or placeholder in password_lower:
            return False

    return True


def load_config(config_file: str = "config/accounts.json") -> Dict:
    """加载配置文件"""
    config_path = Path(__file__).parent / config_file

    if not config_path.exists():
        logger.error(f"❌ 配置文件不存在: {config_path}")
        logger.info("请创建 config/accounts.json 文件并添加账号信息")
        return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ 读取配置文件失败: {str(e)}")
        return {}


def mask_token_key(key: str) -> str:
    """令牌密钥脱敏"""
    if len(key) > 8:
        return f"sk-{key[:4]}****{key[-4:]}"
    return f"sk-{key}"


def generate_reports(accounts_data: List[Dict], show_keys: bool = False):
    """
    生成账号汇总报告

    Args:
        accounts_data: 账号信息列表
        show_keys: 是否在CSV中显示完整密钥
    """
    if not accounts_data:
        return

    # 创建报告目录
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    date_str = datetime.now().strftime('%Y%m%d')

    # 1. 生成 JSON 文件（完整信息，方便程序调取）
    json_file = report_dir / f"tokens_{date_str}.json"
    tokens_data = []

    for account in accounts_data:
        username = account.get('username')
        for token in account.get('tokens', []):
            tokens_data.append({
                'username': username,
                'user_id': account.get('user_id'),
                'account_quota_raw': account.get('quota', 0),  # 原始值
                'account_quota_usd': account.get('quota', 0) / 500000,  # 美元
                'token_name': token.get('name', ''),
                'token_key': f"sk-{token.get('key', '')}",  # 完整密钥
                'token_quota_raw': token.get('remain_quota', 0),  # 原始值
                'token_quota_usd': token.get('remain_quota', 0) / 500000,  # 美元
                'used_quota_raw': token.get('used_quota', 0),
                'used_quota_usd': token.get('used_quota', 0) / 500000,
                'status': token.get('status', 0),
                'expired_time': token.get('expired_time', 0),
                'created_time': token.get('created_time', 0),
                'checkin_success': account.get('success', False)
            })

    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(tokens_data, f, ensure_ascii=False, indent=2)

    # 设置文件权限为 600（仅所有者可读写）
    os.chmod(json_file, 0o600)

    # 2. 生成 CSV 文件（方便查看）
    csv_file = report_dir / f"summary_{date_str}.csv"

    with open(csv_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['账号', '用户ID', '账户余额($)', '令牌名称', '令牌余额($)', '已用额度($)', '令牌密钥', '签到结果'])

        for account in accounts_data:
            username = account.get('username')
            user_id = account.get('user_id', '')
            account_quota = account.get('quota', 0) / 500000  # 转换为美元
            checkin_result = '成功' if account.get('success') else '失败'
            tokens = account.get('tokens', [])

            if tokens:
                for token in tokens:
                    token_name = token.get('name', '')
                    token_quota = token.get('remain_quota', 0) / 500000
                    used_quota = token.get('used_quota', 0) / 500000
                    token_key = token.get('key', '')

                    if show_keys:
                        display_key = f"sk-{token_key}"
                    else:
                        display_key = mask_token_key(token_key)

                    writer.writerow([username, user_id, f"{account_quota:.2f}", token_name,
                                   f"{token_quota:.2f}", f"{used_quota:.2f}", display_key, checkin_result])
            else:
                writer.writerow([username, user_id, f"{account_quota:.2f}", '', '', '', '', checkin_result])

    # 3. 按额度分类生成令牌文件
    keys_by_quota = {}  # {额度: [令牌列表]}

    for account in accounts_data:
        for token in account.get('tokens', []):
            token_key = token.get('key', '')
            if token_key:
                quota_usd = token.get('remain_quota', 0) / 500000
                # 四舍五入到整数美元作为分类键
                quota_key = int(round(quota_usd))
                if quota_key not in keys_by_quota:
                    keys_by_quota[quota_key] = []
                keys_by_quota[quota_key].append(f"sk-{token_key}")

    # 为每个额度生成单独的文件
    keys_dir = report_dir / "keys"
    keys_dir.mkdir(exist_ok=True)

    generated_files = []
    for quota, keys in sorted(keys_by_quota.items(), reverse=True):
        if keys:
            keys_file = keys_dir / f"keys_{quota}usd.txt"
            with open(keys_file, 'w', encoding='utf-8') as f:
                for key in keys:
                    f.write(f"{key}\n")
            os.chmod(keys_file, 0o600)
            generated_files.append((quota, len(keys), keys_file))

    # 同时生成一个汇总的所有令牌文件
    all_keys_file = report_dir / f"keys_{date_str}.txt"
    with open(all_keys_file, 'w', encoding='utf-8') as f:
        for quota in sorted(keys_by_quota.keys(), reverse=True):
            f.write(f"# === ${quota} ===\n")
            for key in keys_by_quota[quota]:
                f.write(f"{key}\n")
            f.write("\n")
    os.chmod(all_keys_file, 0o600)

    logger.info(f"\n📊 报告已生成:")
    logger.info(f"   汇总表格: {csv_file}")
    logger.info(f"   完整数据: {json_file}")
    logger.info(f"   所有令牌: {all_keys_file}")
    logger.info(f"   按额度分类:")
    for quota, count, filepath in generated_files:
        logger.info(f"      ${quota}: {count} 个令牌 → {filepath.name}")


def send_email_report(accounts_data: List[Dict], failed_accounts: List[str], email_config: Dict):
    """
    发送邮件报告（仅在有失败账号时发送）

    Args:
        accounts_data: 账号签到结果列表
        failed_accounts: 失败的账号用户名列表
        email_config: 邮件配置
    """
    if not email_config or not email_config.get('enabled'):
        return

    # 只有在有失败账号时才发送邮件
    if not failed_accounts:
        logger.info("📧 所有账号签到成功，跳过邮件通知")
        return

    try:
        smtp_server = email_config.get('smtp_server', 'smtp.qq.com')
        smtp_port = email_config.get('smtp_port', 465)
        sender = email_config.get('sender')
        password = email_config.get('password')
        receiver = email_config.get('receiver', sender)

        if not sender or not password:
            logger.warning("邮件配置不完整，跳过发送")
            return

        # 统计数据
        total = len(accounts_data)
        success = sum(1 for a in accounts_data if a.get('success'))
        failed = total - success
        total_quota = sum(a.get('quota', 0) for a in accounts_data) / 500000

        # 构建邮件内容
        date_str = datetime.now().strftime('%Y-%m-%d %H:%M')
        subject = f"AnyRouter 签到报告 - {success}/{total} 成功"
        if failed > 0:
            subject = f"⚠️ {subject}"

        # HTML 邮件内容
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .summary {{ background: #f5f5f5; padding: 15px; border-radius: 8px; margin-bottom: 20px; }}
                .success {{ color: #28a745; }}
                .failed {{ color: #dc3545; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
            </style>
        </head>
        <body>
            <h2>AnyRouter 签到报告</h2>
            <p>时间: {date_str}</p>

            <div class="summary">
                <h3>摘要</h3>
                <p>总账号数: <strong>{total}</strong></p>
                <p>签到成功: <strong class="success">{success}</strong></p>
                <p>签到失败: <strong class="failed">{failed}</strong></p>
                <p>总余额: <strong>${total_quota:.2f}</strong></p>
            </div>
        """

        if failed_accounts:
            html_content += f"""
            <div class="failed-section">
                <h3 class="failed">失败账号</h3>
                <p>{', '.join(failed_accounts)}</p>
            </div>
            """

        html_content += """
            <h3>详细结果</h3>
            <table>
                <tr>
                    <th>账号</th>
                    <th>状态</th>
                    <th>余额</th>
                </tr>
        """

        for account in accounts_data:
            status = "✅ 成功" if account.get('success') else "❌ 失败"
            status_class = "success" if account.get('success') else "failed"
            quota = account.get('quota', 0) / 500000
            html_content += f"""
                <tr>
                    <td>{account.get('username')}</td>
                    <td class="{status_class}">{status}</td>
                    <td>${quota:.2f}</td>
                </tr>
            """

        html_content += """
            </table>
        </body>
        </html>
        """

        # 创建邮件
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = receiver

        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

        # 发送邮件
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()

        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())
        server.quit()

        logger.info(f"📧 邮件报告已发送至: {receiver}")

    except Exception as e:
        logger.error(f"❌ 发送邮件失败: {str(e)}")


def run_checkin_batch(accounts: List[Dict], settings: Dict) -> List[Dict]:
    """
    运行一批账号的签到

    Args:
        accounts: 账号列表
        settings: 配置选项

    Returns:
        账号签到结果列表
    """
    min_delay = settings.get('min_delay', 60)
    max_delay = settings.get('max_delay', 180)
    headless = settings.get('headless', True)
    global_proxy = settings.get('proxy', None)

    accounts_data = []

    for i, account in enumerate(accounts, 1):
        account_proxy = account.get('proxy', global_proxy)
        checker = AnyRouterCheckin(headless=headless, proxy=account_proxy)

        result = checker.process_account(account)
        accounts_data.append(result)

        # 账号之间随机延迟
        if i < len(accounts):
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"\n⏳ 等待 {delay:.0f} 秒后处理下一个账号...\n")
            time.sleep(delay)

    return accounts_data


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AnyRouter 自动签到脚本')
    parser.add_argument('-c', '--config', default='config/accounts.json',
                        help='配置文件路径 (默认: config/accounts.json)')
    parser.add_argument('--show-keys', action='store_true',
                        help='在 CSV 报告中显示完整令牌密钥')
    args = parser.parse_args()

    logger.info("="*60)
    logger.info("AnyRouter 自动签到脚本 (Playwright 版本)")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"配置文件: {args.config}")
    logger.info("="*60)

    # 加载配置
    config = load_config(args.config)
    if not config:
        return

    accounts = config.get('accounts', [])
    if not accounts:
        logger.error("❌ 配置文件中没有账号信息")
        return

    # 过滤无效账号
    valid_accounts = []
    skipped_accounts = []

    for account in accounts:
        if is_valid_account(account):
            valid_accounts.append(account)
        else:
            skipped_accounts.append(account.get('username', '(空)'))

    if skipped_accounts:
        logger.info(f"⏭️  跳过 {len(skipped_accounts)} 个无效账号: {', '.join(skipped_accounts)}")

    if not valid_accounts:
        logger.error("❌ 没有有效的账号可以处理")
        logger.info("请检查配置文件，确保填入了真实的账号信息")
        return

    # 读取配置选项
    settings = config.get('settings', {})
    min_delay = settings.get('min_delay', 60)
    max_delay = settings.get('max_delay', 180)
    headless = settings.get('headless', True)
    global_proxy = settings.get('proxy', None)
    retry_delay_hours = settings.get('retry_delay_hours', 1)  # 重试等待时间（小时）
    max_retries = settings.get('max_retries', 2)  # 最大重试次数
    email_config = settings.get('email', {})  # 邮件配置

    logger.info(f"共加载 {len(valid_accounts)} 个有效账号")
    logger.info(f"账号间延迟: {min_delay}-{max_delay} 秒")
    logger.info(f"无头模式: {'是' if headless else '否'}")
    logger.info(f"失败重试: 最多 {max_retries} 次，间隔 {retry_delay_hours} 小时")
    if global_proxy:
        logger.info(f"全局代理: {global_proxy}")
    if email_config.get('enabled'):
        logger.info(f"邮件通知: 已启用 -> {email_config.get('receiver', email_config.get('sender'))}")
    logger.info("")

    # 第一轮签到
    all_accounts_data = {}  # 用用户名作为 key 存储结果
    accounts_to_process = valid_accounts.copy()

    for retry_round in range(max_retries + 1):
        if retry_round > 0:
            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 第 {retry_round} 次重试 ({len(accounts_to_process)} 个失败账号)")
            logger.info(f"{'='*60}\n")

        # 运行签到
        results = run_checkin_batch(accounts_to_process, settings)

        # 更新结果
        for result in results:
            username = result.get('username')
            all_accounts_data[username] = result

        # 检查失败账号
        failed_accounts = [a for a in accounts_to_process
                         if not all_accounts_data.get(a.get('username'), {}).get('success')]

        if not failed_accounts:
            logger.info("\n✅ 所有账号签到成功!")
            break

        # 如果还有重试次数，等待后重试
        if retry_round < max_retries:
            wait_seconds = retry_delay_hours * 3600
            logger.info(f"\n⏰ {len(failed_accounts)} 个账号失败，将在 {retry_delay_hours} 小时后重试...")
            logger.info(f"   失败账号: {', '.join(a.get('username') for a in failed_accounts)}")
            time.sleep(wait_seconds)
            accounts_to_process = failed_accounts
        else:
            logger.warning(f"\n⚠️  {len(failed_accounts)} 个账号最终失败")

    # 汇总结果
    final_results = list(all_accounts_data.values())
    success_count = sum(1 for r in final_results if r.get('success'))
    fail_count = len(final_results) - success_count
    failed_usernames = [r.get('username') for r in final_results if not r.get('success')]

    # 统计结果
    logger.info("\n" + "="*60)
    logger.info("签到完成!")
    logger.info(f"总计: {len(final_results)} 个账号")
    logger.info(f"成功: {success_count} 个")
    logger.info(f"失败: {fail_count} 个")
    if failed_usernames:
        logger.info(f"失败账号: {', '.join(failed_usernames)}")
    logger.info("="*60)

    # 生成报告
    generate_reports(final_results, show_keys=args.show_keys)

    # 发送邮件报告
    send_email_report(final_results, failed_usernames, email_config)


if __name__ == "__main__":
    main()
