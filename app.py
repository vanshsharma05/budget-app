import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import re
import json
import os
import random
import time

# -----------------------------------------------------------------------------
# 0. PAGE CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Luxury Shopping Calculator",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_FILE = "wishlist_data.json"

# -----------------------------------------------------------------------------
# 1. PERSISTENCE
# -----------------------------------------------------------------------------
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("items", []), float(data.get("budget", 100000.0))
        except Exception:
            return [], 100000.0
    return [], 100000.0


def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "items": st.session_state.shopping_list,
                    "budget": st.session_state.total_budget,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 2. CURRENCY HELPERS
# -----------------------------------------------------------------------------
APPROX_RATES_TO_INR = {
    "INR": 1.0, "USD": 86.0, "EUR": 93.0, "GBP": 109.0, "AED": 23.0,
    "JPY": 0.57, "CNY": 12.0, "SGD": 64.0, "CAD": 62.0, "AUD": 56.0, "CHF": 98.0,
}
CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


def fmt_inr(n):
    """Format number in Indian Rupee style: ₹ 1,00,000"""
    n = round(abs(n))
    s = str(n)
    if len(s) <= 3:
        return f"₹ {s}"
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result
        s = s[:-2]
    if s:
        result = s + "," + result
    return f"₹ {result}"


def parse_amount(raw):
    s = re.sub(r"[^\d.,]", "", str(raw)).strip(".,")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


def detect_currency(text):
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    m = re.search(r"\b(INR|USD|EUR|GBP|AED|JPY|CNY|SGD|CAD|AUD|CHF|Rs)\b", text, re.I)
    if m:
        code = m.group(1).upper()
        return "INR" if code == "RS" else code
    return None


# -----------------------------------------------------------------------------
# 3. ENHANCED SCRAPER — multiple strategies for luxury sites
# -----------------------------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# Known luxury site patterns for price/title extraction
LUXURY_PATTERNS = {
    "louisvuitton.com": {
        "price_selectors": [
            '[data-testid="product-price"]', '.lv-product-price',
            '.product-price', '.price', '.product__price',
            'span[class*="price"]', 'div[class*="price"]',
            'p[class*="price"]',
        ],
        "title_selectors": [
            'h1[class*="product"]', '.product-name', '.product__title',
            'h1[class*="title"]', '[data-testid="product-name"]',
        ],
        "image_selectors": [
            'img[class*="product"]', '.product-image img',
            'img[data-testid*="product"]',
        ],
    },
    "gucci.com": {
        "price_selectors": [
            '.product-detail-price', 'span[class*="price"]',
            'div[class*="price"]', '.price',
        ],
        "title_selectors": [
            'h1[class*="product"]', '.product-name', 'h1',
        ],
        "image_selectors": [
            'img[class*="product"]', '.product-image img',
        ],
    },
    "prada.com": {
        "price_selectors": [
            '.price', 'span[class*="price"]', 'div[class*="price"]',
            '[data-price]',
        ],
        "title_selectors": [
            'h1', '.product-name', 'h1[class*="product"]',
        ],
        "image_selectors": [
            'img[class*="product"]', '.product-image img',
        ],
    },
}

# Generic fallback selectors that work on many e-commerce sites
GENERIC_PRICE_SELECTORS = [
    '[itemprop="price"]', '[data-price]', '.price', '#price',
    'span[class*="price"]', 'div[class*="price"]', 'p[class*="price"]',
    '.product-price', '.product__price', '.pdp-price',
    'span[class*="amount"]', '.offer-price', '.sale-price',
    '.current-price', 'ins .amount', '.price-current',
    '[class*="ProductPrice"]', '[class*="product-price"]',
]

GENERIC_TITLE_SELECTORS = [
    '[itemprop="name"]', 'h1[class*="product"]', 'h1[class*="title"]',
    '.product-name', '.product-title', '.product__title',
    '.pdp-title', '[class*="ProductName"]', '[class*="product-name"]',
    'h1',
]

