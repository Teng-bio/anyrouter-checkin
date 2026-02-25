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
from typing import Dict, Optional, List, Any
from urllib.parse import urlparse
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

DEFAULT_SITE_CONFIG = {
    "name": "anyrouter",
    "base_url": "https://anyrouter.top",
    "login_path": "/login",
    "console_path": "/console",
    "checkin_api_path": "/api/user/sign_in",
    "user_api_path": "/api/user/self",
    "tokens_api_path": "/api/token/?p=0&size=100",
    "auth_mode": "local",
    "linuxdo_entry_path": "/register",
    "linuxdo_button_text": "使用 LinuxDo 继续",
    "manual_auth_timeout_sec": 180,
    "storage_state_path": None,
}

SITE_CONFIG_KEYS = tuple(DEFAULT_SITE_CONFIG.keys())


def normalize_site_path(path: str, default_path: str) -> str:
    """规范化站点路径配置。"""
    if not path:
        return default_path
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return path if path.startswith("/") else f"/{path}"


def merge_site_config(settings: Dict, account: Dict) -> Dict:
    """合并站点配置（全局 settings.site + 账号级覆盖）。"""
    site_config = DEFAULT_SITE_CONFIG.copy()

    global_site = settings.get('site', {})
    if isinstance(global_site, dict):
        for key in SITE_CONFIG_KEYS:
            value = global_site.get(key)
            if value not in (None, ''):
                site_config[key] = value

    # 兼容旧配置：settings.base_url
    if settings.get('base_url'):
        site_config['base_url'] = settings.get('base_url')

    account_site = account.get('site', {})
    if isinstance(account_site, dict):
        for key in SITE_CONFIG_KEYS:
            value = account_site.get(key)
            if value not in (None, ''):
                site_config[key] = value

    # 兼容账号级平铺字段
    for key in SITE_CONFIG_KEYS:
        value = account.get(key)
        if value not in (None, ''):
            site_config[key] = value

    base_url = str(site_config.get('base_url', DEFAULT_SITE_CONFIG['base_url'])).strip()
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"https://{base_url}"
    site_config['base_url'] = base_url.rstrip('/')

    site_config['login_path'] = normalize_site_path(
        str(site_config.get('login_path', DEFAULT_SITE_CONFIG['login_path'])).strip(),
        DEFAULT_SITE_CONFIG['login_path']
    )
    site_config['console_path'] = normalize_site_path(
        str(site_config.get('console_path', DEFAULT_SITE_CONFIG['console_path'])).strip(),
        DEFAULT_SITE_CONFIG['console_path']
    )
    site_config['checkin_api_path'] = normalize_site_path(
        str(site_config.get('checkin_api_path', DEFAULT_SITE_CONFIG['checkin_api_path'])).strip(),
        DEFAULT_SITE_CONFIG['checkin_api_path']
    )
    site_config['user_api_path'] = normalize_site_path(
        str(site_config.get('user_api_path', DEFAULT_SITE_CONFIG['user_api_path'])).strip(),
        DEFAULT_SITE_CONFIG['user_api_path']
    )
    site_config['tokens_api_path'] = normalize_site_path(
        str(site_config.get('tokens_api_path', DEFAULT_SITE_CONFIG['tokens_api_path'])).strip(),
        DEFAULT_SITE_CONFIG['tokens_api_path']
    )
    site_config['linuxdo_entry_path'] = normalize_site_path(
        str(site_config.get('linuxdo_entry_path', DEFAULT_SITE_CONFIG['linuxdo_entry_path'])).strip(),
        DEFAULT_SITE_CONFIG['linuxdo_entry_path']
    )

    auth_mode = str(site_config.get('auth_mode', DEFAULT_SITE_CONFIG['auth_mode'])).strip().lower()
    site_config['auth_mode'] = auth_mode if auth_mode else DEFAULT_SITE_CONFIG['auth_mode']

    try:
        site_config['manual_auth_timeout_sec'] = int(site_config.get('manual_auth_timeout_sec', 180))
    except Exception:
        site_config['manual_auth_timeout_sec'] = 180

    storage_state = site_config.get('storage_state_path')
    if isinstance(storage_state, str) and storage_state.strip():
        storage_path = Path(storage_state.strip())
        if not storage_path.is_absolute():
            storage_path = Path(__file__).parent / storage_path
        site_config['storage_state_path'] = str(storage_path)
    else:
        site_config['storage_state_path'] = None

    site_name = str(site_config.get('name', '')).strip()
    if not site_name:
        site_name = urlparse(site_config['base_url']).netloc or site_config['base_url']
    site_config['name'] = site_name

    return site_config


