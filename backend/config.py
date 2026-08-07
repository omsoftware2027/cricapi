import os

# Bright Data proxy configuration (used for Cloudflare-blocked sites like cricheroes)
BRIGHTDATA_PROXY_HOST = os.environ.get("BRIGHTDATA_PROXY_HOST", "brd.superproxy.io")
BRIGHTDATA_PROXY_PORT = os.environ.get("BRIGHTDATA_PROXY_PORT", "44445")
BRIGHTDATA_PROXY_USER = os.environ.get("BRIGHTDATA_PROXY_USER", "")
BRIGHTDATA_PROXY_PASS = os.environ.get("BRIGHTDATA_PROXY_PASS", "")


def brightdata_proxy_url() -> str | None:
    if not BRIGHTDATA_PROXY_USER or not BRIGHTDATA_PROXY_PASS:
        return None
    return f"http://{BRIGHTDATA_PROXY_USER}:{BRIGHTDATA_PROXY_PASS}@{BRIGHTDATA_PROXY_HOST}:{BRIGHTDATA_PROXY_PORT}"


# Cricheroes API constants (discovered via traffic inspection)
CRICHEROES_API_BASE = "https://api.cricheroes.in/api/v1"
CRICHEROES_API_KEY = "cr!CkH3r0s"
CRICHEROES_UDID = "1410d72e8783739df812e678d2844e81"
CRICHEROES_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