GENERIC_IMAGE_SELECTORS = [
    '[itemprop="image"]', 'img[class*="product"]', 'img[class*="gallery"]',
    '.product-image img', '.product__image img', '.pdp-image img',
    'img[class*="main"]', 'img[data-zoom]', 'img[data-src*="product"]',
    'meta[property="og:image"]',
]


def _walk_jsonld(node, found):
    if isinstance(node, dict):
        types = node.get("@type", "")
        types = " ".join(types) if isinstance(types, list) else str(types)
        is_product = "product" in types.lower()
        if is_product:
            if node.get("name") and not found.get("title"):
                found["title"] = str(node["name"]).strip()
            img = node.get("image")
            if img and not found.get("image"):
                if isinstance(img, list):
                    img = img[0]
                if isinstance(img, dict):
                    img = img.get("url") or img.get("contentUrl")
                if img:
                    found["image"] = str(img)
        offers = node.get("offers")
        if offers:
            for off in (offers if isinstance(offers, list) else [offers]):
                if isinstance(off, dict):
                    price = off.get("price") or off.get("lowPrice") or off.get("highPrice")
                    if price and not found.get("price"):
                        parsed = parse_amount(price)
                        if parsed:
                            found["price"] = parsed
                            if off.get("priceCurrency"):
                                found["currency"] = off["priceCurrency"]
        for v in node.values():
            _walk_jsonld(v, found)
    elif isinstance(node, list):
        for item in node:
            _walk_jsonld(item, found)


def _try_css_selectors(soup, selectors, attr=None):
    """Try multiple CSS selectors, return first match's text or attr."""
    for sel in selectors:
        try:
            el = soup.select_one(sel)
            if el:
                if attr:
                    val = el.get(attr)
                    if val:
                        return val.strip()
                else:
                    text = el.get_text(strip=True)
                    if text:
                        return text
        except Exception:
            continue
    return None


def _try_image_selectors(soup, selectors, base_url=""):
    """Try multiple CSS selectors for images."""
    for sel in selectors:
        try:
            if sel.startswith("meta"):
                tag = soup.select_one(sel)
                if tag and tag.get("content"):
                    return tag["content"].strip()
                continue
            el = soup.select_one(sel)
            if el:
                src = el.get("src") or el.get("data-src") or el.get("data-lazy-src") or el.get("srcset", "").split(",")[0].split(" ")[0]
                if src:
                    src = src.strip()
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = urljoin(base_url, src)
                    return src
        except Exception:
            continue
    return None


def _extract_price_from_scripts(soup):
    """Search all <script> tags for price patterns — catches JS-rendered prices."""
    price_patterns = [
        r'"price"\s*:\s*["\']?([\d.,]+)',
        r'"productPrice"\s*:\s*["\']?([\d.,]+)',
        r'"amount"\s*:\s*["\']?([\d.,]+)',
        r'"salePrice"\s*:\s*["\']?([\d.,]+)',
        r'"displayPrice"\s*:\s*["\']?([\d.,]+)',
        r"price['\"]?\s*[:=]\s*['\"]?([\d.,]+)",
        r'"formattedPrice"\s*:\s*"[^"]*?([\d][,.\d]+)',
        r'"raw"\s*:\s*"([\d.,]+)"',
    ]
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text or len(text) < 10:
            continue
        for pat in price_patterns:
            m = re.search(pat, text)
            if m:
                val = parse_amount(m.group(1))
                if val and val > 100:  # skip tiny numbers that are likely not prices
                    return val
    return None


def _extract_image_from_scripts(soup):
    """Search script tags for image URLs."""
    img_patterns = [
        r'"image"\s*:\s*"(https?://[^"]+)"',
        r'"imageUrl"\s*:\s*"(https?://[^"]+)"',
        r'"productImage"\s*:\s*"(https?://[^"]+)"',
        r'"src"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp))',
    ]
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text:
            continue
        for pat in img_patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1)
    return None


