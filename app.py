import streamlit as st
import re
import json
import os
from urllib.parse import urlparse, urljoin, quote_plus
from bs4 import BeautifulSoup
import requests

st.set_page_config(
    page_title="Luxury Shopping Calculator",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)
DATA_FILE = "wishlist_data.json"

# =============================================================================
# 1. PERSISTENCE
# =============================================================================
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
            json.dump({"items": st.session_state.shopping_list, "budget": st.session_state.total_budget},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# =============================================================================
# 2. CURRENCY
# =============================================================================
RATES = {"INR":1.0, "USD":86.0, "EUR":93.0, "GBP":109.0, "AED":23.0,
         "JPY":0.57, "CNY":12.0, "SGD":64.0, "CAD":62.0, "AUD":56.0, "CHF":98.0}
CUR_SYM = {"₹":"INR", "$":"USD", "€":"EUR", "£":"GBP", "¥":"JPY"}

def fmt_inr(n):
    n = round(abs(n))
    s = str(n)
    if len(s) <= 3: return f"₹ {s}"
    result = s[-3:]; s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result; s = s[:-2]
    if s: result = s + "," + result
    return f"₹ {result}"

def parse_amount(raw):
    s = re.sub(r"[^\d.,]", "", str(raw)).strip(".,")
    if not s: return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."): s = s.replace(".", "").replace(",", ".")
        else: s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 2: s = s.replace(",", ".")
        else: s = s.replace(",", "")
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None

def detect_currency(text):
    for sym, code in CUR_SYM.items():
        if sym in text: return code
    m = re.search(r"\b(INR|USD|EUR|GBP|AED|JPY|CNY|SGD|CAD|AUD|CHF|Rs)\b", text, re.I)
    if m:
        c = m.group(1).upper()
        return "INR" if c == "RS" else c
    return None

# =============================================================================
# 3. SCRAPER — fail fast, smart strategy per site
# =============================================================================
BRANDS_RE = r'Louis Vuitton|Gucci|Prada|Chanel|Dior|Herm[eè]s|Burberry|Versace|Fendi|Balenciaga|Bottega Veneta|Cartier|Tiffany|Jimmy Choo|Christian Louboutin|Saint Laurent|YSL|Celine|Valentino|Givenchy|Bvlgari|Tom Ford|Moncler|TataCLiQ|Tata CLiQ|Myntra|Ajio|Nykaa|Official'

# Sites that need premium + JS rendering (heavy bot protection)
AKAMAI_SITES = ["louisvuitton.com", "chanel.com", "hermes.com", "dior.com",
                "gucci.com", "prada.com", "bottegaveneta.com", "ysl.com",
                "burberry.com", "valentino.com", "saintlaurent.com"]
# Indian sites that need render but not premium
INDIAN_RENDER_SITES = ["tatacliq.com", "myntra.com", "ajio.com", "nykaa.com",
                       "tatacliqluxury.com", "ajiomania.com"]


def _get_scraper_api_key():
    key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if not key:
        try: key = st.secrets.get("SCRAPER_API_KEY", "").strip()
        except Exception: pass
    return key if key else None


def _classify_site(url):
    """Return ('premium'|'render'|'simple', country_code)."""
    domain = urlparse(url).netloc.lower().replace("www.", "")
    if any(s in domain for s in AKAMAI_SITES):
        return ("premium", "in" if ".in" in domain else "us")
    if any(s in domain for s in INDIAN_RENDER_SITES):
        return ("render", "in")
    if ".in" in domain or domain.endswith(".in"):
        return ("simple", "in")
    return ("simple", "us")


def _try_scraperapi(url, mode, country, timeout=45):
    """Returns (html, status). Status is 'ok', 'invalid_key', 'no_credits', 'rate_limited', or error string."""
    api_key = _get_scraper_api_key()
    if not api_key: return None, "no_key"
    params = {"api_key": api_key, "url": url, "country_code": country}
    if mode == "premium":
        params["render"] = "true"
        params["premium"] = "true"
    elif mode == "render":
        params["render"] = "true"
    # simple mode = no params, fastest
    try:
        resp = requests.get("https://api.scraperapi.com/", params=params, timeout=timeout)
        if resp.status_code == 200 and len(resp.text) > 300:
            return resp.text, "ok"
        elif resp.status_code == 401: return None, "invalid_key"
        elif resp.status_code == 403: return None, "no_credits"
        elif resp.status_code == 429: return None, "rate_limited"
        elif resp.status_code == 500: return None, "scrape_failed"
        else: return None, f"http_{resp.status_code}"
    except requests.Timeout: return None, "timeout"
    except Exception as e: return None, f"error_{type(e).__name__}"


def _walk_jsonld(node, found):
    if isinstance(node, dict):
        types = " ".join(node.get("@type", [])) if isinstance(node.get("@type"), list) else str(node.get("@type", ""))
        if "product" in types.lower():
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
                        p = parse_amount(price)
                        if p:
                            found["price"] = p
                            if off.get("priceCurrency"): found["currency"] = off["priceCurrency"]
        for v in node.values(): _walk_jsonld(v, found)
    elif isinstance(node, list):
        for i in node: _walk_jsonld(i, found)


INDIAN_PRICE_KEYS = [
    "sellingPrice", "finalPrice", "discountedPrice", "offerPrice",
    "currentPrice", "salePrice", "displayPrice", "mrp", "MRP",
    "actualPrice", "listPrice", "amount", "value", "price"
]

def _extract_from_html(html_str, url):
    soup = BeautifulSoup(html_str, "html.parser")
    r = {"title": None, "price": None, "image": None, "currency": None}

    # JSON-LD
    found = {}
    for sc in soup.find_all("script", type="application/ld+json"):
        raw = sc.string or sc.get_text()
        if not raw: continue
        try: data = json.loads(raw)
        except Exception:
            try: data = json.loads(raw.strip().rstrip(";"))
            except Exception: continue
        _walk_jsonld(data, found)
    r.update({k: found[k] for k in ("title","price","image","currency") if found.get(k)})

    # Meta tags
    def meta(prop, attr="property"):
        tag = soup.find("meta", {attr: prop})
        return tag.get("content","").strip() if tag and tag.get("content") else None

    if not r["title"]: r["title"] = meta("og:title") or meta("twitter:title")
    if not r["image"]: r["image"] = meta("og:image:secure_url") or meta("og:image") or meta("twitter:image")
    if not r["price"]:
        pm = meta("product:price:amount") or meta("og:price:amount")
        if pm: r["price"] = parse_amount(pm)
        cm = meta("product:price:currency") or meta("og:price:currency")
        if cm: r["currency"] = cm

    # CSS selectors
    for sel in ['[itemprop="price"]', '.price', '#price',
                'span[class*="price"]', 'div[class*="price"]', 'p[class*="price"]',
                '.product-price', '.product__price', '[data-price]', '.pdp-price',
                '.current-price', '.sale-price', '.selling-price',
                '[class*="ProductPrice"]', '[class*="SellingPrice"]',
                '[class*="PriceWidget"]', '[class*="PriceBlock"]']:
        if r["price"]: break
        try:
            for el in soup.select(sel)[:3]:
                txt = el.get("content") or el.get_text(strip=True)
                p = parse_amount(txt)
                if p and p > 50:
                    r["price"] = p
                    if not r["currency"]: r["currency"] = detect_currency(txt)
                    break
        except Exception: continue

    for sel in ['[itemprop="name"]', 'h1[class*="product"]', '.product-name', '.product-title']:
        if r["title"]: break
        try:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True): r["title"] = el.get_text(strip=True)
        except Exception: continue

    if not r["image"]:
        for sel in ['[itemprop="image"]', 'img[class*="product"]', 'img[class*="gallery"]',
                     '.product-image img', '[class*="ProductImage"] img']:
            try:
                el = soup.select_one(sel)
                if el:
                    src = el.get("src") or el.get("data-src") or el.get("data-lazy-src")
                    if src:
                        src = src.strip()
                        if src.startswith("//"): src = "https:" + src
                        elif src.startswith("/"): src = urljoin(url, src)
                        r["image"] = src; break
            except Exception: continue

    # Script mining
    if not r["price"]:
        for sc in soup.find_all("script"):
            txt = sc.string or ""
            if len(txt) < 20: continue
            for key in INDIAN_PRICE_KEYS:
                m = re.search(rf'"{key}"\s*:\s*"?([\d.,]+)', txt)
                if m:
                    v = parse_amount(m.group(1))
                    if v and 50 < v < 10000000:
                        r["price"] = v
                        break
            if r["price"]: break

    if not r["title"]:
        h1 = soup.find("h1")
        if h1: r["title"] = h1.get_text(strip=True)[:100]

    if not r["price"]:
        text = soup.get_text(" ", strip=True)
        for pat in [r"₹\s*([\d][\d,]+(?:\.\d+)?)", r"Rs\.?\s*([\d][\d,]+(?:\.\d+)?)"]:
            m = re.search(pat, text)
            if m:
                v = parse_amount(m.group(1))
                if v and 50 < v < 10000000:
                    r["price"] = v; r["currency"] = "INR"; break

    if r["price"] and not r["currency"]: r["currency"] = "INR"
    return r


