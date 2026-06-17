import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote_plus
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
                {"items": st.session_state.shopping_list, "budget": st.session_state.total_budget},
                f, ensure_ascii=False, indent=2,
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
# 3. SCRAPER ENGINE
# -----------------------------------------------------------------------------
UA_DESKTOP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
UA_MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
UA_GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

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
                if isinstance(img, list): img = img[0]
                if isinstance(img, dict): img = img.get("url") or img.get("contentUrl")
                if img: found["image"] = str(img)
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


def _extract_from_soup(soup, url):
    """Extract product data from a BeautifulSoup object using all methods."""
    result = {"title": None, "price": None, "image": None, "currency": None}

    # 1) JSON-LD
    found = {}
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        if not raw: continue
        try:
            data = json.loads(raw)
        except Exception:
            try: data = json.loads(raw.strip().rstrip(";"))
            except Exception: continue
        _walk_jsonld(data, found)
    result.update({k: found[k] for k in ("title", "price", "image", "currency") if found.get(k)})

    # 2) Meta tags
    def meta(prop, attr="property"):
        tag = soup.find("meta", {attr: prop})
        return tag.get("content").strip() if tag and tag.get("content") else None

    if not result["title"]:
        result["title"] = meta("og:title") or meta("twitter:title")
    if not result["image"]:
        result["image"] = meta("og:image:secure_url") or meta("og:image") or meta("twitter:image")
    if not result["price"]:
        pm = meta("product:price:amount") or meta("og:price:amount")
        if pm: result["price"] = parse_amount(pm)
        cm = meta("product:price:currency") or meta("og:price:currency")
        if cm: result["currency"] = cm

    # 3) CSS selectors
    price_sels = [
        '[itemprop="price"]', '[data-price]', '.price', '#price',
        'span[class*="price"]', 'div[class*="price"]', 'p[class*="price"]',
        '.product-price', '.product__price', '.pdp-price',
        '.current-price', '.sale-price', '.offer-price',
        '[class*="ProductPrice"]',
    ]
    title_sels = [
        '[itemprop="name"]', 'h1[class*="product"]', 'h1[class*="title"]',
        '.product-name', '.product-title', '.product__title', '.pdp-title',
    ]
    img_sels = [
        '[itemprop="image"]', 'img[class*="product"]', 'img[class*="gallery"]',
        '.product-image img', 'img[class*="main"]', 'img[data-zoom]',
    ]

    if not result["title"]:
        for sel in title_sels:
            try:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    result["title"] = el.get_text(strip=True)
                    break
            except Exception: continue

    if not result["price"]:
        for sel in price_sels:
            try:
                el = soup.select_one(sel)
                if el:
                    txt = el.get("content") or el.get_text(strip=True)
                    p = parse_amount(txt)
                    if p:
                        result["price"] = p
                        if not result["currency"]: result["currency"] = detect_currency(txt)
                        break
            except Exception: continue

    if not result["image"]:
        for sel in img_sels:
            try:
                el = soup.select_one(sel)
                if el:
                    src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                    if src:
                        src = src.strip()
                        if src.startswith("//"): src = "https:" + src
                        elif src.startswith("/"): src = urljoin(url, src)
                        result["image"] = src
                        break
            except Exception: continue

    # 4) Script tag mining
    if not result["price"]:
        for script in soup.find_all("script"):
            text = script.string or ""
            if len(text) < 20: continue
            for pat in [r'"price"\s*:\s*"?([\d.,]+)', r'"amount"\s*:\s*"?([\d.,]+)',
                        r'"salePrice"\s*:\s*"?([\d.,]+)', r'"displayPrice"\s*:\s*"?([\d.,]+)']:
                m = re.search(pat, text)
                if m:
                    val = parse_amount(m.group(1))
                    if val and val > 50:
                        result["price"] = val
                        break
            if result["price"]: break

    if not result["image"]:
        for script in soup.find_all("script"):
            text = script.string or ""
            m = re.search(r'"(?:image|imageUrl|productImage)"\s*:\s*"(https?://[^"]+)"', text)
            if m:
                result["image"] = m.group(1)
                break

    # 5) H1 fallback for title
    if not result["title"]:
        h1 = soup.find("h1")
        if h1: result["title"] = h1.get_text(strip=True)[:100]

    # 6) Body text price scan
    if not result["price"]:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"(?:₹|Rs\.?|INR)\s?([\d][\d.,]{2,})", text)
        if m:
            result["price"] = parse_amount(m.group(1))
            result["currency"] = "INR"
        else:
            m = re.search(r"(?:\$|€|£)\s?([\d][\d.,]{2,})", text)
            if m:
                result["price"] = parse_amount(m.group(1))
                result["currency"] = detect_currency(m.group(0))

    if result["price"] and not result["currency"]:
        result["currency"] = "INR"

    return result