def extract_product_details(url):
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}

    domain = urlparse(url).netloc.lower().replace("www.", "")
    site_patterns = None
    for key in LUXURY_PATTERNS:
        if key in domain:
            site_patterns = LUXURY_PATTERNS[key]
            break

    # Try multiple user agents
    attempts = [
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        },
        {  # Mobile UA fallback — many luxury sites serve simpler mobile pages
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/",
        },
    ]

    soup = None
    for headers in attempts:
        try:
            session = requests.Session()
            # First hit the homepage to get cookies (helps with bot detection)
            home_url = f"https://{urlparse(url).netloc}/"
            try:
                session.get(home_url, headers=headers, timeout=8, allow_redirects=True)
                time.sleep(0.5)
            except Exception:
                pass

            resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 1000:
                soup = BeautifulSoup(resp.content, "html.parser")
                break
            elif resp.status_code == 403:
                continue
        except Exception:
            continue

    if not soup:
        return result

    # ---- LAYER 1: JSON-LD (most reliable) ----
    found = {}
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = json.loads(raw.strip().rstrip(";"))
            except Exception:
                continue
        _walk_jsonld(data, found)
    result.update({k: found[k] for k in ("title", "price", "image", "currency") if found.get(k)})

    # ---- LAYER 2: Open Graph / meta tags ----
    def meta(prop, attr="property"):
        tag = soup.find("meta", {attr: prop})
        return tag.get("content").strip() if tag and tag.get("content") else None

    if not result["title"]:
        result["title"] = meta("og:title") or meta("twitter:title") or (
            soup.title.string.strip() if soup.title and soup.title.string else None
        )
    if not result["image"]:
        result["image"] = meta("og:image:secure_url") or meta("og:image") or meta("twitter:image")
    if not result["price"]:
        pm = meta("product:price:amount") or meta("og:price:amount") or meta("price", "itemprop")
        if pm:
            result["price"] = parse_amount(pm)
        cm = meta("product:price:currency") or meta("og:price:currency")
        if cm and not result["currency"]:
            result["currency"] = cm

    # ---- LAYER 3: Site-specific CSS selectors ----
    if site_patterns:
        if not result["title"]:
            result["title"] = _try_css_selectors(soup, site_patterns["title_selectors"])
        if not result["price"]:
            price_text = _try_css_selectors(soup, site_patterns["price_selectors"])
            if price_text:
                result["price"] = parse_amount(price_text)
                if not result["currency"]:
                    result["currency"] = detect_currency(price_text)
        if not result["image"]:
            result["image"] = _try_image_selectors(soup, site_patterns["image_selectors"], url)

    # ---- LAYER 4: Generic CSS selectors ----
    if not result["title"]:
        result["title"] = _try_css_selectors(soup, GENERIC_TITLE_SELECTORS)
    if not result["price"]:
        price_text = _try_css_selectors(soup, GENERIC_PRICE_SELECTORS)
        if price_text:
            result["price"] = parse_amount(price_text)
            if not result["currency"]:
                result["currency"] = detect_currency(price_text)
    if not result["image"]:
        result["image"] = _try_image_selectors(soup, GENERIC_IMAGE_SELECTORS, url)

    # ---- LAYER 5: Script tag mining (catches JS-rendered data) ----
    if not result["price"]:
        result["price"] = _extract_price_from_scripts(soup)
    if not result["image"]:
        result["image"] = _extract_image_from_scripts(soup)

    # ---- LAYER 6: Last-resort body text scan ----
    if not result["title"]:
        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)[:100]
    if not result["price"]:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:₹|Rs\.?|INR|\$|€|£)\s?([\d][\d.,]{2,})", text)
        if m:
            result["price"] = parse_amount(m.group(1))
            if not result["currency"]:
                result["currency"] = detect_currency(m.group(0))

    # Clean up
    if result["price"] and not result["currency"]:
        result["currency"] = "INR"
    if result["title"]:
        # Clean up title - remove site names, pipes, dashes at end
        result["title"] = re.split(r'\s*[|\-–—]\s*(?:Louis Vuitton|Gucci|Prada|Chanel|Dior|Hermès|Hermes|Burberry|Versace|Fendi|Balenciaga|Bottega Veneta)', result["title"], flags=re.I)[0].strip()
        result["title"] = result["title"][:80]
    if result["title"] or result["price"]:
        result["ok"] = True
    return result


