# Scrapy settings for xiaogu scanner
BOT_NAME = 'xiaogu_scrapy'

SPIDER_MODULES = ['scrapy_scanner.spiders']
NEWSPIDER_MODULE = 'scrapy_scanner.spiders'

# Obey robots.txt
ROBOTSTXT_OBEY = False

# Disable Telnet Console
TELNETCONSOLE_ENABLED = False

# Override the default request headers
DEFAULT_REQUEST_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
}

# Configure item pipelines
ITEM_PIPELINES = {}

# Reduce log verbosity
LOG_LEVEL = 'WARNING'

# Auto-throttle to be polite
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.1
AUTOTHROTTLE_MAX_DELAY = 1.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

# Disable cookies (not needed for API calls)
COOKIES_ENABLED = False

# DNS cache
DNSCACHE_ENABLED = True
DNSCACHE_SIZE = 100

# Request timeout
DOWNLOAD_TIMEOUT = 15

# Retry
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]