def _fetch_with_strategy(url, ua, extra_headers=None):
    """Try to fetch a URL with given user agent. Returns soup or None."""
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8,hi;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        session = requests.Session()
        # Hit homepage first for cookies
        home = f"https://{urlparse(url).netloc}/"
        try:
            session.get(home, headers=headers, timeout=8, allow_redirects=True)
            time.sleep(0.3)
        except Exception:
            pass
        resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        if resp.status_code == 200 and len(resp.content) > 500:
            return BeautifulSoup(resp.content, "html.parser")
    except Exception:
        pass
    return None


# --- Louis Vuitton specific API handler ---
def _try_lv_api(url):
    """Louis Vuitton has internal APIs. Try to extract product data from them."""
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}

    # Extract SKU from URL (e.g. LP0095 from the URL)
    sku_match = re.search(r'/([A-Z]{1,3}\d{4,6})', url)
    if not sku_match:
        return result
    sku = sku_match.group(1)

    # Determine locale from URL
    locale_match = re.search(r'louisvuitton\.com/(\w{3}-\w{2})/', url)
    locale = locale_match.group(1) if locale_match else "eng-in"

    # Try LV's product API endpoints
    api_urls = [
        f"https://api.louisvuitton.com/eco-eu/search-merch-eapi/v1/{locale}/plp/products/{sku}",
        f"https://api.louisvuitton.com/api/{locale}/catalog/product/{sku}",
    ]

    headers = {
        "User-Agent": UA_DESKTOP,
        "Accept": "application/json",
        "Referer": url,
        "Origin": f"https://{urlparse(url).netloc}",
    }

    for api_url in api_urls:
        try:
            resp = requests.get(api_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Try to extract from various API response shapes
                if isinstance(data, list) and len(data) > 0:
                    data = data[0]
                name = (data.get("name") or data.get("productName") or
                        data.get("model", {}).get("name") if isinstance(data.get("model"), dict) else None)
                if name:
                    result["title"] = str(name).strip()

                # Price extraction from API
                for price_key in ["price", "priceFormatted", "offers"]:
                    if price_key in data:
                        pdata = data[price_key]
                        if isinstance(pdata, dict):
                            p = pdata.get("amount") or pdata.get("value") or pdata.get("price")
                            if p: result["price"] = parse_amount(str(p))
                            cur = pdata.get("currency") or pdata.get("priceCurrency")
                            if cur: result["currency"] = cur
                        elif isinstance(pdata, (int, float)):
                            result["price"] = float(pdata)
                        elif isinstance(pdata, str):
                            result["price"] = parse_amount(pdata)

                # Image from API
                for img_key in ["image", "imageUrl", "productImage", "defaultImage"]:
                    if img_key in data and data[img_key]:
                        img = data[img_key]
                        if isinstance(img, list): img = img[0]
                        if isinstance(img, dict): img = img.get("url") or img.get("contentUrl")
                        if isinstance(img, str):
                            result["image"] = img
                            break

                if result["title"] or result["price"]:
                    result["ok"] = True
                    return result
        except Exception:
            continue

    return result


# --- Google Shopping / search fallback ---
def _try_google_fallback(url):
    """Use Google's cache/search to find product info when the site blocks us."""
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}

    # Search Google for the exact URL
    search_url = f"https://www.google.com/search?q={quote_plus(url)}"
    headers = {
        "User-Agent": UA_DESKTOP,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            text = soup.get_text(" ", strip=True)

            # Try to find price in Google's result
            price_patterns = [
                r"₹\s?([\d][\d.,]{2,})",
                r"Rs\.?\s?([\d][\d.,]{2,})",
                r"INR\s?([\d][\d.,]{2,})",
                r"\$\s?([\d][\d.,]{2,})",
                r"€\s?([\d][\d.,]{2,})",
                r"£\s?([\d][\d.,]{2,})",
            ]
            for pat in price_patterns:
                m = re.search(pat, text)
                if m:
                    val = parse_amount(m.group(1))
                    if val and val > 100:
                        result["price"] = val
                        result["currency"] = detect_currency(m.group(0)) or "INR"
                        break

            # Title from Google result
            h3 = soup.find("h3")
            if h3:
                title = h3.get_text(strip=True)
                # Clean up Google's title additions
                title = re.split(r'\s*[-|–—]\s*(?:Louis Vuitton|Gucci|Prada|Chanel|Dior|Hermès|Hermes|Burberry|Versace|Fendi|Balenciaga|Bottega)', title, flags=re.I)[0].strip()
                if title and len(title) > 3:
                    result["title"] = title[:80]

            if result["title"] or result["price"]:
                result["ok"] = True
    except Exception:
        pass
    return result


def _clean_title(title, url):
    """Remove brand names and site suffixes from product titles."""
    if not title:
        return title
    brands = r'Louis Vuitton|Gucci|Prada|Chanel|Dior|Hermès|Hermes|Burberry|Versace|Fendi|Balenciaga|Bottega Veneta|Cartier|Tiffany|Rolex|Omega|Jimmy Choo|Christian Louboutin|Saint Laurent|YSL|Celine|Valentino|Givenchy|Bvlgari|Bulgari|Tom Ford|Moncler'
    # Remove " | Brand" or " - Brand" suffixes
    title = re.split(rf'\s*[|\-–—:]\s*(?:{brands}|Official)', title, flags=re.I)[0].strip()
    # Remove "Products by Brand:" prefix
    title = re.sub(rf'^(?:Products?\s+by\s+)?(?:{brands})\s*[:|\-–—]\s*', '', title, flags=re.I).strip()
    # Remove "Buy ... online" wrappers
    title = re.sub(r'^Buy\s+', '', title, flags=re.I).strip()
    title = re.sub(r'\s+[|\-]?\s*(?:Buy\s+)?Online.*$', '', title, flags=re.I).strip()
    return title[:80] if title else None


def extract_product_details(url):
    """Master extraction function — tries multiple strategies."""
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}
    domain = urlparse(url).netloc.lower().replace("www.", "")

    # ===== STRATEGY 1: Site-specific APIs =====
    if "louisvuitton.com" in domain:
        api_result = _try_lv_api(url)
        if api_result["ok"]:
            result.update({k: v for k, v in api_result.items() if v and k != "ok"})

    # ===== STRATEGY 2: Direct fetch (desktop UA) =====
    if not result["title"] or not result["price"]:
        soup = _fetch_with_strategy(url, UA_DESKTOP)
        if soup:
            extracted = _extract_from_soup(soup, url)
            for k in ("title", "price", "image", "currency"):
                if extracted.get(k) and not result.get(k):
                    result[k] = extracted[k]

    # ===== STRATEGY 3: Mobile UA (luxury sites serve simpler pages) =====
    if not result["price"]:
        soup = _fetch_with_strategy(url, UA_MOBILE)
        if soup:
            extracted = _extract_from_soup(soup, url)
            for k in ("title", "price", "image", "currency"):
                if extracted.get(k) and not result.get(k):
                    result[k] = extracted[k]

    # ===== STRATEGY 4: Googlebot UA (some sites serve full content) =====
    if not result["price"]:
        soup = _fetch_with_strategy(url, UA_GOOGLEBOT, {"From": "googlebot(at)googlebot.com"})
        if soup:
            extracted = _extract_from_soup(soup, url)
            for k in ("title", "price", "image", "currency"):
                if extracted.get(k) and not result.get(k):
                    result[k] = extracted[k]

    # ===== STRATEGY 5: Google search fallback =====
    if not result["price"] and not result["title"]:
        google_result = _try_google_fallback(url)
        for k in ("title", "price", "image", "currency"):
            if google_result.get(k) and not result.get(k):
                result[k] = google_result[k]

    # ===== Build OG image from known URL patterns =====
    if not result["image"]:
        if "louisvuitton.com" in domain:
            sku_match = re.search(r'/([A-Z]{1,3}\d{4,6})', url)
            if sku_match:
                sku = sku_match.group(1)
                result["image"] = f"https://in.louisvuitton.com/images/is/image/lv/1/PP_VP_L/louis-vuitton--{sku}_PM2_Front%20view.jpg?wid=600&hei=600"

    # Clean title
    result["title"] = _clean_title(result.get("title"), url)

    if result["price"] and not result["currency"]:
        result["currency"] = "INR"
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
    "f_priority": "Considering", "fetch_note": "", "_clear_form": False,
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