def build_account_key(account: Dict, settings: Dict) -> str:
    """构建账号唯一键，避免多站点同用户名相互覆盖结果。"""
    site_config = merge_site_config(settings, account)
    username = str(account.get('username', '')).strip()
    return f"{site_config['base_url']}::{username}"


def format_account_label(account: Dict, settings: Dict) -> str:
    """格式化账号显示标签。"""
    site_config = merge_site_config(settings, account)
    return f"{account.get('username')} @ {site_config['base_url']}"


def select_accounts(accounts: List[Dict], account_filter: Optional[str]) -> List[Dict]:
    """按用户名过滤账号（精确匹配）。"""
    if not account_filter:
        return accounts
    filtered = [a for a in accounts if str(a.get('username', '')).strip() == account_filter.strip()]
    return filtered


def to_int(value: Any, default: int = 0) -> int:
    """尽量将任意值转换为整数。"""
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        return int(float(value))
    except Exception:
        return default


def extract_token_items(payload: Any, depth: int = 0) -> List[Any]:
    """
    从不同结构中提取 token 列表。
    兼容 list / dict(data/list/items/rows/tokens/records) / 单个字符串。
    """
    if payload is None or depth > 4:
        return []

    if isinstance(payload, list):
        return payload

    if isinstance(payload, str):
        return [payload] if payload.strip() else []

    if isinstance(payload, dict):
        list_keys = ('data', 'list', 'items', 'rows', 'tokens', 'records')
        for key in list_keys:
            value = payload.get(key)
            if isinstance(value, list):
                return value

        for key in list_keys:
            value = payload.get(key)
            if isinstance(value, dict):
                nested = extract_token_items(value, depth + 1)
                if nested:
                    return nested

        token_keys = ('key', 'token', 'token_key', 'access_key')
        quota_keys = ('remain_quota', 'quota', 'balance', 'used_quota', 'used')
        if any(k in payload for k in token_keys + quota_keys):
            return [payload]

    return []


def normalize_token_item(item: Any, index: int = 0) -> Optional[Dict]:
    """将单个 token 项标准化为字典结构。"""
    if isinstance(item, dict):
        key_candidates = [
            item.get('key'),
            item.get('token_key'),
            item.get('token'),
            item.get('access_key'),
            item.get('value'),
        ]
        token_key = ''
        for candidate in key_candidates:
            if candidate is None:
                continue
            text = str(candidate).strip()
            if text:
                token_key = text
                break
        if token_key.startswith('sk-'):
            token_key = token_key[3:]

        token_name = item.get('name') or item.get('token_name') or item.get('title') or f"token_{index + 1}"
        token_name = str(token_name).strip() or f"token_{index + 1}"

        remain_quota = to_int(item.get('remain_quota', item.get('quota', item.get('balance', 0))), 0)
        used_quota = to_int(item.get('used_quota', item.get('used', 0)), 0)

        return {
            'name': token_name,
            'key': token_key,
            'remain_quota': remain_quota,
            'used_quota': used_quota,
            'status': to_int(item.get('status', 0), 0),
            'expired_time': to_int(item.get('expired_time', item.get('expires_at', 0)), 0),
            'created_time': to_int(item.get('created_time', item.get('created_at', 0)), 0),
        }

    if isinstance(item, str):
        token_key = item.strip()
        if not token_key:
            return None
        if token_key.startswith('sk-'):
            token_key = token_key[3:]
        return {
            'name': f"token_{index + 1}",
            'key': token_key,
            'remain_quota': 0,
            'used_quota': 0,
            'status': 0,
            'expired_time': 0,
            'created_time': 0,
        }

    return None


def normalize_tokens_payload(payload: Any) -> List[Dict]:
    """将任意 token 返回结构规范化为 List[Dict]。"""
    raw_items = extract_token_items(payload)
    normalized: List[Dict] = []
    for idx, item in enumerate(raw_items):
        token = normalize_token_item(item, idx)
        if token is not None:
            normalized.append(token)
    return normalized