def _clean_title(title):
    if not title: return title
    title = re.split(rf'\s*[|\-–—:]\s*(?:{BRANDS_RE})', title, flags=re.I)[0].strip()
    title = re.sub(r'^Buy\s+', '', title, flags=re.I).strip()
    title = re.sub(r'\s+(?:Online|at Best Price).*$', '', title, flags=re.I).strip()
    if title and " | " in title: title = title.split(" | ")[0].strip()
    return title[:80] if title else None


def extract_product_details(url):
    """Smart scraper — picks the right strategy per site. Fails fast on errors."""
    result = {"title": None, "price": None, "image": None, "currency": None,
              "ok": False, "method": None, "error": None}
    domain = urlparse(url).netloc.lower().replace("www.", "")
    mode, country = _classify_site(url)

    api_key = _get_scraper_api_key()

    # Try ScraperAPI if key exists
    if api_key:
        # Premium for Akamai sites takes 20-40s, render takes 8-15s, simple takes 3-5s
        timeout = 50 if mode == "premium" else (20 if mode == "render" else 12)
        html, status = _try_scraperapi(url, mode, country, timeout=timeout)

        if status == "invalid_key":
            result["error"] = "Your ScraperAPI key is invalid. Update it in Streamlit Cloud → Settings → Secrets."
            return result
        if status == "no_credits":
            result["error"] = "ScraperAPI credits exhausted. Resets next month."
            return result
        if status == "rate_limited":
            result["error"] = "ScraperAPI rate limit hit. Wait 30 seconds and try again."
            return result
        if status == "timeout":
            result["error"] = f"Scraper timed out. The site may be very slow today — try again or enter manually."
            return result

        if html:
            extracted = _extract_from_html(html, url)
            for k in ("title", "price", "image", "currency"):
                if extracted.get(k): result[k] = extracted[k]
            result["method"] = mode

    # If no API key OR ScraperAPI didn't get us a price, try direct (only for non-Akamai sites)
    if not result["price"] and mode != "premium":
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"}
            resp = requests.get(url, headers=headers, timeout=8, allow_redirects=True)
            if resp.status_code == 200 and len(resp.text) > 1000:
                extracted = _extract_from_html(resp.text, url)
                for k in ("title", "price", "image", "currency"):
                    if extracted.get(k) and not result.get(k): result[k] = extracted[k]
                if not result["method"]: result["method"] = "direct"
        except Exception: pass

    # LV image from SKU
    if not result["image"] and "louisvuitton.com" in domain:
        sku = re.search(r'/([A-Z]{1,3}\d{4,6})', url)
        if sku:
            result["image"] = f"https://in.louisvuitton.com/images/is/image/lv/1/PP_VP_L/louis-vuitton--{sku.group(1)}_PM2_Front%20view.jpg?wid=400&hei=400"

    result["title"] = _clean_title(result.get("title"))
    if result["price"] and not result["currency"]: result["currency"] = "INR"
    result["ok"] = bool(result["title"] or result["price"])
    return result


