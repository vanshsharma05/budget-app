import streamlit as st
import re
import json
import os
import time
from urllib.parse import urlparse, urljoin, quote_plus

# --- Lazy imports for scraper libs (graceful fallback) ---
def _get_cloudscraper():
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=3,
        )
    except ImportError:
        return None

def _get_playwright_page(url):
    """Launch a real headless browser. Most reliable but slowest."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)  # let JS render prices
            html = page.content()
            browser.close()
            return html
    except Exception:
        return None

from bs4 import BeautifulSoup
import requests

# =============================================================================
# 0. PAGE CONFIG
# =============================================================================
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
RATES = {
    "INR": 1.0, "USD": 86.0, "EUR": 93.0, "GBP": 109.0, "AED": 23.0,
    "JPY": 0.57, "CNY": 12.0, "SGD": 64.0, "CAD": 62.0, "AUD": 56.0, "CHF": 98.0,
}
CUR_SYM = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}

def fmt_inr(n):
    n = round(abs(n))
    s = str(n)
    if len(s) <= 3: return f"₹ {s}"
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result
        s = s[:-2]
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
# 3. SCRAPER
# =============================================================================
BRANDS_RE = r'Louis Vuitton|Gucci|Prada|Chanel|Dior|Herm[eè]s|Burberry|Versace|Fendi|Balenciaga|Bottega Veneta|Cartier|Tiffany|Jimmy Choo|Christian Louboutin|Saint Laurent|YSL|Celine|Valentino|Givenchy|Bvlgari|Tom Ford|Moncler|Official'

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

def _extract_from_html(html_str, url):
    """Parse HTML string and extract product data using every method."""
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
    for sel in ['[itemprop="price"]', '.price', 'span[class*="price"]', 'div[class*="price"]',
                '.product-price', '.product__price', '[data-price]', '.pdp-price', '.current-price']:
        if r["price"]: break
        try:
            el = soup.select_one(sel)
            if el:
                txt = el.get("content") or el.get_text(strip=True)
                p = parse_amount(txt)
                if p:
                    r["price"] = p
                    if not r["currency"]: r["currency"] = detect_currency(txt)
        except Exception: continue

    for sel in ['[itemprop="name"]', 'h1[class*="product"]', '.product-name', '.product-title', '.pdp-title']:
        if r["title"]: break
        try:
            el = soup.select_one(sel)
            if el and el.get_text(strip=True): r["title"] = el.get_text(strip=True)
        except Exception: continue

    if not r["image"]:
        for sel in ['[itemprop="image"]', 'img[class*="product"]', 'img[class*="gallery"]',
                     '.product-image img', 'img[class*="main"]']:
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
            for pat in [r'"price"\s*:\s*"?([\d.,]+)', r'"amount"\s*:\s*"?([\d.,]+)',
                        r'"salePrice"\s*:\s*"?([\d.,]+)']:
                m = re.search(pat, txt)
                if m:
                    v = parse_amount(m.group(1))
                    if v and v > 50: r["price"] = v; break
            if r["price"]: break

    # H1 fallback
    if not r["title"]:
        h1 = soup.find("h1")
        if h1: r["title"] = h1.get_text(strip=True)[:100]

    # Body text price scan
    if not r["price"]:
        text = soup.get_text(" ", strip=True)
        for pat in [r"₹\s?([\d][\d.,]{2,})", r"Rs\.?\s?([\d][\d.,]{2,})",
                     r"\$\s?([\d][\d.,]{2,})", r"€\s?([\d][\d.,]{2,})", r"£\s?([\d][\d.,]{2,})"]:
            m = re.search(pat, text)
            if m:
                v = parse_amount(m.group(1))
                if v and v > 50:
                    r["price"] = v
                    r["currency"] = detect_currency(m.group(0)) or "INR"
                    break

    if r["price"] and not r["currency"]: r["currency"] = "INR"
    return r

def _clean_title(title, url):
    if not title: return title
    title = re.split(rf'\s*[|\-–—:]\s*(?:{BRANDS_RE})', title, flags=re.I)[0].strip()
    title = re.sub(rf'^(?:Products?\s+by\s+)?(?:{BRANDS_RE})\s*[:|\-–—]\s*', '', title, flags=re.I).strip()
    title = re.sub(r'^Buy\s+', '', title, flags=re.I).strip()
    title = re.sub(r'\s+Online.*$', '', title, flags=re.I).strip()
    # Also strip from page title
    if title and " | " in title: title = title.split(" | ")[0].strip()
    if title and " - " in title:
        parts = title.split(" - ")
        title = parts[0].strip()
    return title[:80] if title else None


def extract_product_details(url, progress_cb=None):
    """Master extraction — tries cloudscraper → requests → playwright → Google."""
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}
    domain = urlparse(url).netloc.lower().replace("www.", "")
    html = None

    # ── STRATEGY 1: cloudscraper (bypasses Cloudflare/Akamai) ──
    if progress_cb: progress_cb("Attempting smart bypass…")
    scraper = _get_cloudscraper()
    if scraper:
        try:
            # Visit homepage first for cookies
            home = f"https://{urlparse(url).netloc}/"
            try: scraper.get(home, timeout=8)
            except Exception: pass
            time.sleep(0.5)
            resp = scraper.get(url, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 1000:
                html = resp.text
        except Exception:
            pass

    # ── STRATEGY 2: plain requests with browser-like headers ──
    if not html:
        if progress_cb: progress_cb("Trying direct connection…")
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none", "Sec-Fetch-User": "?1",
        }
        try:
            s = requests.Session()
            try: s.get(f"https://{urlparse(url).netloc}/", headers=headers, timeout=8)
            except Exception: pass
            resp = s.get(url, headers=headers, timeout=15, allow_redirects=True)
            if resp.status_code == 200 and len(resp.text) > 1000:
                html = resp.text
        except Exception:
            pass

    # ── STRATEGY 3: Playwright (real browser — nuclear option) ──
    if not html:
        if progress_cb: progress_cb("Launching headless browser…")
        pw_html = _get_playwright_page(url)
        if pw_html and len(pw_html) > 1000:
            html = pw_html

    # ── Extract from whatever HTML we got ──
    if html:
        extracted = _extract_from_html(html, url)
        for k in ("title", "price", "image", "currency"):
            if extracted.get(k): result[k] = extracted[k]

    # ── STRATEGY 4: Google search fallback ──
    if not result["price"] and not result["title"]:
        if progress_cb: progress_cb("Searching Google for product info…")
        try:
            search_url = f"https://www.google.com/search?q={quote_plus(url)}&hl=en"
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                "Accept": "text/html",
            }
            resp = requests.get(search_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                text = soup.get_text(" ", strip=True)
                for pat in [r"₹\s?([\d][\d.,]{2,})", r"Rs\.?\s?([\d][\d.,]{2,})",
                             r"\$\s?([\d][\d.,]{2,})", r"€\s?([\d][\d.,]{2,})"]:
                    m = re.search(pat, text)
                    if m:
                        v = parse_amount(m.group(1))
                        if v and v > 100:
                            result["price"] = v
                            result["currency"] = detect_currency(m.group(0)) or "INR"
                            break
                h3 = soup.find("h3")
                if h3 and not result["title"]:
                    result["title"] = h3.get_text(strip=True)[:80]
        except Exception:
            pass

    # ── Build LV image from SKU if missing ──
    if not result["image"] and "louisvuitton.com" in domain:
        sku = re.search(r'/([A-Z]{1,3}\d{4,6})', url)
        if sku:
            result["image"] = f"https://in.louisvuitton.com/images/is/image/lv/1/PP_VP_L/louis-vuitton--{sku.group(1)}_PM2_Front%20view.jpg?wid=400&hei=400"

    result["title"] = _clean_title(result.get("title"), url)
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
# 5. STYLING — high contrast, readable
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500&display=swap');
    :root {
        --ink: #2C2420;
        --ink-mid: #4A3F38;
        --ink-soft: #6B5E56;
        --cream: #F9F5F1;
        --warm-bg: #FDFAF7;
        --blush: #EFE0D8;
        --border: #E0CFC5;
        --accent: #B8897A;
        --accent-deep: #96675A;
        --rose: #B34D4D;
        --rose-bg: #FBF0EE;
    }

    .stApp { background: var(--warm-bg) !important; }
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
        color: var(--ink) !important;
    }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1080px; }
    footer, #MainMenu, .stDeployButton { display: none !important; visibility: hidden !important; }
    header[data-testid="stHeader"] { background: transparent !important; }

    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--ink) !important;
        font-weight: 500 !important;
    }

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: var(--cream) !important;
        border-right: 1px solid var(--border) !important;
    }
    [data-testid="stSidebar"] h3 {
        font-family: 'Playfair Display', serif !important;
        font-size: 22px !important;
        color: var(--ink) !important;
        margin-bottom: 4px !important;
    }
    [data-testid="stSidebar"] h4 {
        font-family: 'Inter', sans-serif !important;
        font-size: 13px !important;
        font-weight: 500 !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
        color: var(--ink-soft) !important;
    }
    [data-testid="stSidebar"] label {
        font-family: 'Inter' !important;
        font-weight: 500 !important;
        font-size: 13px !important;
        color: var(--ink-mid) !important;
    }

    /* ── Inputs ── */
    .stTextInput input, .stNumberInput input {
        border-radius: 8px !important;
        border: 1px solid var(--border) !important;
        background: white !important;
        font-size: 14px !important;
        color: var(--ink) !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 2px rgba(184,137,122,0.2) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 8px !important; border: 1px solid var(--border) !important; background: white !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        border-radius: 8px !important;
        font-family: 'Inter' !important;
        font-weight: 500 !important;
        font-size: 13px !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--ink) !important; color: white !important; border: none !important;
    }
    .stButton > button[kind="primary"]:hover { background: var(--accent-deep) !important; }
    .stButton > button[kind="secondary"], .stButton > button:not([kind="primary"]) {
        background: white !important; color: var(--ink) !important; border: 1px solid var(--border) !important;
    }
    .stButton > button:not([kind="primary"]):hover {
        border-color: var(--accent) !important; background: var(--cream) !important;
    }

    /* ── Metrics ── */
    .lux-metrics { display: grid; grid-template-columns: repeat(3,1fr); gap: 14px; margin-bottom: 14px; }
    .lux-metric {
        background: var(--cream); border-radius: 12px; padding: 22px 16px;
        text-align: center; border: 1px solid var(--border);
    }
    .lux-metric.warn { background: var(--rose-bg); border-color: #D4A0A0; }
    .m-label {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--ink-soft); margin: 0 0 6px;
    }
    .lux-metric.warn .m-label { color: var(--rose); }
    .m-val {
        font-family: 'Playfair Display', serif; font-size: 24px;
        font-weight: 500; color: var(--ink); margin: 0;
    }
    .lux-metric.warn .m-val { color: var(--rose); }

    /* ── Bar ── */
    .bar-wrap {
        background: var(--cream); border-radius: 12px;
        padding: 16px 20px; border: 1px solid var(--border); margin-bottom: 20px;
    }
    .bar-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
    .bar-head span:first-child {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-soft);
    }
    .bar-head .pct { font-family: 'Playfair Display', serif; font-size: 16px; color: var(--ink); }
    .bar-track { height: 6px; border-radius: 6px; background: var(--blush); overflow: hidden; }
    .bar-fill { height: 100%; border-radius: 6px; background: var(--accent); }
    .bar-fill.over { background: var(--rose); }
    .bar-note {
        font-family: 'Inter'; font-size: 13px; color: var(--ink-soft);
        margin-top: 10px; line-height: 1.5;
    }

    /* ── Section labels ── */
    .sec-label {
        font-family: 'Inter'; font-size: 11px; font-weight: 500;
        letter-spacing: 0.08em; text-transform: uppercase;
        color: var(--ink-soft); margin: 0 0 12px;
    }

    /* ── Product cards ── */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 10px !important; border: 1px solid var(--border) !important;
        background: var(--cream) !important; box-shadow: none !important;
    }
    .thumb {
        width: 54px; height: 54px; border-radius: 8px; overflow: hidden;
        background: var(--blush); display: flex; align-items: center; justify-content: center;
    }
    .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .thumb-ph { font-size: 16px; color: var(--accent); }
    .p-name {
        font-family: 'Playfair Display', serif; font-weight: 500;
        font-size: 15px; color: var(--ink); text-decoration: none; line-height: 1.35;
    }
    a.p-name:hover { color: var(--accent-deep); }
    .p-src { font-family: 'Inter'; font-size: 11px; color: var(--ink-soft); margin-top: 3px; }
    .badge {
        display: inline-block; padding: 2px 10px; border-radius: 4px;
        font-family: 'Inter'; font-size: 10px; font-weight: 500;
        letter-spacing: 0.04em; text-transform: uppercase;
    }
    .p-price {
        font-family: 'Playfair Display', serif; font-weight: 500;
        font-size: 17px; color: var(--ink); text-align: right;
    }

    /* ── Notes ── */
    .notes-card {
        background: var(--cream); border-radius: 12px;
        padding: 18px; border: 1px solid var(--border);
    }
    .notes-card.warn { background: var(--rose-bg); border-color: #D4A0A0; }
    .n-text {
        font-family: 'Inter'; font-size: 13px; line-height: 1.7;
        color: var(--ink-mid); margin-bottom: 6px;
    }
    .notes-card.warn .n-text { color: var(--rose); }

    .watermark {
        text-align: center; padding: 2.5rem 0 0.5rem;
        font-family: 'Inter'; font-size: 12px;
        color: var(--ink-soft); opacity: 0.4;
    }
    .watermark .h { color: var(--accent-deep); }

    .stAlert { border-radius: 10px !important; }
    hr { border: none !important; border-top: 1px solid var(--border) !important; margin: 0.8rem 0 !important; }
</style>
""", unsafe_allow_html=True)


