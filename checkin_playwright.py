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
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
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
                    const response = await fetch('/api/user/self', {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                    });
                    return await response.json();
                }
            """)

            if result.get('success'):
                return result.get('data', {})
            return None

        except Exception as e:
            logger.debug(f"获取用户信息失败: {str(e)}")
            return None

    def process_account(self, account: Dict) -> bool:
        """
        处理单个账号的签到流程

        Args:
            account: 账号信息字典

        Returns:
            是否成功
        """
        username = account.get('username')
        password = account.get('password')

        if not username or not password:
            logger.error("❌ 账号配置错误: 缺少用户名或密码")
            return False

        logger.info(f"\n{'='*50}")
        logger.info(f"开始处理账号: {username}")
        logger.info(f"{'='*50}")

        try:
            # 启动浏览器
            self.start_browser()

            # 登录
            if not self.login(username, password):
                return False

            self.random_delay(2, 4)

            # 获取用户信息
            user_info = self.get_user_info()
            if user_info:
                logger.info(f"   用户ID: {user_info.get('id')}")
                logger.info(f"   当前额度: {user_info.get('quota', 0)}")

            # 签到
            result = self.checkin()

            # 签到后再次获取用户信息，查看额度变化
            if result:
                self.random_delay(1, 2)
                new_info = self.get_user_info()
                if new_info and user_info:
                    old_quota = user_info.get('quota', 0)
                    new_quota = new_info.get('quota', 0)
                    if new_quota > old_quota:
                        logger.info(f"   额度变化: {old_quota} → {new_quota} (+{new_quota - old_quota})")

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


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AnyRouter 自动签到脚本')
    parser.add_argument('-c', '--config', default='config/accounts.json',
                        help='配置文件路径 (默认: config/accounts.json)')
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
    global_proxy = settings.get('proxy', None)  # 全局代理

    logger.info(f"共加载 {len(valid_accounts)} 个有效账号")
    logger.info(f"账号间延迟: {min_delay}-{max_delay} 秒")
    logger.info(f"无头模式: {'是' if headless else '否'}")
    if global_proxy:
        logger.info(f"全局代理: {global_proxy}")
    logger.info("")

    # 处理每个账号
    success_count = 0
    fail_count = 0

    for i, account in enumerate(valid_accounts, 1):
        # 优先使用账号自己的代理，否则使用全局代理
        account_proxy = account.get('proxy', global_proxy)

        checker = AnyRouterCheckin(headless=headless, proxy=account_proxy)

        if checker.process_account(account):
            success_count += 1
        else:
            fail_count += 1

        # 账号之间随机延迟
        if i < len(valid_accounts):
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"\n⏳ 等待 {delay:.0f} 秒后处理下一个账号...\n")
            time.sleep(delay)

    # 统计结果
    logger.info("\n" + "="*60)
    logger.info("签到完成!")
    logger.info(f"总计: {len(valid_accounts)} 个账号")
    logger.info(f"成功: {success_count} 个")
    logger.info(f"失败: {fail_count} 个")
    logger.info("="*60)


if __name__ == "__main__":
    main()
