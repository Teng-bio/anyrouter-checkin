#!/usr/bin/env python3
"""
AnyRouter 自动签到脚本
支持多账号批量签到，带随机延迟防检测

改进版本：
- 模拟真实浏览器行为（new-api-user 头）
- User-Agent 轮换
- 更完善的反检测机制
- 可选代理支持
"""

import requests
import json
import time
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

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

# User-Agent 池，模拟不同浏览器
USER_AGENTS = [
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
]


class AnyRouterCheckin:
    """AnyRouter 签到类"""

    def __init__(self, base_url: str = "https://anyrouter.top", proxy: str = None):
        self.base_url = base_url
        self.user_id = -1  # 初始用户ID，模拟未登录状态
        self.session = requests.Session()

        # 如果配置了代理
        if proxy:
            self.session.proxies = {
                'http': proxy,
                'https': proxy
            }
            logger.info(f"使用代理: {proxy}")

        # 随机选择 User-Agent
        user_agent = random.choice(USER_AGENTS)

        self.session.headers.update({
            'User-Agent': user_agent,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Content-Type': 'application/json',
            'Cache-Control': 'no-store',
            'Origin': self.base_url,
            'Referer': f'{self.base_url}/login',
            'new-api-user': str(self.user_id),  # 关键：模拟未登录状态
        })

    def warmup(self) -> bool:
        """
        预热：访问登录页面获取 CDN 安全 Cookie

        这一步模拟真实浏览器行为：
        1. 先访问网页
        2. CDN 返回安全 Cookie（acw_tc, cdn_sec_tc, acw_sc__v2）
        3. 后续 API 请求带上这些 Cookie

        Returns:
            是否成功
        """
        try:
            logger.info("正在预热（获取 CDN Cookie）...")

            # 临时修改 Accept 头，模拟访问网页
            original_accept = self.session.headers.get('Accept')
            self.session.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            })

            # 访问登录页面
            response = self.session.get(f"{self.base_url}/login", timeout=30)

            # 恢复 Accept 头
            self.session.headers.update({
                'Accept': original_accept
            })

            if response.status_code == 200:
                # 检查是否获取到 CDN Cookie
                cookies = self.session.cookies.get_dict()
                cdn_cookies = [k for k in cookies.keys() if k.startswith(('acw_', 'cdn_'))]

                if cdn_cookies:
                    logger.info(f"✅ 预热成功，获取到 CDN Cookie: {cdn_cookies}")
                else:
                    logger.info("✅ 预热成功（无 CDN Cookie，可能不需要）")

                return True
            else:
                logger.warning(f"⚠️ 预热请求返回: HTTP {response.status_code}")
                return True  # 即使失败也继续尝试

        except Exception as e:
            logger.warning(f"⚠️ 预热异常: {str(e)}，继续尝试登录...")
            return True  # 即使异常也继续尝试

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
            url = f"{self.base_url}/api/user/login?turnstile="
            data = {
                "username": username,
                "password": password
            }

            logger.info(f"正在登录账号: {username}")
            response = self.session.post(url, json=data, timeout=30)

            # 调试：打印响应信息
            logger.debug(f"响应状态码: {response.status_code}")
            logger.debug(f"响应头: {dict(response.headers)}")
            logger.debug(f"响应内容前500字符: {response.text[:500] if response.text else '空'}")

            if response.status_code == 200:
                # 检查是否是 JSON 响应
                content_type = response.headers.get('Content-Type', '')
                if 'application/json' not in content_type:
                    logger.error(f"❌ 登录失败: {username} - 服务器返回非 JSON 响应")
                    logger.error(f"   Content-Type: {content_type}")
                    logger.error(f"   响应内容: {response.text[:200]}...")
                    return False

                result = response.json()
                if result.get('success'):
                    user_data = result.get('data', {})
                    self.user_id = user_data.get('id', -1)

                    # 关键：登录成功后更新 new-api-user 头，模拟真实浏览器行为
                    self.session.headers.update({
                        'new-api-user': str(self.user_id)
                    })

                    logger.info(f"✅ 登录成功: {username} (ID: {self.user_id})")
                    logger.info(f"   当前额度: {user_data.get('quota', 0)}")
                    return True
                else:
                    logger.error(f"❌ 登录失败: {username} - {result.get('message', '未知错误')}")
                    return False
            else:
                logger.error(f"❌ 登录失败: {username} - HTTP {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"❌ 登录异常: {username} - {str(e)}")
            return False

    def checkin(self) -> Optional[Dict]:
        """
        执行签到

        Returns:
            签到结果
        """
        try:
            url = f"{self.base_url}/api/user/sign_in"

            # 更新 Referer 为控制台页面
            self.session.headers.update({
                'Referer': f'{self.base_url}/console'
            })

            logger.info("正在执行签到...")
            response = self.session.post(url, timeout=30)

            if response.status_code == 200:
                result = response.json()
                if result.get('success'):
                    logger.info(f"✅ 签到成功! {result.get('message', '')}")
                    return result
                else:
                    message = result.get('message', '未知错误')
                    if '已签到' in message:
                        logger.info(f"ℹ️  今日已签到")
                    else:
                        logger.warning(f"⚠️  签到失败: {message}")
                    return result
            else:
                logger.error(f"❌ 签到失败: HTTP {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"❌ 签到异常: {str(e)}")
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

        # 预热：获取 CDN Cookie（模拟真实浏览器）
        self.warmup()

        # 随机延迟 1-3 秒
        time.sleep(random.uniform(1, 3))

        # 登录
        if not self.login(username, password):
            return False

        # 随机延迟 2-5 秒，模拟人类操作
        delay = random.uniform(2, 5)
        logger.info(f"等待 {delay:.1f} 秒后签到...")
        time.sleep(delay)

        # 签到
        result = self.checkin()

        return result is not None


def load_config(config_file: str = "config/accounts.json") -> Dict:
    """
    加载配置文件

    Args:
        config_file: 配置文件路径

    Returns:
        配置字典
    """
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
    logger.info("="*60)
    logger.info("AnyRouter 自动签到脚本启动")
    logger.info(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*60)

    # 加载配置
    config = load_config()
    if not config:
        return

    accounts = config.get('accounts', [])
    if not accounts:
        logger.error("❌ 配置文件中没有账号信息")
        return

    # 读取配置选项
    settings = config.get('settings', {})
    min_delay = settings.get('min_delay', 60)      # 最小延迟（秒）
    max_delay = settings.get('max_delay', 180)     # 最大延迟（秒）
    proxy = settings.get('proxy', None)            # 代理服务器

    logger.info(f"共加载 {len(accounts)} 个账号")
    logger.info(f"账号间延迟: {min_delay}-{max_delay} 秒")
    if proxy:
        logger.info(f"使用代理: {proxy}")
    logger.info("")

    # 处理每个账号
    success_count = 0
    fail_count = 0

    for i, account in enumerate(accounts, 1):
        # 每个账号使用独立的 Session，避免 Cookie 污染
        checker = AnyRouterCheckin(proxy=proxy)

        if checker.process_account(account):
            success_count += 1
        else:
            fail_count += 1

        # 账号之间随机延迟，防止被检测
        if i < len(accounts):
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"\n⏳ 等待 {delay:.0f} 秒后处理下一个账号...\n")
            time.sleep(delay)

    # 统计结果
    logger.info("\n" + "="*60)
    logger.info("签到完成!")
    logger.info(f"总计: {len(accounts)} 个账号")
    logger.info(f"成功: {success_count} 个")
    logger.info(f"失败: {fail_count} 个")
    logger.info("="*60)


if __name__ == "__main__":
    main()