# -----------------------------------------------------------------------------
# 4. SESSION STATE
# -----------------------------------------------------------------------------
if "shopping_list" not in st.session_state:
    items, budget = load_data()
    st.session_state.shopping_list = items
    st.session_state.total_budget = budget

PRIORITIES = ["Must have", "Considering", "Someday"]
_defaults = {
    "f_url": "", "f_name": "", "f_price": 0.0, "f_image": "",
    "f_priority": "Considering", "fetch_note": "",
    "_clear_form": False,
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

if st.session_state._clear_form:
    st.session_state.f_url = ""
    st.session_state.f_name = ""
    st.session_state.f_price = 0.0
    st.session_state.f_image = ""
    st.session_state.f_priority = "Considering"
    st.session_state._clear_form = False

# -----------------------------------------------------------------------------
# 5. STYLING
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500&display=swap');

    :root {
        --taupe: #3D342F;
        --taupe-mid: #6B5E56;
        --taupe-light: #8A7D75;
        --cream: #FAF6F2;
        --cream-deep: #F3ECE4;
        --warm-white: #FDFBF9;
        --blush: #F0DED6;
        --blush-border: #E4CFC5;
        --accent: #C49A8A;
        --accent-deep: #A8766A;
        --rose-warn: #B85C5C;
        --rose-bg: #FBF0EE;
        --green-ok: #7A9E6B;
    }

    .stApp {
        background: var(--warm-white) !important;
    }

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
        color: var(--taupe) !important;
    }

    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 2rem;
        max-width: 1080px;
    }

    footer, #MainMenu, .stDeployButton { display: none !important; visibility: hidden !important; }
    header[data-testid="stHeader"] { background: transparent !important; }

    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--taupe) !important;
        font-weight: 500 !important;
    }

    /* — Sidebar — */
    [data-testid="stSidebar"] {
        background: var(--cream) !important;
        border-right: 1px solid var(--blush-border) !important;
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {
        font-family: 'Playfair Display', serif !important;
        color: var(--taupe) !important;
        font-size: 20px !important;
    }
    [data-testid="stSidebar"] label {
        font-family: 'Inter', sans-serif !important;
        font-weight: 500 !important;
        font-size: 12px !important;
        letter-spacing: 0.06em !important;
        color: var(--taupe-mid) !important;
    }

    /* — Inputs — */
    .stTextInput input, .stNumberInput input {
        border-radius: 8px !important;
        border: 1px solid var(--blush-border) !important;
        background: white !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 14px !important;
        color: var(--taupe) !important;
        padding: 10px 12px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 2px rgba(196, 154, 138, 0.15) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 8px !important;
        border: 1px solid var(--blush-border) !important;
        background: white !important;
    }

    /* — Buttons — */
    .stButton > button {
        border-radius: 8px !important;
        font-family: 'Inter', sans-serif !important;
        font-weight: 500 !important;
        font-size: 13px !important;
        letter-spacing: 0.03em !important;
        transition: all 0.15s ease !important;
        padding: 0.5rem 1rem !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--taupe) !important;
        color: white !important;
        border: none !important;
        box-shadow: none !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--accent-deep) !important;
    }
    .stButton > button[kind="secondary"],
    .stButton > button:not([kind="primary"]) {
        background: white !important;
        color: var(--taupe) !important;
        border: 1px solid var(--blush-border) !important;
    }
    .stButton > button[kind="secondary"]:hover,
    .stButton > button:not([kind="primary"]):hover {
        border-color: var(--accent) !important;
        background: var(--cream) !important;
    }

    /* — Metric cards — */
    .lux-metrics {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 16px;
        margin-bottom: 16px;
    }
    .lux-metric {
        background: var(--cream);
        border-radius: 12px;
        padding: 20px 16px;
        text-align: center;
        border: 1px solid var(--blush-border);
    }
    .lux-metric.warn {
        background: var(--rose-bg);
        border-color: #D4A0A0;
    }
    .lux-metric-label {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--taupe-light);
        margin: 0 0 8px;
    }
    .lux-metric.warn .lux-metric-label { color: var(--rose-warn); }
    .lux-metric-value {
        font-family: 'Playfair Display', serif;
        font-size: 22px;
        font-weight: 500;
        color: var(--taupe);
        margin: 0;
    }
    .lux-metric.warn .lux-metric-value { color: var(--rose-warn); }

    /* — Budget bar — */
    .budget-bar-wrap {
        background: var(--cream);
        border-radius: 12px;
        padding: 16px 20px;
        border: 1px solid var(--blush-border);
        margin-bottom: 24px;
    }
    .budget-bar-header {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 10px;
    }
    .budget-bar-header span:first-child {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--taupe-light);
    }
    .budget-bar-header .pct {
        font-family: 'Playfair Display', serif;
        font-size: 15px;
        color: var(--taupe);
    }
    .budget-track {
        height: 5px;
        border-radius: 5px;
        background: var(--blush);
        overflow: hidden;
    }
    .budget-fill {
        height: 100%;
        border-radius: 5px;
        background: var(--accent);
        transition: width 0.4s ease;
    }
    .budget-fill.over { background: var(--rose-warn); }
    .budget-note {
        font-family: 'Playfair Display', serif;
        font-size: 13px;
        font-style: italic;
        color: var(--taupe-light);
        margin-top: 10px;
    }

    /* — Section label — */
    .section-label {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: 500;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--taupe-light);
        margin: 0 0 14px;
    }

    /* — Product cards — */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 10px !important;
        border: 1px solid var(--blush-border) !important;
        background: var(--cream) !important;
        box-shadow: none !important;
    }
    .prod-thumb {
        width: 56px;
        height: 56px;
        border-radius: 8px;
        overflow: hidden;
        background: var(--blush);
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }
    .prod-thumb img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    .prod-thumb-ph {
        font-size: 18px;
        color: var(--accent);
    }
    .prod-name {
        font-family: 'Playfair Display', serif;
        font-weight: 500;
        font-size: 15px;
        color: var(--taupe);
        text-decoration: none;
        line-height: 1.3;
    }
    a.prod-name:hover { color: var(--accent-deep); }
    .prod-source {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        color: var(--taupe-light);
        margin-top: 2px;
    }
    .prio-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 4px;
        font-family: 'Inter', sans-serif;
        font-size: 10px;
        font-weight: 500;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .prod-price {
        font-family: 'Playfair Display', serif;
        font-weight: 500;
        font-size: 17px;
        color: var(--taupe);
        text-align: right;
    }

    /* — Stylist notes — */
    .stylist-card {
        background: var(--cream);
        border-radius: 12px;
        padding: 18px;
        border: 1px solid var(--blush-border);
    }
    .stylist-card.warn {
        background: var(--rose-bg);
        border-color: #D4A0A0;
    }
    .stylist-text {
        font-family: 'Inter', sans-serif;
        font-size: 13px;
        line-height: 1.7;
        color: var(--taupe-mid);
        margin-bottom: 6px;
    }
    .stylist-card.warn .stylist-text { color: var(--rose-warn); }

    /* — Watermark — */
    .watermark {
        text-align: center;
        padding: 2rem 0 0.5rem;
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        color: var(--taupe-light);
        opacity: 0.5;
        letter-spacing: 0.03em;
    }
    .watermark .heart { color: var(--accent-deep); font-size: 13px; }

    /* — Info box override — */
    .stAlert {
        border-radius: 10px !important;
        border: 1px solid var(--blush-border) !important;
        background: var(--cream) !important;
    }

    hr {
        border: none !important;
        border-top: 1px solid var(--blush-border) !important;
        margin: 0.8rem 0 !important;
    }