class AnyRouterCheckin:
    """AnyRouter 签到类 (Playwright 版本)"""

    def __init__(self, headless: bool = True, proxy: str = None, site_config: Dict = None, cdp_url: str = None):
        """
        初始化

        Args:
            headless: 是否使用无头模式（不显示浏览器窗口）
            proxy: 代理服务器地址，格式如：
                   - http://ip:port
                   - http://user:pass@ip:port
                   - socks5://ip:port
        """
        resolved_site = site_config or DEFAULT_SITE_CONFIG.copy()
        self.site_name = str(resolved_site.get('name', 'anyrouter'))
        self.base_url = str(resolved_site.get('base_url', DEFAULT_SITE_CONFIG['base_url']))
        self.login_path = str(resolved_site.get('login_path', DEFAULT_SITE_CONFIG['login_path']))
        self.console_path = str(resolved_site.get('console_path', DEFAULT_SITE_CONFIG['console_path']))
        self.checkin_api_path = str(resolved_site.get('checkin_api_path', DEFAULT_SITE_CONFIG['checkin_api_path']))
        self.user_api_path = str(resolved_site.get('user_api_path', DEFAULT_SITE_CONFIG['user_api_path']))
        self.tokens_api_path = str(resolved_site.get('tokens_api_path', DEFAULT_SITE_CONFIG['tokens_api_path']))
        self.auth_mode = str(resolved_site.get('auth_mode', DEFAULT_SITE_CONFIG['auth_mode'])).lower()
        self.linuxdo_entry_path = str(resolved_site.get('linuxdo_entry_path', DEFAULT_SITE_CONFIG['linuxdo_entry_path']))
        self.linuxdo_button_text = str(resolved_site.get('linuxdo_button_text', DEFAULT_SITE_CONFIG['linuxdo_button_text']))
        self.manual_auth_timeout_sec = int(resolved_site.get('manual_auth_timeout_sec', DEFAULT_SITE_CONFIG['manual_auth_timeout_sec']))
        self.storage_state_path = resolved_site.get('storage_state_path')
        self.headless = headless
        self.proxy = proxy
        self.cdp_url = cdp_url.strip() if isinstance(cdp_url, str) and cdp_url.strip() else None
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.connected_over_cdp = False

    def _build_url(self, path: str) -> str:
        """构建完整 URL，支持绝对地址和相对路径。"""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

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

        if self.cdp_url:
            logger.info(f"尝试连接已打开浏览器: {self.cdp_url}")
            self.browser = self.playwright.chromium.connect_over_cdp(self.cdp_url)
            self.connected_over_cdp = True

            if self.browser.contexts:
                self.context = self.browser.contexts[0]
            else:
                self.context = self.browser.new_context()

            # 在现有浏览器上下文中新开一个标签页执行自动化，尽量不影响用户原页面
            self.page = self.context.new_page()
            self.page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
            """)

            logger.info("✅ 已连接已打开浏览器 (CDP 模式)")
            return

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

        # 加载已保存的登录态（适用于 LinuxDo OAuth 场景）
        if self.storage_state_path:
            state_file = Path(self.storage_state_path)
            if state_file.exists():
                context_options['storage_state'] = str(state_file)
                logger.info(f"使用已保存登录态: {state_file}")

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
        try:
            if self.connected_over_cdp:
                # CDP 模式下只关闭脚本创建的标签页并断开连接，不关闭用户原浏览器
                if self.page:
                    try:
                        self.page.close()
                    except Exception:
                        pass
                if self.playwright:
                    self.playwright.stop()
                logger.info("已断开已打开浏览器连接")
                return

            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
            logger.info("浏览器已关闭")
        except Exception:
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

    def wait_for_console_url(self, timeout_sec: int = 15) -> bool:
        """等待跳转到控制台页面。"""
        timeout_sec = max(timeout_sec, 1)
        deadline = time.time() + timeout_sec
        target_urls = [self._build_url(self.console_path), self._build_url("/console")]
        target_urls = list(dict.fromkeys(target_urls))

        while time.time() < deadline:
            pages: List[Page] = []
            seen = set()
            if self.page:
                pages.append(self.page)
                seen.add(id(self.page))
            if self.context:
                for p in self.context.pages:
                    if id(p) not in seen:
                        pages.append(p)
                        seen.add(id(p))

            for p in pages:
                current_url = (p.url or "").strip()
                if not current_url:
                    continue
                if any(current_url.startswith(target) for target in target_urls) or "/console" in current_url:
                    self.page = p
                    return True

            time.sleep(0.4)

        return False

    def check_authenticated(self) -> bool:
        """通过 /api/user/self 判断当前是否已登录。"""
        try:
            result = self.page.evaluate("""
                async (userApiUrl) => {
                    const userStr = localStorage.getItem('user');
                    const user = userStr ? JSON.parse(userStr) : null;
                    const userId = user ? user.id : '';

                    const response = await fetch(userApiUrl, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                            'new-api-user': String(userId)
                        },
                    });
                    if (!response.ok) {
                        return { success: false, status: response.status };
                    }
                    return await response.json();
                }
            """, self._build_url(self.user_api_path))
            return bool(result.get('success'))
        except Exception:
            return False

    def save_storage_state(self):
        """保存登录态到文件，便于后续免人工授权。"""
        if not self.storage_state_path or not self.context:
            return
        try:
            state_file = Path(self.storage_state_path)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            self.context.storage_state(path=str(state_file))
            os.chmod(state_file, 0o600)
            logger.info(f"已保存登录态: {state_file}")
        except Exception as e:
            logger.warning(f"保存登录态失败: {str(e)}")

    def build_linuxdo_entry_urls(self) -> List[str]:
        """构建 LinuxDo 授权入口候选地址（去重后按顺序尝试）。"""
        candidates = [self.linuxdo_entry_path, "/login", "/register"]
        urls = []
        seen = set()
        for path in candidates:
            url = self._build_url(path)
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def click_linuxdo_button(self) -> bool:
        """点击 LinuxDo 授权按钮，并处理可能的新标签页。"""
        selectors = [
            f'button:has-text("{self.linuxdo_button_text}")',
            'button:has-text("使用 LinuxDo 继续")',
            'button:has-text("使用 LinuxDO 继续")',
            'a:has-text("使用 LinuxDo 继续")',
            'a:has-text("使用 LinuxDO 继续")',
        ]

        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if not btn.is_visible(timeout=1500):
                    continue

                old_page_ids = {id(p) for p in self.context.pages} if self.context else set()
                btn.click(force=True)
                self.page.wait_for_timeout(1200)

                # 某些站点会在新标签页打开授权页
                if self.context:
                    for p in self.context.pages:
                        if id(p) not in old_page_ids:
                            self.page = p
                            logger.info("检测到授权新页面，已切换")
                            break

                logger.info("已点击 LinuxDo 授权入口")
                return True
            except Exception:
                continue
        return False

    def wait_and_click_linuxdo_button(self, timeout_sec: int) -> bool:
        """等待 LinuxDo 按钮出现并点击。"""
        deadline = time.time() + max(timeout_sec, 1)
        while time.time() < deadline:
            if self.click_linuxdo_button():
                return True
            self.page.wait_for_timeout(1500)
        return False

    def login_with_linuxdo(self) -> bool:
        """使用 LinuxDo OAuth 登录。"""
        try:
            logger.info("使用 LinuxDo 登录模式")

            # 无头模式下无法完成人机验证，必须先有可复用登录态
            state_exists = False
            if self.storage_state_path:
                state_exists = Path(self.storage_state_path).exists()
            if self.headless and not state_exists:
                logger.error("❌ LinuxDo 模式首次授权需要可视化浏览器")
                logger.error("   请先运行: python checkin_playwright.py -c <配置> --prepare-linuxdo --account <账号名>")
                logger.error("   首次授权成功后会保存 storage_state，后续可无头运行")
                return False

            # 优先尝试现有登录态
            try:
                self.page.goto(self._build_url(self.console_path), wait_until="domcontentloaded", timeout=45000)
                if self.check_authenticated():
                    logger.info("✅ 使用已有登录态进入控制台")
                    return True
            except Exception as e:
                logger.info(f"控制台直连检查未通过，转授权入口: {str(e)}")

            # 尝试多个入口页（/register 与 /login 都可能出现 LinuxDo 按钮）
            clicked = False
            wait_for_button_sec = 12 if self.headless else min(max(self.manual_auth_timeout_sec, 30), 120)
            for entry_url in self.build_linuxdo_entry_urls():
                try:
                    logger.info(f"尝试授权入口: {entry_url}")
                    self.page.goto(entry_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    current_url = self.page.url or ""
                    if "/login" in current_url or "/register" in current_url:
                        logger.info(f"授权入口发生重定向，继续流程: {current_url}")
                    else:
                        logger.debug(f"入口访问失败: {entry_url} - {str(e)}")
                        continue

                self.random_delay(1, 2)
                clicked = self.wait_and_click_linuxdo_button(wait_for_button_sec)
                if clicked:
                    break

                # 某些页面默认在登录 tab，点“注册”后才出现 LinuxDo 按钮
                try:
                    register_link = self.page.locator('a:has-text("注册"), button:has-text("注册")').first
                    if register_link.is_visible(timeout=2000):
                        register_link.click(force=True)
                        self.page.wait_for_timeout(1200)
                        clicked = self.wait_and_click_linuxdo_button(8 if self.headless else 40)
                        if clicked:
                            break
                except Exception:
                    pass

            if not clicked:
                self.save_screenshot("linuxdo_button_not_found")
                logger.error("❌ 未找到 LinuxDo 登录按钮（可能受人机验证/风控影响）")
                return False

            # 等待授权流程完成（支持手动完成人机验证）
            deadline = time.time() + max(self.manual_auth_timeout_sec, 30)
            if not self.headless:
                logger.info("请在浏览器中完成人机验证和 LinuxDo 授权，脚本将自动等待登录完成")

            while time.time() < deadline:
                if self.wait_for_console_url(timeout_sec=3):
                    if self.check_authenticated():
                        self.save_storage_state()
                        logger.info("✅ LinuxDo 登录成功")
                        return True

                # 兼容部分站点登录后先回首页，再跳控制台
                current_url = (self.page.url or "").strip()
                if current_url.startswith(self.base_url) and self.check_authenticated():
                    self.save_storage_state()
                    logger.info("✅ LinuxDo 登录成功（已登录，等待控制台跳转）")
                    return True

                self.page.wait_for_timeout(2000)

            self.save_screenshot("linuxdo_login_timeout")
            logger.error("❌ LinuxDo 登录超时（请在非无头模式手动完成人机验证并授权）")
            return False
        except Exception as e:
            self.save_screenshot("linuxdo_login_exception")
            logger.error(f"❌ LinuxDo 登录异常: {str(e)}")
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
        if self.auth_mode == "linuxdo":
            return self.login_with_linuxdo()

        try:
            logger.info(f"正在登录账号: {username}")

            # 访问登录页面
            self.page.goto(self._build_url(self.login_path), wait_until="networkidle")
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

            # 等待登录完成（兼容站点登录后不跳转 /console 的情况）
            auth_deadline = time.time() + 20
            while time.time() < auth_deadline:
                if self.wait_for_console_url(timeout_sec=2):
                    logger.info(f"✅ 登录成功: {username}")
                    return True

                if self.check_authenticated():
                    current_url = (self.page.url or "").strip()
                    logger.info(f"✅ 登录成功: {username} (已认证，当前页面: {current_url})")
                    return True

                self.page.wait_for_timeout(800)

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
            console_url = self._build_url(self.console_path)
            if not self.page.url.startswith(console_url):
                self.page.goto(console_url, wait_until="networkidle")
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

            # 按钮不可点击通常表示“今日已签到”或受前端状态限制，改用 API 确认
            try:
                button_enabled = checkin_button.is_enabled(timeout=1000)
            except Exception:
                button_enabled = True

            if not button_enabled:
                button_text = ""
                try:
                    button_text = (checkin_button.text_content() or "").strip()
                except Exception:
                    pass

                if "已签到" in button_text:
                    logger.info("ℹ️  今日已签到（按钮不可点击）")
                    return True

                logger.info("签到按钮不可点击，改用 API 方式确认签到状态...")
                return self.api_checkin()

            # 点击签到按钮；若失败则回退到 API 签到
            try:
                checkin_button.click(timeout=8000)
            except Exception as e:
                logger.warning(f"点击签到按钮失败，改用 API 签到: {str(e)}")
                return self.api_checkin()

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
                async (apiUrl) => {
                    const response = await fetch(apiUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                    });
                    return await response.json();
                }
            """, self._build_url(self.checkin_api_path))

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
                async (userApiUrl) => {
                    // 从 localStorage 获取用户 ID
                    const userStr = localStorage.getItem('user');
                    const user = userStr ? JSON.parse(userStr) : null;
                    const userId = user ? user.id : '';

                    const response = await fetch(userApiUrl, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                            'new-api-user': String(userId)
                        },
                    });
                    return await response.json();
                }
            """, self._build_url(self.user_api_path))

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
                async (tokensApiUrl) => {
                    // 从 localStorage 获取用户 ID
                    const userStr = localStorage.getItem('user');
                    const user = userStr ? JSON.parse(userStr) : null;
                    const userId = user ? user.id : '';

                    const response = await fetch(tokensApiUrl, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json',
                            'new-api-user': String(userId)
                        },
                    });
                    return await response.json();
                }
            """, self._build_url(self.tokens_api_path))

            # 兼容不同站点返回结构：
            # 1) {"success":true, "data":[...]}
            # 2) {"success":true, "data":{"items":[...]}}
            # 3) 直接返回 list / str
            payload: Any
            if isinstance(result, dict):
                if not result.get('success', True):
                    logger.warning(f"获取令牌列表失败: {result.get('message', '未知错误')}")
                payload = result.get('data', result)
            else:
                payload = result

            tokens = normalize_tokens_payload(payload)
            if payload and not tokens:
                logger.warning("令牌数据结构无法识别，已跳过本次令牌解析")
            return tokens

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
        password = account.get('password', '')

        result = {
            'username': username,
            'site': self.site_name,
            'base_url': self.base_url,
            'auth_mode': self.auth_mode,
            'account_key': f"{self.base_url}::{username}",
            'success': False,
            'user_id': None,
            'quota': 0,
            'tokens': []
        }

        if not username:
            logger.error("❌ 账号配置错误: 缺少用户名")
            return result

        if self.auth_mode != 'linuxdo' and not password:
            logger.error("❌ 账号配置错误: 缺少密码")
            return result

        if self.auth_mode == 'linuxdo' and self.headless:
            state_exists = False
            if self.storage_state_path:
                state_exists = Path(self.storage_state_path).exists()
            if not state_exists:
                logger.error("❌ LinuxDo 模式在无头下缺少可用登录态")
                logger.error("   请先执行 --prepare-linuxdo 进行一次人工授权并保存 storage_state")
                return result

        logger.info(f"\n{'='*50}")
        logger.info(f"开始处理账号: {username}")
        logger.info(f"站点: {self.site_name} ({self.base_url})")
        logger.info(f"认证模式: {self.auth_mode}")
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
                    token_name = str(token.get('name', '未命名'))
                    token_key = str(token.get('key', ''))
                    token_quota = to_int(token.get('remain_quota', 0), 0) / 500000  # 转换为美元
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

        except Exception as e:
            logger.error(f"❌ 处理账号异常: {username} - {str(e)}")
            return result

        finally:
            # 确保浏览器被关闭
            self.close_browser()