if st.session_state._clear_form:
    for k in ("f_url", "f_name", "f_image", "fetch_note"):
        st.session_state[k] = ""
    st.session_state.f_price = 0.0
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
    }

    .stApp { background: var(--warm-white) !important; }
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
        color: var(--taupe) !important;
    }
    .block-container { padding-top: 1.8rem; padding-bottom: 2rem; max-width: 1080px; }
    footer, #MainMenu, .stDeployButton { display: none !important; visibility: hidden !important; }
    header[data-testid="stHeader"] { background: transparent !important; }
    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--taupe) !important;
        font-weight: 500 !important;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background: var(--cream) !important;
        border-right: 1px solid var(--blush-border) !important;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
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

    /* Inputs */
    .stTextInput input, .stNumberInput input {
        border-radius: 8px !important;
        border: 1px solid var(--blush-border) !important;
        background: white !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 14px !important;
        color: var(--taupe) !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 2px rgba(196,154,138,0.15) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 8px !important; border: 1px solid var(--blush-border) !important; background: white !important;
    }

    /* Buttons */
    .stButton > button {
        border-radius: 8px !important;
        font-family: 'Inter', sans-serif !important;
        font-weight: 500 !important;
        font-size: 13px !important;
        transition: all 0.15s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--taupe) !important; color: white !important; border: none !important;
    }
    .stButton > button[kind="primary"]:hover { background: var(--accent-deep) !important; }
    .stButton > button[kind="secondary"], .stButton > button:not([kind="primary"]) {
        background: white !important; color: var(--taupe) !important; border: 1px solid var(--blush-border) !important;
    }
    .stButton > button[kind="secondary"]:hover, .stButton > button:not([kind="primary"]):hover {
        border-color: var(--accent) !important; background: var(--cream) !important;
    }

    /* Metric cards */
    .lux-metrics { display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; margin-bottom: 16px; }
    .lux-metric {
        background: var(--cream); border-radius: 12px; padding: 20px 16px;
        text-align: center; border: 1px solid var(--blush-border);
    }
    .lux-metric.warn { background: var(--rose-bg); border-color: #D4A0A0; }
    .lux-metric-label {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--taupe-light); margin: 0 0 8px;
    }
    .lux-metric.warn .lux-metric-label { color: var(--rose-warn); }
    .lux-metric-value {
        font-family: 'Playfair Display', serif; font-size: 22px;
        font-weight: 500; color: var(--taupe); margin: 0;
    }
    .lux-metric.warn .lux-metric-value { color: var(--rose-warn); }

    /* Budget bar */
    .budget-bar-wrap {
        background: var(--cream); border-radius: 12px;
        padding: 16px 20px; border: 1px solid var(--blush-border); margin-bottom: 24px;
    }
    .budget-bar-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
    .budget-bar-header span:first-child {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.1em; text-transform: uppercase; color: var(--taupe-light);
    }
    .budget-bar-header .pct { font-family: 'Playfair Display', serif; font-size: 15px; color: var(--taupe); }
    .budget-track { height: 5px; border-radius: 5px; background: var(--blush); overflow: hidden; }
    .budget-fill { height: 100%; border-radius: 5px; background: var(--accent); transition: width 0.4s ease; }
    .budget-fill.over { background: var(--rose-warn); }
    .budget-note {
        font-family: 'Playfair Display', serif; font-size: 13px;
        font-style: italic; color: var(--taupe-light); margin-top: 10px;
    }

    /* Section label */
    .section-label {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--taupe-light); margin: 0 0 14px;
    }

    /* Product cards */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 10px !important; border: 1px solid var(--blush-border) !important;
        background: var(--cream) !important; box-shadow: none !important;
    }
    .prod-thumb {
        width: 56px; height: 56px; border-radius: 8px; overflow: hidden;
        background: var(--blush); display: flex; align-items: center;
        justify-content: center; flex-shrink: 0;
    }
    .prod-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .prod-thumb-ph { font-size: 18px; color: var(--accent); }
    .prod-name {
        font-family: 'Playfair Display', serif; font-weight: 500;
        font-size: 15px; color: var(--taupe); text-decoration: none; line-height: 1.3;
    }
    a.prod-name:hover { color: var(--accent-deep); }
    .prod-source { font-family: 'Inter'; font-size: 11px; color: var(--taupe-light); margin-top: 2px; }
    .prio-badge {
        display: inline-block; padding: 2px 10px; border-radius: 4px;
        font-family: 'Inter'; font-size: 10px; font-weight: 500;
        letter-spacing: 0.04em; text-transform: uppercase;
    }
    .prod-price {
        font-family: 'Playfair Display', serif; font-weight: 500;
        font-size: 17px; color: var(--taupe); text-align: right;
    }

    /* Stylist notes */
    .stylist-card {
        background: var(--cream); border-radius: 12px;
        padding: 18px; border: 1px solid var(--blush-border);
    }
    .stylist-card.warn { background: var(--rose-bg); border-color: #D4A0A0; }
    .stylist-text {
        font-family: 'Inter'; font-size: 13px; line-height: 1.7;
        color: var(--taupe-mid); margin-bottom: 6px;
    }
    .stylist-card.warn .stylist-text { color: var(--rose-warn); }

    /* Watermark */
    .watermark {
        text-align: center; padding: 2rem 0 0.5rem;
        font-family: 'Inter'; font-size: 12px;
        color: var(--taupe-light); opacity: 0.45; letter-spacing: 0.03em;
    }
    .watermark .heart { color: var(--accent-deep); }

    .stAlert { border-radius: 10px !important; }
    hr { border: none !important; border-top: 1px solid var(--blush-border) !important; margin: 0.8rem 0 !important; }
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

    def _save_budget(): save_data()

    st.number_input("Wardrobe budget (₹)", min_value=0.0, step=5000.0,
                     key="total_budget", on_change=_save_budget, format="%.0f")
    st.markdown("---")
    st.markdown("#### Add to collection")

    st.text_input("Product link", key="f_url", placeholder="https://...")

    if st.button("Auto-fill from link", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Paste a link above first."
        elif not url.startswith(("http://", "https://")):
            st.session_state.fetch_note = "Enter a full URL starting with https://"
        else:
            with st.spinner("Reading product page…"):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]: st.session_state.f_name = res["title"]
                if res["image"]: st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = APPROX_RATES_TO_INR.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = f"Converted {cur.upper()} {res['price']:,.0f} → ~{fmt_inr(st.session_state.f_price)}. Verify the rate."
                    else:
                        st.session_state.fetch_note = "Details found — review and adjust below."
                else:
                    st.session_state.fetch_note = "Found the name but not the price. Enter it manually."
            else:
                st.session_state.fetch_note = "Could not read this page. Enter the details manually below."
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="e.g. Ombre Nomade 100ml")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
    st.text_input("Image URL (optional)", key="f_image",
                   placeholder="Right-click product image → Copy image address")
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
# 7. MAIN CONTENT
# -----------------------------------------------------------------------------
st.markdown(
    '<div style="text-align:center; margin-bottom:1.2rem;">'
    '<h1 style="font-size:30px; margin:0; letter-spacing:0.03em;">Luxury Shopping Calculator</h1>'
    '<p style="font-family:Inter,sans-serif; font-size:12px; font-weight:300; letter-spacing:0.12em; '
    'text-transform:uppercase; color:#8A7D75; margin-top:4px;">Wardrobe investment planner</p>'
    '</div>',
    unsafe_allow_html=True,
)