</style>
""", unsafe_allow_html=True)


def priority_badge(p):
    palette = {
        "Must have": ("#FBF0EE", "#A85C5C"),
        "Considering": ("#F3EDE8", "#6B5E56"),
        "Someday": ("#EDE8F3", "#7B6B8A"),
    }
    bg, fg = palette.get(p, ("#F3EDE8", "#6B5E56"))
    return f'<span class="prio-badge" style="background:{bg};color:{fg};">{p}</span>'


# -----------------------------------------------------------------------------
# 6. SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### The atelier")

    def _save_budget():
        save_data()

    st.number_input(
        "Wardrobe budget (₹)",
        min_value=0.0, step=5000.0, key="total_budget",
        on_change=_save_budget, format="%.0f",
    )

    st.markdown("---")
    st.markdown("#### Add to collection")

    st.text_input("Product link", key="f_url", placeholder="https://...")

    if st.button("Auto-fill from link", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Paste a link above first."
        elif not url.startswith(("http://", "https://")):
            st.session_state.fetch_note = "Please enter a full URL starting with https://"
        else:
            with st.spinner("Reading product page…"):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]:
                    st.session_state.f_name = res["title"]
                if res["image"]:
                    st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = APPROX_RATES_TO_INR.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = (
                            f"Converted {cur.upper()} {res['price']:,.0f} → ~{fmt_inr(st.session_state.f_price)}. "
                            f"Verify and adjust if needed."
                        )
                    else:
                        st.session_state.fetch_note = "Details found — review and adjust below."
                else:
                    st.session_state.fetch_note = "Found the name but not the price. Enter it manually."
            else:
                st.session_state.fetch_note = (
                    "This site blocked automatic reading. "
                    "Enter the item name, price, and image URL manually."
                )
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="e.g. Prada Re-Edition 2005")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
    st.text_input("Image URL (optional)", key="f_image", placeholder="https://...image.jpg")
    st.selectbox("Priority", PRIORITIES, key="f_priority")

    if st.button("Add item", type="primary", use_container_width=True):
        if st.session_state.f_name.strip() and st.session_state.f_price > 0:
            url = st.session_state.f_url.strip()
            domain = urlparse(url).netloc.replace("www.", "") if url else "Manual entry"
            st.session_state.shopping_list.append({
                "name": st.session_state.f_name.strip(),
                "price": float(st.session_state.f_price),
                "url": url if url else "#",
                "source": domain or "Manual entry",
                "image": st.session_state.f_image.strip(),
                "priority": st.session_state.f_priority,
            })
            save_data()
            st.session_state._clear_form = True
            st.session_state.fetch_note = ""
            st.rerun()
        else:
            st.warning("Please enter both a name and a price.")

# -----------------------------------------------------------------------------
# 7. HEADER
# -----------------------------------------------------------------------------
st.markdown(
    '<div style="text-align:center; margin-bottom:1.2rem;">'
    '<h1 style="font-size:30px; margin:0; letter-spacing:0.03em;">Luxury Shopping Calculator</h1>'
    '<p style="font-family:Inter,sans-serif; font-size:12px; font-weight:300; letter-spacing:0.12em; '
    'text-transform:uppercase; color:#8A7D75; margin-top:4px;">Wardrobe investment planner</p>'
    '</div>',
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 8. BUDGET SUMMARY
# -----------------------------------------------------------------------------
budget = float(st.session_state.total_budget)
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

st.markdown(
    f'<div class="lux-metrics">'
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Total budget</p>'
    f'<p class="lux-metric-value">{fmt_inr(budget)}</p>'
    f'</div>'
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Allocated</p>'
    f'<p class="lux-metric-value">{fmt_inr(total_spent)}</p>'
    f'</div>'
    f'<div class="lux-metric{"  warn" if over else ""}">'
    f'<p class="lux-metric-label">{"Over budget" if over else "Available"}</p>'
    f'<p class="lux-metric-value">{fmt_inr(abs(remaining))}</p>'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

pct = min(total_spent / budget, 1.0) if budget > 0 else 0.0
fill_cls = "over" if over else ""
st.markdown(
    f'<div class="budget-bar-wrap">'
    f'<div class="budget-bar-header">'
    f'<span>Allocation</span>'
    f'<span class="pct">{pct * 100:.0f}%</span>'
    f'</div>'
    f'<div class="budget-track">'
    f'<div class="budget-fill {fill_cls}" style="width:{pct * 100:.1f}%;"></div>'
    f'</div>'
    f'<div class="budget-note">'
    f'{"Over the limit — consider removing a piece to rebalance." if over else "On track — your curation sits comfortably within budget."}'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# 9. COLLECTION + STYLIST NOTES
# -----------------------------------------------------------------------------
col_items, col_notes = st.columns([2.2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    st.markdown(
        f'<p class="section-label">The collection — {count} piece{"s" if count != 1 else ""}</p>',
        unsafe_allow_html=True,
    )

    if not st.session_state.shopping_list:
        st.info("Your collection is empty. Add your first piece from the sidebar.")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            with st.container(border=True):
                ci, cn, cp = st.columns([0.6, 3.2, 1.5])
                with ci:
                    if item.get("image"):
                        st.markdown(
                            f'<div class="prod-thumb">'
                            f'<img src="{item["image"]}" onerror="this.parentElement.innerHTML='
                            f"'<span class=prod-thumb-ph>✦</span>'\"/>"
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            '<div class="prod-thumb"><span class="prod-thumb-ph">✦</span></div>',
                            unsafe_allow_html=True,
                        )
                with cn:
                    if item["url"] != "#":
                        st.markdown(
                            f'<a class="prod-name" href="{item["url"]}" target="_blank">{item["name"]}</a>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            f'<span class="prod-name">{item["name"]}</span>',
                            unsafe_allow_html=True,
                        )
                    st.markdown(
                        f'<div style="margin-top:4px;">{priority_badge(item.get("priority", "Considering"))}'
                        f' <span class="prod-source">{item["source"]}</span></div>',
                        unsafe_allow_html=True,
                    )
                with cp:
                    st.markdown(
                        f'<div class="prod-price">{fmt_inr(item["price"])}</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("Remove", key=f"del_{index}", use_container_width=True):
                        st.session_state.shopping_list.pop(index)
                        save_data()
                        st.rerun()

with col_notes:
    st.markdown('<p class="section-label">Stylist notes</p>', unsafe_allow_html=True)

    if not st.session_state.shopping_list:
        st.markdown(
            '<div class="stylist-card">'
            '<p class="stylist-text">Add pieces to see budget insights here.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
        cheapest = min(st.session_state.shopping_list, key=lambda x: x["price"])
        avg = total_spent / count
        warn_cls = "warn" if over else ""

        notes_html = f'<div class="stylist-card {warn_cls}">'
        if over:
            notes_html += (
                f'<p class="stylist-text"><b>Over budget by {fmt_inr(abs(remaining))}</b></p>'
                f'<p class="stylist-text">Consider pausing on <b>{dearest["name"]}</b> '
                f'({fmt_inr(dearest["price"])}) to rebalance.</p>'
            )
        elif pct > 0.85:
            notes_html += (
                f'<p class="stylist-text">Nearly at capacity — {fmt_inr(remaining)} remaining.</p>'
                f'<p class="stylist-text">Consider holding off before adding more.</p>'
            )
        else:
            notes_html += (
                f'<p class="stylist-text">Your curation is perfectly balanced.</p>'
                f'<p class="stylist-text">{fmt_inr(remaining)} of room remaining.</p>'
            )
        notes_html += '<hr>'
        notes_html += (
            f'<p class="stylist-text" style="font-size:12px;">'
            f'Top piece: <b>{dearest["name"]}</b> — {fmt_inr(dearest["price"])}<br>'
            f'Average: <b>{fmt_inr(avg)}</b> per item<br>'
            f'Items: <b>{count}</b></p>'
        )
        notes_html += '</div>'
        st.markdown(notes_html, unsafe_allow_html=True)

# Watermark
st.markdown(
    '<div class="watermark">Made with <span class="heart">♥</span> by Vansh for Didi Gupta</div>',
    unsafe_allow_html=True,
)