# =============================================================================
# 4. SESSION STATE
# =============================================================================
if "shopping_list" not in st.session_state:
    items, budget = load_data()
    st.session_state.shopping_list = items
    st.session_state.total_budget = budget

PRIORITIES = ["Must have", "Considering", "Someday"]
for k, v in {"f_url":"", "f_name":"", "f_price":0.0, "f_image":"",
             "f_priority":"Considering", "fetch_note":"", "_clear_form":False}.items():
    st.session_state.setdefault(k, v)

if st.session_state._clear_form:
    for k in ("f_url","f_name","f_image","fetch_note"): st.session_state[k] = ""
    st.session_state.f_price = 0.0
    st.session_state.f_priority = "Considering"
    st.session_state._clear_form = False

# =============================================================================
# 5. STYLING
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Inter:wght@400;500;600&display=swap');

    :root {
        --ink: #1F1814; --ink-mid: #3D3530; --ink-soft: #5A4F47;
        --cream: #F7F2EC; --warm-bg: #FCF8F4;
        --blush: #ECD8CD; --border: #DCC9BE; --border-soft: #E8D9CE;
        --accent: #B07A6A; --accent-deep: #8B5949;
        --rose: #A83838; --rose-bg: #FAEAE8;
        --green: #4A6638; --green-bg: #EEF3E5;
    }

    .stApp { background: var(--warm-bg) !important; }
    html { font-size: 16px; }
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
        color: var(--ink) !important;
    }
    .block-container { padding: 1.5rem 1.25rem 2rem !important; max-width: 1080px; }
    footer, #MainMenu, .stDeployButton { display: none !important; visibility: hidden !important; }
    header[data-testid="stHeader"] { background: transparent !important; }

    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--ink) !important; font-weight: 500 !important;
    }

    [data-testid="stSidebar"] {
        background: var(--cream) !important;
        border-right: 1px solid var(--border) !important;
    }
    [data-testid="stSidebar"] > div { padding: 1.5rem 1.25rem !important; }
    [data-testid="stSidebar"] h3 {
        font-family: 'Playfair Display', serif !important;
        font-size: 24px !important; color: var(--ink) !important;
        font-weight: 600 !important; margin-bottom: 8px !important;
    }
    [data-testid="stSidebar"] h4 {
        font-family: 'Inter' !important; font-size: 13px !important;
        font-weight: 600 !important; letter-spacing: 0.08em !important;
        text-transform: uppercase !important; color: var(--ink-mid) !important;
        margin: 16px 0 8px !important;
    }
    [data-testid="stSidebar"] label {
        font-family: 'Inter' !important; font-weight: 600 !important;
        font-size: 14px !important; color: var(--ink-mid) !important;
    }
    [data-testid="stSidebar"] .stCaption {
        font-size: 13px !important; color: var(--ink-mid) !important;
    }

    .stTextInput input, .stNumberInput input {
        border-radius: 10px !important; border: 1px solid var(--border) !important;
        background: white !important; font-size: 16px !important;
        color: var(--ink) !important; padding: 12px 14px !important;
        min-height: 44px !important;
    }
    .stTextInput input::placeholder, .stNumberInput input::placeholder { color: #9A8A80 !important; }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(176,122,106,0.18) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 10px !important; border: 1px solid var(--border) !important;
        background: white !important; min-height: 44px !important; font-size: 16px !important;
    }

    .stButton > button {
        border-radius: 10px !important; font-family: 'Inter' !important;
        font-weight: 600 !important; font-size: 15px !important;
        padding: 12px 18px !important; min-height: 46px !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--ink) !important; color: white !important;
        border: none !important; box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--accent-deep) !important;
        transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.08);
    }
    .stButton > button:not([kind="primary"]) {
        background: white !important; color: var(--ink) !important;
        border: 1px solid var(--border) !important;
    }
    .stButton > button:not([kind="primary"]):hover {
        border-color: var(--accent) !important; background: var(--cream) !important;
    }

    .lux-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }
    .lux-metric {
        background: white; border-radius: 16px; padding: 22px 18px;
        text-align: center; border: 1px solid var(--border);
        box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    }
    .lux-metric.warn { background: var(--rose-bg); border-color: #D9A2A2; }
    .m-label {
        font-family: 'Inter'; font-size: 12px; font-weight: 600;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--ink-mid); margin: 0 0 10px;
    }
    .lux-metric.warn .m-label { color: var(--rose); }
    .m-val {
        font-family: 'Playfair Display', serif; font-size: 28px; font-weight: 600;
        color: var(--ink); margin: 0; line-height: 1.1;
    }
    .lux-metric.warn .m-val { color: var(--rose); }

    .bar-wrap {
        background: white; border-radius: 16px; padding: 18px 22px;
        border: 1px solid var(--border); margin-bottom: 22px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    }
    .bar-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
    .bar-head span:first-child {
        font-family: 'Inter'; font-size: 12px; font-weight: 600;
        letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mid);
    }
    .bar-head .pct { font-family: 'Playfair Display', serif; font-size: 18px; color: var(--ink); font-weight: 600; }
    .bar-track { height: 8px; border-radius: 8px; background: var(--blush); overflow: hidden; }
    .bar-fill { height: 100%; border-radius: 8px; background: var(--accent); transition: width 0.4s ease; }
    .bar-fill.over { background: var(--rose); }
    .bar-note { font-family: 'Inter'; font-size: 14px; color: var(--ink-mid); margin-top: 12px; line-height: 1.5; }

    .sec-label {
        font-family: 'Inter'; font-size: 12px; font-weight: 600;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--ink-mid); margin: 0 0 14px;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important; border: 1px solid var(--border) !important;
        background: white !important; box-shadow: 0 1px 3px rgba(0,0,0,0.03) !important;
    }
    .thumb {
        width: 60px; height: 60px; border-radius: 10px; overflow: hidden;
        background: var(--blush); display: flex; align-items: center;
        justify-content: center; flex-shrink: 0;
    }
    .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .thumb-ph { font-size: 18px; color: var(--accent); }
    .p-name {
        font-family: 'Playfair Display', serif; font-weight: 600;
        font-size: 16px; color: var(--ink); text-decoration: none; line-height: 1.3;
    }
    a.p-name:hover { color: var(--accent-deep); }
    .p-src { font-family: 'Inter'; font-size: 12px; color: var(--ink-soft); margin-top: 4px; }
    .badge {
        display: inline-block; padding: 3px 10px; border-radius: 6px;
        font-family: 'Inter'; font-size: 11px; font-weight: 600;
        letter-spacing: 0.04em; text-transform: uppercase;
    }
    .p-price {
        font-family: 'Playfair Display', serif; font-weight: 600;
        font-size: 19px; color: var(--ink); text-align: right;
    }

    .notes-card {
        background: white; border-radius: 14px; padding: 20px;
        border: 1px solid var(--border); box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    }
    .notes-card.warn { background: var(--rose-bg); border-color: #D9A2A2; }
    .n-text {
        font-family: 'Inter'; font-size: 14px; line-height: 1.7;
        color: var(--ink-mid); margin-bottom: 8px;
    }
    .notes-card.warn .n-text { color: var(--rose); }

    .api-status {
        font-family: 'Inter'; font-size: 13px; font-weight: 500;
        padding: 8px 12px; border-radius: 8px; margin-bottom: 12px;
    }
    .api-status.on { background: var(--green-bg); color: var(--green); border: 1px solid #C5D5B3; }
    .api-status.off { background: var(--rose-bg); color: var(--rose); border: 1px solid #D9A2A2; }

    .watermark { text-align: center; padding: 3rem 0 1rem; font-family: 'Inter'; font-size: 13px; color: var(--ink-soft); opacity: 0.6; }
    .watermark .h { color: var(--accent-deep); font-size: 14px; }

    .stAlert { border-radius: 12px !important; font-size: 14px !important; }
    hr { border: none !important; border-top: 1px solid var(--border-soft) !important; margin: 1rem 0 !important; }

    @media (max-width: 768px) {
        .block-container { padding: 1rem 0.75rem 1.5rem !important; }
        .lux-metrics { grid-template-columns: 1fr !important; gap: 10px !important; }
        .lux-metric { padding: 18px 16px !important; }
        .m-val { font-size: 24px !important; }
        .m-label { font-size: 11px !important; margin-bottom: 6px !important; }
        h1 { font-size: 26px !important; }
        .bar-wrap { padding: 16px 18px !important; }
        .bar-head .pct { font-size: 16px !important; }
        .bar-note { font-size: 13px !important; }
        .p-name { font-size: 15px !important; }
        .p-price { font-size: 17px !important; }
        .thumb { width: 50px; height: 50px; }
    }
    @media (max-width: 480px) {
        h1 { font-size: 23px !important; }
        .m-val { font-size: 22px !important; }
        .lux-metric { padding: 16px 14px !important; }
    }
    @media (max-width: 640px) {
        [data-testid="column"] { padding: 0 4px !important; }
    }
</style>
""", unsafe_allow_html=True)


def pbadge(p):
    pal = {"Must have": ("#FAEAE8","#8B2828"),
           "Considering": ("#EDE6DD","#4A3E33"),
           "Someday": ("#E6E1F0","#4A3D6B")}
    bg, fg = pal.get(p, ("#EDE6DD","#4A3E33"))
    return f'<span class="badge" style="background:{bg};color:{fg};">{p}</span>'


# =============================================================================
# 6. SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### The atelier")

    has_key = bool(_get_scraper_api_key())
    if has_key:
        st.markdown('<div class="api-status on">✓ Premium scraping enabled</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="api-status off">⚠ Premium scraping not configured</div>', unsafe_allow_html=True)
        with st.expander("How to enable luxury site scraping"):
            st.markdown("""
1. Sign up at **scraperapi.com**
2. Copy your API key
3. Streamlit Cloud → app Settings → Secrets
4. Add: `SCRAPER_API_KEY = "your-key"`
            """)

    def _sb(): save_data()
    st.number_input("Wardrobe budget (₹)", min_value=0.0, step=5000.0,
                     key="total_budget", on_change=_sb, format="%.0f")
    st.markdown("---")
    st.markdown("#### Add to collection")
    st.text_input("Product link", key="f_url", placeholder="https://...")

    if st.button("Auto-fill from link", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Paste a link above first."
        elif not url.startswith(("http://","https://")):
            st.session_state.fetch_note = "Enter a full URL starting with https://"
        else:
            mode, _ = _classify_site(url)
            wait_msg = {
                "premium": "Reading luxury site (20-45 sec)…",
                "render": "Reading product page (8-15 sec)…",
                "simple": "Reading product page (5-10 sec)…",
            }[mode]
            with st.spinner(wait_msg):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]: st.session_state.f_name = res["title"]
                if res["image"]: st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = RATES.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = f"✓ Converted {cur.upper()} {res['price']:,.0f} → ~{fmt_inr(st.session_state.f_price)}"
                    else:
                        st.session_state.fetch_note = "✓ Details found — review below."
                else:
                    st.session_state.fetch_note = "Got name but no price. Enter the price manually."
            elif res.get("error"):
                st.session_state.fetch_note = res["error"]
            else:
                st.session_state.fetch_note = "Could not read this page. Enter details manually."
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="e.g. Ombre Nomade 100ml")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
    st.text_input("Image URL (optional)", key="f_image",
                   placeholder="Right-click image → Copy image address")
    st.selectbox("Priority", PRIORITIES, key="f_priority")

    if st.button("Add item", type="primary", use_container_width=True):
        if st.session_state.f_name.strip() and st.session_state.f_price > 0:
            url = st.session_state.f_url.strip()
            domain = urlparse(url).netloc.replace("www.", "") if url else "Manual"
            st.session_state.shopping_list.append({
                "name": st.session_state.f_name.strip(),
                "price": float(st.session_state.f_price),
                "url": url if url else "#",
                "source": domain or "Manual",
                "image": st.session_state.f_image.strip(),
                "priority": st.session_state.f_priority,
            })
            save_data()
            st.session_state._clear_form = True
            st.session_state.fetch_note = ""
            st.rerun()
        else:
            st.warning("Enter both a name and a price.")

# =============================================================================
# 7. MAIN
# =============================================================================
st.markdown(
    '<div style="text-align:center; margin-bottom:1.5rem;">'
    '<h1 style="font-size:32px; margin:0; font-weight:600;">Luxury Shopping Calculator</h1>'
    '<p style="font-family:Inter; font-size:13px; font-weight:500; letter-spacing:0.12em; '
    'text-transform:uppercase; color:#5A4F47; margin-top:6px;">Wardrobe investment planner</p>'
    '</div>', unsafe_allow_html=True)

budget = float(st.session_state.total_budget)
total_spent = sum(i["price"] for i in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

st.markdown(
    f'<div class="lux-metrics">'
    f'<div class="lux-metric"><p class="m-label">Total budget</p><p class="m-val">{fmt_inr(budget)}</p></div>'
    f'<div class="lux-metric"><p class="m-label">Allocated</p><p class="m-val">{fmt_inr(total_spent)}</p></div>'
    f'<div class="lux-metric{" warn" if over else ""}"><p class="m-label">{"Over budget" if over else "Available"}</p>'
    f'<p class="m-val">{fmt_inr(abs(remaining))}</p></div></div>', unsafe_allow_html=True)

pct = min(total_spent / budget, 1.0) if budget > 0 else 0.0
st.markdown(
    f'<div class="bar-wrap"><div class="bar-head"><span>Allocation</span>'
    f'<span class="pct">{pct*100:.0f}%</span></div>'
    f'<div class="bar-track"><div class="bar-fill {"over" if over else ""}" style="width:{pct*100:.1f}%"></div></div>'
    f'<div class="bar-note">{"Over the limit — consider removing a piece." if over else "On track — your budget is balanced."}</div></div>',
    unsafe_allow_html=True)

col_items, col_notes = st.columns([2.2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    st.markdown(f'<p class="sec-label">The collection — {count} piece{"s" if count!=1 else ""}</p>', unsafe_allow_html=True)
    if not st.session_state.shopping_list:
        st.info("Your collection is empty. Add your first piece from the sidebar.")
    else:
        for idx, item in enumerate(st.session_state.shopping_list):
            with st.container(border=True):
                ci, cn, cp = st.columns([0.6, 3.2, 1.5])
                with ci:
                    if item.get("image"):
                        st.markdown(f'<div class="thumb"><img src="{item["image"]}" '
                                    f'onerror="this.style.display=\'none\';this.parentElement.innerHTML='
                                    f'\'<span class=thumb-ph>✦</span>\'"/></div>', unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="thumb"><span class="thumb-ph">✦</span></div>', unsafe_allow_html=True)
                with cn:
                    if item["url"] != "#":
                        st.markdown(f'<a class="p-name" href="{item["url"]}" target="_blank">{item["name"]}</a>',
                                    unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="p-name">{item["name"]}</span>', unsafe_allow_html=True)
                    st.markdown(f'<div style="margin-top:6px;">{pbadge(item.get("priority","Considering"))}'
                                f' <span class="p-src">{item["source"]}</span></div>', unsafe_allow_html=True)
                with cp:
                    st.markdown(f'<div class="p-price">{fmt_inr(item["price"])}</div>', unsafe_allow_html=True)
                    if st.button("Remove", key=f"del_{idx}", use_container_width=True):
                        st.session_state.shopping_list.pop(idx)
                        save_data()
                        st.rerun()

with col_notes:
    st.markdown('<p class="sec-label">Stylist notes</p>', unsafe_allow_html=True)
    if not st.session_state.shopping_list:
        st.markdown('<div class="notes-card"><p class="n-text">Add pieces to see budget insights.</p></div>',
                    unsafe_allow_html=True)
    else:
        dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
        avg = total_spent / count
        w = "warn" if over else ""
        n = f'<div class="notes-card {w}">'
        if over:
            n += (f'<p class="n-text"><b>Over budget by {fmt_inr(abs(remaining))}</b></p>'
                  f'<p class="n-text">Consider pausing on <b>{dearest["name"]}</b> ({fmt_inr(dearest["price"])}) to rebalance.</p>')
        elif pct > 0.85:
            n += (f'<p class="n-text">Nearly at capacity — {fmt_inr(remaining)} remaining.</p>'
                  f'<p class="n-text">Consider holding off before adding more.</p>')
        else:
            n += (f'<p class="n-text">Your curation is perfectly balanced.</p>'
                  f'<p class="n-text">{fmt_inr(remaining)} of room remaining.</p>')
        n += '<hr>'
        n += (f'<p class="n-text" style="font-size:13px;">Top piece: <b>{dearest["name"]}</b> — {fmt_inr(dearest["price"])}<br>'
              f'Average: <b>{fmt_inr(avg)}</b>/item &nbsp;·&nbsp; Items: <b>{count}</b></p></div>')
        st.markdown(n, unsafe_allow_html=True)

st.markdown('<div class="watermark">Made with <span class="h">♥</span> by Vansh for Didi Gupta</div>', unsafe_allow_html=True)