budget = float(st.session_state.total_budget)
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

st.markdown(
    f'<div class="lux-metrics">'
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Total budget</p>'
    f'<p class="lux-metric-value">{fmt_inr(budget)}</p></div>'
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Allocated</p>'
    f'<p class="lux-metric-value">{fmt_inr(total_spent)}</p></div>'
    f'<div class="lux-metric{" warn" if over else ""}">'
    f'<p class="lux-metric-label">{"Over budget" if over else "Available"}</p>'
    f'<p class="lux-metric-value">{fmt_inr(abs(remaining))}</p></div>'
    f'</div>',
    unsafe_allow_html=True,
)

pct = min(total_spent / budget, 1.0) if budget > 0 else 0.0
st.markdown(
    f'<div class="budget-bar-wrap">'
    f'<div class="budget-bar-header"><span>Allocation</span>'
    f'<span class="pct">{pct*100:.0f}%</span></div>'
    f'<div class="budget-track">'
    f'<div class="budget-fill {"over" if over else ""}" style="width:{pct*100:.1f}%;"></div></div>'
    f'<div class="budget-note">'
    f'{"Over the limit — consider removing a piece to rebalance." if over else "On track — your curation sits comfortably within budget."}'
    f'</div></div>',
    unsafe_allow_html=True,
)