def is_valid_account(account: Dict, settings: Dict = None) -> bool:
    """
    检查账号是否有效

    跳过以下情况：
    - 用户名或密码为空
    - 用户名或密码是占位符（如 "账号1", "your_username" 等）
    """
    settings = settings or {}
    site_config = merge_site_config(settings, account)
    auth_mode = site_config.get('auth_mode', 'local')

    username = str(account.get('username', '')).strip()
    password = str(account.get('password', '')).strip()
    username_lower = username.lower()
    password_lower = password.lower()

    # 检查是否为空
    if not username:
        return False
    if auth_mode != 'linuxdo' and not password:
        return False

    # LinuxDo 授权模式：只做最基础校验（无密码字段）
    if auth_mode == 'linuxdo':
        obvious_placeholders = {
            '账号', '用户名', 'username', 'your_username',
            'your_runanytime_user', 'your_account'
        }
        return username_lower not in obvious_placeholders

    # 常见的占位符关键词
    placeholders = [
        '账号', '密码', 'username', 'password', 'your_',
        'example', 'test', 'xxx', 'user', 'pass',
        '用户名', '你的'
    ]

    for placeholder in placeholders:
        if placeholder in username_lower:
            return False
        if auth_mode != 'linuxdo' and placeholder in password_lower:
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
        site = account.get('base_url', account.get('site', ''))
        tokens = normalize_tokens_payload(account.get('tokens', []))
        for token in tokens:
            token_key = str(token.get('key', '')).strip()
            tokens_data.append({
                'site': site,
                'username': username,
                'user_id': account.get('user_id'),
                'account_quota_raw': account.get('quota', 0),  # 原始值
                'account_quota_usd': account.get('quota', 0) / 500000,  # 美元
                'token_name': token.get('name', ''),
                'token_key': f"sk-{token_key}" if token_key else "",  # 完整密钥
                'token_quota_raw': to_int(token.get('remain_quota', 0), 0),  # 原始值
                'token_quota_usd': to_int(token.get('remain_quota', 0), 0) / 500000,  # 美元
                'used_quota_raw': to_int(token.get('used_quota', 0), 0),
                'used_quota_usd': to_int(token.get('used_quota', 0), 0) / 500000,
                'status': to_int(token.get('status', 0), 0),
                'expired_time': to_int(token.get('expired_time', 0), 0),
                'created_time': to_int(token.get('created_time', 0), 0),
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
        writer.writerow(['站点', '账号', '用户ID', '账户余额($)', '令牌名称', '令牌余额($)', '已用额度($)', '令牌密钥', '签到结果'])

        for account in accounts_data:
            site = account.get('base_url', account.get('site', ''))
            username = account.get('username')
            user_id = account.get('user_id', '')
            account_quota = account.get('quota', 0) / 500000  # 转换为美元
            checkin_result = '成功' if account.get('success') else '失败'
            tokens = normalize_tokens_payload(account.get('tokens', []))

            if tokens:
                for token in tokens:
                    token_name = str(token.get('name', ''))
                    token_quota = to_int(token.get('remain_quota', 0), 0) / 500000
                    used_quota = to_int(token.get('used_quota', 0), 0) / 500000
                    token_key = str(token.get('key', '')).strip()

                    if show_keys:
                        display_key = f"sk-{token_key}" if token_key else ''
                    else:
                        display_key = mask_token_key(token_key) if token_key else ''

                    writer.writerow([site, username, user_id, f"{account_quota:.2f}", token_name,
                                   f"{token_quota:.2f}", f"{used_quota:.2f}", display_key, checkin_result])
            else:
                writer.writerow([site, username, user_id, f"{account_quota:.2f}", '', '', '', '', checkin_result])

    # 3. 按额度分类生成令牌文件
    keys_by_quota = {}  # {额度: [令牌列表]}

    for account in accounts_data:
        tokens = normalize_tokens_payload(account.get('tokens', []))
        for token in tokens:
            token_key = str(token.get('key', '')).strip()
            if token_key:
                quota_usd = to_int(token.get('remain_quota', 0), 0) / 500000
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
    global_cdp_url = settings.get('cdp_url', None)

    accounts_data = []

    for i, account in enumerate(accounts, 1):
        site_config = merge_site_config(settings, account)
        account_proxy = account.get('proxy', global_proxy)
        account_cdp_url = account.get('cdp_url', global_cdp_url)
        checker = AnyRouterCheckin(
            headless=headless,
            proxy=account_proxy,
            site_config=site_config,
            cdp_url=account_cdp_url,
        )

        result = checker.process_account(account)
        result['account_key'] = build_account_key(account, settings)
        accounts_data.append(result)

        # 账号之间随机延迟
        if i < len(accounts):
            delay = random.uniform(min_delay, max_delay)
            logger.info(f"\n⏳ 等待 {delay:.0f} 秒后处理下一个账号...\n")
            time.sleep(delay)

    return accounts_data


def prepare_linuxdo_auth(accounts: List[Dict], settings: Dict, account_filter: Optional[str] = None) -> bool:
    """
    仅执行 LinuxDo 授权并保存登录态，不做签到。

    Returns:
        是否全部成功
    """
    target_accounts = select_accounts(accounts, account_filter)
    if account_filter and not target_accounts:
        logger.error(f"❌ 未找到账号: {account_filter}")
        return False

    linuxdo_accounts = []
    for account in target_accounts:
        site_config = merge_site_config(settings, account)
        if site_config.get('auth_mode') == 'linuxdo':
            linuxdo_accounts.append((account, site_config))

    if not linuxdo_accounts:
        logger.error("❌ 没有可用于 LinuxDo 授权的账号（请在账号 site.auth_mode 设置为 linuxdo）")
        return False

    all_ok = True
    global_proxy = settings.get('proxy', None)
    global_cdp_url = settings.get('cdp_url', None)

    for account, site_config in linuxdo_accounts:
        username = account.get('username')
        account_proxy = account.get('proxy', global_proxy)
        account_cdp_url = account.get('cdp_url', global_cdp_url)
        checker = AnyRouterCheckin(
            headless=False,
            proxy=account_proxy,
            site_config=site_config,
            cdp_url=account_cdp_url,
        )

        logger.info("\n" + "=" * 60)
        logger.info(f"准备 LinuxDo 授权: {username} @ {site_config.get('base_url')}")
        logger.info("将启动可视化浏览器，请手动完成人机验证与授权")
        logger.info("=" * 60)

        try:
            checker.start_browser()
            ok = checker.login(username, account.get('password', ''))
            if not ok:
                logger.error(f"❌ 授权失败: {username}")
                all_ok = False
                continue

            # 登录成功后再次保存一次状态，确保落盘
            checker.save_storage_state()
            user_info = checker.get_user_info()
            if user_info:
                quota = user_info.get('quota', 0) / 500000
                logger.info(f"✅ 授权成功: {username} (用户ID: {user_info.get('id')}, 余额: ${quota:.2f})")
            else:
                logger.info(f"✅ 授权成功: {username}")
        except Exception as e:
            logger.error(f"❌ 授权过程异常: {username} - {str(e)}")
            all_ok = False
        finally:
            checker.close_browser()

    return all_ok


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AnyRouter 自动签到脚本')
    parser.add_argument('-c', '--config', default='config/accounts.json',
                        help='配置文件路径 (默认: config/accounts.json)')
    parser.add_argument('--show-keys', action='store_true',
                        help='在 CSV 报告中显示完整令牌密钥')
    parser.add_argument('--account', default=None,
                        help='指定单个账号用户名（用于定向执行）')
    parser.add_argument('--prepare-linuxdo', action='store_true',
                        help='仅执行 LinuxDo 授权并保存登录态，不进行签到')
    parser.add_argument('--cdp-url', default=None,
                        help='连接已打开 Chromium 浏览器（例如 http://127.0.0.1:9222）')
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

    accounts = select_accounts(accounts, args.account)
    if args.account and not accounts:
        logger.error(f"❌ 配置文件中找不到账号: {args.account}")
        return

    # 过滤无效账号
    valid_accounts = []
    skipped_accounts = []

    for account in accounts:
        if is_valid_account(account, settings=config.get('settings', {})):
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
    if args.cdp_url:
        settings['cdp_url'] = args.cdp_url.strip()
    min_delay = settings.get('min_delay', 60)
    max_delay = settings.get('max_delay', 180)
    headless = settings.get('headless', True)
    global_proxy = settings.get('proxy', None)
    global_cdp_url = settings.get('cdp_url', None)
    retry_delay_hours = settings.get('retry_delay_hours', 1)  # 重试等待时间（小时）
    max_retries = settings.get('max_retries', 2)  # 最大重试次数
    email_config = settings.get('email', {})  # 邮件配置
    default_site = merge_site_config(settings, {})

    logger.info(f"共加载 {len(valid_accounts)} 个有效账号")
    logger.info(f"账号间延迟: {min_delay}-{max_delay} 秒")
    logger.info(f"无头模式: {'是' if headless else '否'}")
    logger.info(f"失败重试: 最多 {max_retries} 次，间隔 {retry_delay_hours} 小时")
    logger.info(f"默认站点: {default_site.get('name')} ({default_site.get('base_url')})")
    if global_proxy:
        logger.info(f"全局代理: {global_proxy}")
    if global_cdp_url:
        logger.info(f"已启用已打开浏览器连接: {global_cdp_url}")
    if email_config.get('enabled'):
        logger.info(f"邮件通知: 已启用 -> {email_config.get('receiver', email_config.get('sender'))}")
    logger.info("")

    # 仅准备 LinuxDo 授权
    if args.prepare_linuxdo:
        ok = prepare_linuxdo_auth(valid_accounts, settings, account_filter=args.account)
        if ok:
            logger.info("✅ LinuxDo 授权准备完成")
        else:
            logger.error("❌ LinuxDo 授权准备失败")
        return

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
            account_key = result.get('account_key')
            if not account_key:
                account_key = f"{result.get('base_url', '')}::{result.get('username', '')}"
            all_accounts_data[account_key] = result

        # 检查失败账号
        failed_accounts = [a for a in accounts_to_process
                         if not all_accounts_data.get(build_account_key(a, settings), {}).get('success')]

        if not failed_accounts:
            logger.info("\n✅ 所有账号签到成功!")
            break

        # 如果还有重试次数，等待后重试
        if retry_round < max_retries:
            wait_seconds = retry_delay_hours * 3600
            logger.info(f"\n⏰ {len(failed_accounts)} 个账号失败，将在 {retry_delay_hours} 小时后重试...")
            logger.info(f"   失败账号: {', '.join(format_account_label(a, settings) for a in failed_accounts)}")
            time.sleep(wait_seconds)
            accounts_to_process = failed_accounts
        else:
            logger.warning(f"\n⚠️  {len(failed_accounts)} 个账号最终失败")

    # 汇总结果
    final_results = list(all_accounts_data.values())
    success_count = sum(1 for r in final_results if r.get('success'))
    fail_count = len(final_results) - success_count
    failed_usernames = [
        f"{r.get('username')} @ {r.get('base_url', r.get('site', ''))}"
        for r in final_results if not r.get('success')
    ]

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