def pbadge(p):
    pal = {"Must have": ("#FBF0EE","#993D3D"), "Considering": ("#F0EBE6","#5A4D44"), "Someday": ("#EDEBF3","#5E5478")}
    bg, fg = pal.get(p, ("#F0EBE6","#5A4D44"))
    return f'<span class="badge" style="background:{bg};color:{fg};">{p}</span>'


# =============================================================================
# 6. SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### The atelier")
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
            progress = st.empty()
            def show_progress(msg): progress.caption(f"⏳ {msg}")
            show_progress("Starting…")
            res = extract_product_details(url, progress_cb=show_progress)
            progress.empty()
            if res["ok"]:
                if res["title"]: st.session_state.f_name = res["title"]
                if res["image"]: st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = RATES.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = f"Converted {cur.upper()} {res['price']:,.0f} → ~{fmt_inr(st.session_state.f_price)}"
                    else:
                        st.session_state.fetch_note = "✓ Details found — review below."
                else:
                    st.session_state.fetch_note = "Found name but not price. Enter price manually."
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
    '<div style="text-align:center; margin-bottom:1.2rem;">'
    '<h1 style="font-size:30px; margin:0;">Luxury Shopping Calculator</h1>'
    '<p style="font-family:Inter; font-size:12px; font-weight:400; letter-spacing:0.1em; '
    'text-transform:uppercase; color:#6B5E56; margin-top:4px;">Wardrobe investment planner</p>'
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
                ci, cn, cp = st.columns([0.55, 3.2, 1.5])
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
                    st.markdown(f'<div style="margin-top:4px;">{pbadge(item.get("priority","Considering"))}'
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
        n += (f'<p class="n-text" style="font-size:12px;">Top piece: <b>{dearest["name"]}</b> — {fmt_inr(dearest["price"])}<br>'
              f'Average: <b>{fmt_inr(avg)}</b>/item &nbsp;·&nbsp; Items: <b>{count}</b></p></div>')
        st.markdown(n, unsafe_allow_html=True)

st.markdown('<div class="watermark">Made with <span class="h">♥</span> by Vansh for Didi Gupta</div>', unsafe_allow_html=True)