# Collection + Notes
col_items, col_notes = st.columns([2.2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    st.markdown(f'<p class="section-label">The collection — {count} piece{"s" if count != 1 else ""}</p>',
                unsafe_allow_html=True)
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
                            f'<img src="{item["image"]}" onerror="this.style.display=\'none\';'
                            f'this.parentElement.innerHTML=\'<span class=prod-thumb-ph>✦</span>\'"/>'
                            f'</div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="prod-thumb"><span class="prod-thumb-ph">✦</span></div>',
                                    unsafe_allow_html=True)
                with cn:
                    if item["url"] != "#":
                        st.markdown(f'<a class="prod-name" href="{item["url"]}" target="_blank">{item["name"]}</a>',
                                    unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="prod-name">{item["name"]}</span>', unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="margin-top:4px;">{priority_badge(item.get("priority","Considering"))}'
                        f' <span class="prod-source">{item["source"]}</span></div>',
                        unsafe_allow_html=True)
                with cp:
                    st.markdown(f'<div class="prod-price">{fmt_inr(item["price"])}</div>', unsafe_allow_html=True)
                    if st.button("Remove", key=f"del_{index}", use_container_width=True):
                        st.session_state.shopping_list.pop(index)
                        save_data()
                        st.rerun()

with col_notes:
    st.markdown('<p class="section-label">Stylist notes</p>', unsafe_allow_html=True)
    if not st.session_state.shopping_list:
        st.markdown('<div class="stylist-card"><p class="stylist-text">Add pieces to see budget insights.</p></div>',
                    unsafe_allow_html=True)
    else:
        dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
        avg = total_spent / count
        warn_cls = "warn" if over else ""
        notes = f'<div class="stylist-card {warn_cls}">'
        if over:
            notes += (f'<p class="stylist-text"><b>Over budget by {fmt_inr(abs(remaining))}</b></p>'
                      f'<p class="stylist-text">Consider pausing on <b>{dearest["name"]}</b> '
                      f'({fmt_inr(dearest["price"])}) to rebalance.</p>')
        elif pct > 0.85:
            notes += (f'<p class="stylist-text">Nearly at capacity — {fmt_inr(remaining)} remaining.</p>'
                      f'<p class="stylist-text">Consider holding off before adding more.</p>')
        else:
            notes += (f'<p class="stylist-text">Your curation is perfectly balanced.</p>'
                      f'<p class="stylist-text">{fmt_inr(remaining)} of room remaining.</p>')
        notes += '<hr>'
        notes += (f'<p class="stylist-text" style="font-size:12px;">'
                  f'Top piece: <b>{dearest["name"]}</b> — {fmt_inr(dearest["price"])}<br>'
                  f'Average: <b>{fmt_inr(avg)}</b> per item<br>'
                  f'Items: <b>{count}</b></p></div>')
        st.markdown(notes, unsafe_allow_html=True)

# Watermark
st.markdown(
    '<div class="watermark">Made with <span class="heart">♥</span> by Vansh for Didi Gupta</div>',
    unsafe_allow_html=True,
)
