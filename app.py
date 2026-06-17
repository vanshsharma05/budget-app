import streamlit as st
import re
import json
import os
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

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

def detect_currency_from_text(text):
    if "₹" in text or "Rs" in text or "INR" in text: return "INR"
    if "$" in text or "USD" in text: return "USD"
    if "€" in text or "EUR" in text: return "EUR"
    if "£" in text or "GBP" in text: return "GBP"
    return None

# =============================================================================
# 3. JINA READER SCRAPER — free, no key, 2-5 sec, works on LV
# =============================================================================
BRANDS_RE = r'Louis Vuitton|Gucci|Prada|Chanel|Dior|Herm[eè]s|Burberry|Versace|Fendi|Balenciaga|Bottega Veneta|Cartier|Tiffany|Saint Laurent|YSL|Celine|Valentino|Givenchy|Bvlgari|Tom Ford|TataCLiQ|Tata CLiQ|Myntra|Ajio|Nykaa|Official'

def _try_jina(url, timeout=12):
    """Jina Reader: r.jina.ai proxies and renders any URL. Free, no API key."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "Accept": "text/markdown",
            "User-Agent": "Mozilla/5.0 LuxuryCalculator/1.0",
        }
        resp = requests.get(jina_url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.text) > 100:
            return resp.text
    except Exception:
        pass
    return None


def _try_direct(url, timeout=8):
    """Direct fetch as backup — works for many sites."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            return resp.text
    except Exception:
        pass
    return None


def _extract_from_jina(content, url):
    """Parse Jina's markdown output for title/price/image."""
    r = {"title": None, "price": None, "image": None, "currency": None}

    # Jina returns content like:
    # Title: Product Name
    # URL Source: ...
    # Markdown Content:
    # # Heading
    # ![alt](image-url)
    # Price ₹42,500 etc.

    # Extract title from "Title:" header
    m = re.search(r'^Title:\s*(.+?)$', content, re.M)
    if m:
        r["title"] = m.group(1).strip()

    # Or from first markdown heading
    if not r["title"]:
        m = re.search(r'^#+\s+(.+?)$', content, re.M)
        if m:
            r["title"] = m.group(1).strip()

    # Extract image: first markdown image
    img_matches = re.findall(r'!\[[^\]]*\]\((https?://[^\)]+)\)', content)
    for img in img_matches:
        # Skip tiny icons, logos, base64
        if any(skip in img.lower() for skip in ["logo", "icon", "favicon", "data:image"]):
            continue
        if any(ext in img.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            r["image"] = img
            break
    if not r["image"] and img_matches:
        r["image"] = img_matches[0]

    # Extract price — try multiple patterns, INR first
    price_patterns = [
        (r"₹\s*([\d][\d,]+(?:\.\d{1,2})?)", "INR"),
        (r"Rs\.?\s*([\d][\d,]+(?:\.\d{1,2})?)", "INR"),
        (r"INR\s*([\d][\d,]+(?:\.\d{1,2})?)", "INR"),
        (r"\$\s*([\d][\d,]+(?:\.\d{1,2})?)", "USD"),
        (r"€\s*([\d][\d,]+(?:\.\d{1,2})?)", "EUR"),
        (r"£\s*([\d][\d,]+(?:\.\d{1,2})?)", "GBP"),
    ]
    for pat, cur in price_patterns:
        m = re.search(pat, content)
        if m:
            v = parse_amount(m.group(1))
            if v and 50 < v < 50000000:
                r["price"] = v
                r["currency"] = cur
                break

    return r


def _extract_from_html(html_str, url):
    """Parse HTML directly for sites where Jina fails."""
    soup = BeautifulSoup(html_str, "html.parser")
    r = {"title": None, "price": None, "image": None, "currency": None}

    # Meta tags
    def meta(prop, attr="property"):
        tag = soup.find("meta", {attr: prop})
        return tag.get("content","").strip() if tag and tag.get("content") else None

    r["title"] = meta("og:title") or meta("twitter:title")
    r["image"] = meta("og:image:secure_url") or meta("og:image") or meta("twitter:image")
    pm = meta("product:price:amount") or meta("og:price:amount")
    if pm: r["price"] = parse_amount(pm)
    cm = meta("product:price:currency") or meta("og:price:currency")
    if cm: r["currency"] = cm

    # JSON-LD
    if not r["price"] or not r["title"]:
        for sc in soup.find_all("script", type="application/ld+json"):
            raw = sc.string or sc.get_text()
            if not raw: continue
            try: data = json.loads(raw)
            except Exception: continue

            def walk(node):
                if isinstance(node, dict):
                    if "Product" in str(node.get("@type", "")):
                        if not r["title"] and node.get("name"):
                            r["title"] = str(node["name"]).strip()
                        img = node.get("image")
                        if img and not r["image"]:
                            if isinstance(img, list): img = img[0]
                            if isinstance(img, dict): img = img.get("url")
                            if img: r["image"] = str(img)
                    offers = node.get("offers")
                    if offers and not r["price"]:
                        offs = offers if isinstance(offers, list) else [offers]
                        for off in offs:
                            if isinstance(off, dict):
                                p = off.get("price")
                                if p:
                                    pv = parse_amount(p)
                                    if pv: r["price"] = pv
                                    if off.get("priceCurrency"): r["currency"] = off["priceCurrency"]
                    for v in node.values(): walk(v)
                elif isinstance(node, list):
                    for i in node: walk(i)
            walk(data)
            if r["price"] and r["title"]: break

    # H1 fallback
    if not r["title"]:
        h1 = soup.find("h1")
        if h1: r["title"] = h1.get_text(strip=True)[:100]

    # Body text price scan
    if not r["price"]:
        text = soup.get_text(" ", strip=True)
        for pat in [r"₹\s*([\d][\d,]+(?:\.\d{1,2})?)", r"Rs\.?\s*([\d][\d,]+(?:\.\d{1,2})?)"]:
            m = re.search(pat, text)
            if m:
                v = parse_amount(m.group(1))
                if v and 50 < v < 50000000:
                    r["price"] = v; r["currency"] = "INR"; break

    return r


def _clean_title(title):
    if not title: return title
    title = re.split(rf'\s*[|\-–—:]\s*(?:{BRANDS_RE})', title, flags=re.I)[0].strip()
    title = re.sub(r'^Buy\s+', '', title, flags=re.I).strip()
    title = re.sub(r'\s+(?:Online|at Best Price).*$', '', title, flags=re.I).strip()
    if title and " | " in title: title = title.split(" | ")[0].strip()
    return title[:80] if title else None


def extract_product_details(url):
    """Fast, free scraping. Jina first (works on LV), direct as backup."""
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}
    domain = urlparse(url).netloc.lower().replace("www.", "")

    # STRATEGY 1: Jina Reader (fast, free, handles JS)
    jina_content = _try_jina(url)
    if jina_content:
        extracted = _extract_from_jina(jina_content, url)
        for k in ("title", "price", "image", "currency"):
            if extracted.get(k): result[k] = extracted[k]

    # STRATEGY 2: Direct HTML if Jina didn't get a price
    if not result["price"]:
        html = _try_direct(url)
        if html:
            extracted = _extract_from_html(html, url)
            for k in ("title", "price", "image", "currency"):
                if extracted.get(k) and not result.get(k): result[k] = extracted[k]

    # LV image fallback from SKU pattern
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
# 5. STYLING — readable, high contrast, mobile-friendly
# =============================================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap');

    :root {
        --ink: #1A130F;
        --ink-mid: #2E2520;
        --ink-soft: #4A3F38;
        --cream: #F7F2EC;
        --warm-bg: #FCF8F4;
        --blush: #ECD8CD;
        --border: #C9B3A6;
        --border-soft: #DCC9BE;
        --accent: #A66B5A;
        --accent-deep: #804433;
        --rose: #8B2828;
        --rose-bg: #F8E5E3;
        --green: #3D5429;
        --green-bg: #ECF1E2;
        --amber: #8B5E1A;
        --amber-bg: #FAF1DC;
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
        color: var(--ink) !important; font-weight: 600 !important;
    }

    /* SIDEBAR */
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
        font-weight: 700 !important; letter-spacing: 0.08em !important;
        text-transform: uppercase !important; color: var(--ink) !important;
        margin: 16px 0 10px !important;
    }
    [data-testid="stSidebar"] label {
        font-family: 'Inter' !important; font-weight: 600 !important;
        font-size: 14px !important; color: var(--ink-mid) !important;
    }

    /* Sidebar caption box — much more visible */
    [data-testid="stSidebar"] .stCaption {
        background: white !important;
        border: 1px solid var(--border) !important;
        border-radius: 8px !important;
        padding: 10px 12px !important;
        font-size: 13px !important;
        line-height: 1.5 !important;
        color: var(--ink-mid) !important;
        font-weight: 500 !important;
        margin: 8px 0 12px !important;
        word-wrap: break-word !important;
        white-space: normal !important;
    }

    /* INPUTS */
    .stTextInput input, .stNumberInput input {
        border-radius: 10px !important; border: 1px solid var(--border) !important;
        background: white !important; font-size: 16px !important;
        color: var(--ink) !important; padding: 12px 14px !important;
        min-height: 46px !important;
    }
    .stTextInput input::placeholder, .stNumberInput input::placeholder {
        color: #8A7A70 !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px rgba(166,107,90,0.2) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 10px !important; border: 1px solid var(--border) !important;
        background: white !important; min-height: 46px !important; font-size: 16px !important;
        color: var(--ink) !important;
    }
    .stSelectbox div[data-baseweb="select"] span {
        color: var(--ink) !important; font-weight: 500 !important;
    }

    /* BUTTONS */
    .stButton > button {
        border-radius: 10px !important; font-family: 'Inter' !important;
        font-weight: 600 !important; font-size: 15px !important;
        padding: 12px 18px !important; min-height: 48px !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--ink) !important; color: white !important;
        border: none !important; box-shadow: 0 2px 4px rgba(0,0,0,0.08);
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--accent-deep) !important; transform: translateY(-1px);
        box-shadow: 0 4px 10px rgba(0,0,0,0.12);
    }
    .stButton > button:not([kind="primary"]) {
        background: white !important; color: var(--ink) !important;
        border: 1.5px solid var(--border) !important;
    }
    .stButton > button:not([kind="primary"]):hover {
        border-color: var(--accent) !important; background: var(--cream) !important;
    }

    /* METRIC CARDS */
    .lux-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 18px; }
    .lux-metric {
        background: white; border-radius: 16px; padding: 22px 18px;
        text-align: center; border: 1px solid var(--border-soft);
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .lux-metric.warn { background: var(--rose-bg); border-color: #C58080; }
    .m-label {
        font-family: 'Inter'; font-size: 12px; font-weight: 700;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--ink-mid); margin: 0 0 10px;
    }
    .lux-metric.warn .m-label { color: var(--rose); }
    .m-val {
        font-family: 'Playfair Display', serif; font-size: 28px; font-weight: 600;
        color: var(--ink); margin: 0; line-height: 1.1;
    }
    .lux-metric.warn .m-val { color: var(--rose); }

    /* BAR */
    .bar-wrap {
        background: white; border-radius: 16px; padding: 18px 22px;
        border: 1px solid var(--border-soft); margin-bottom: 22px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .bar-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
    .bar-head span:first-child {
        font-family: 'Inter'; font-size: 12px; font-weight: 700;
        letter-spacing: 0.1em; text-transform: uppercase; color: var(--ink-mid);
    }
    .bar-head .pct {
        font-family: 'Playfair Display', serif; font-size: 18px;
        color: var(--ink); font-weight: 600;
    }
    .bar-track { height: 8px; border-radius: 8px; background: var(--blush); overflow: hidden; }
    .bar-fill { height: 100%; border-radius: 8px; background: var(--accent); transition: width 0.4s ease; }
    .bar-fill.over { background: var(--rose); }
    .bar-note {
        font-family: 'Inter'; font-size: 14px; color: var(--ink-mid);
        margin-top: 12px; line-height: 1.5; font-weight: 500;
    }

    .sec-label {
        font-family: 'Inter'; font-size: 12px; font-weight: 700;
        letter-spacing: 0.1em; text-transform: uppercase;
        color: var(--ink-mid); margin: 0 0 14px;
    }

    /* PRODUCT CARDS */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important; border: 1px solid var(--border-soft) !important;
        background: white !important; box-shadow: 0 1px 3px rgba(0,0,0,0.04) !important;
    }
    .thumb {
        width: 60px; height: 60px; border-radius: 10px; overflow: hidden;
        background: var(--blush); display: flex; align-items: center;
        justify-content: center; flex-shrink: 0;
    }
    .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
    .thumb-ph { font-size: 18px; color: var(--accent-deep); }
    .p-name {
        font-family: 'Playfair Display', serif; font-weight: 600;
        font-size: 16px; color: var(--ink); text-decoration: none; line-height: 1.3;
    }
    a.p-name:hover { color: var(--accent-deep); }
    .p-src {
        font-family: 'Inter'; font-size: 12px;
        color: var(--ink-soft); margin-top: 4px; font-weight: 500;
    }

    /* PRIORITY BADGES — much more visible */
    .badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 6px;
        font-family: 'Inter';
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        border: 1px solid;
    }

    .p-price {
        font-family: 'Playfair Display', serif; font-weight: 600;
        font-size: 19px; color: var(--ink); text-align: right;
    }

    /* STYLIST NOTES */
    .notes-card {
        background: white; border-radius: 14px; padding: 20px;
        border: 1px solid var(--border-soft); box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .notes-card.warn { background: var(--rose-bg); border-color: #C58080; }
    .n-text {
        font-family: 'Inter'; font-size: 14px; line-height: 1.7;
        color: var(--ink-mid); margin-bottom: 8px; font-weight: 500;
    }
    .notes-card.warn .n-text { color: var(--rose); }
    .notes-card b { color: var(--ink); font-weight: 700; }

    /* STATUS INDICATOR */
    .api-status {
        font-family: 'Inter'; font-size: 13px; font-weight: 600;
        padding: 10px 14px; border-radius: 8px; margin-bottom: 14px;
        display: flex; align-items: center; gap: 8px;
    }
    .api-status.on { background: var(--green-bg); color: var(--green); border: 1px solid #A8BD8F; }

    .watermark {
        text-align: center; padding: 3rem 0 1rem; font-family: 'Inter';
        font-size: 13px; color: var(--ink-soft); opacity: 0.7; font-weight: 500;
    }
    .watermark .h { color: var(--accent-deep); font-size: 14px; }

    .stAlert {
        border-radius: 12px !important; font-size: 14px !important;
        font-weight: 500 !important;
    }
    hr { border: none !important; border-top: 1px solid var(--border-soft) !important; margin: 1rem 0 !important; }

    /* MOBILE */
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
    """High-contrast, fully visible priority badges."""
    pal = {
        "Must have":   ("#F8E5E3", "#7A1F1F", "#C58080"),
        "Considering": ("#E8DCCB", "#3D2E20", "#B89A7A"),
        "Someday":     ("#DDD4E8", "#2F2350", "#9F8FB8"),
    }
    bg, fg, border = pal.get(p, ("#E8DCCB", "#3D2E20", "#B89A7A"))
    return f'<span class="badge" style="background:{bg};color:{fg};border-color:{border};">{p}</span>'


# =============================================================================
# 6. SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### The atelier")
    st.markdown('<div class="api-status on">✓ Fast scraping ready</div>', unsafe_allow_html=True)

    def _sb(): save_data()
    st.number_input("Wardrobe budget (₹)", min_value=0.0, step=5000.0,
                     key="total_budget", on_change=_sb, format="%.0f")
    st.markdown("---")
    st.markdown("#### Add to collection")
    st.text_input("Product link", key="f_url", placeholder="https://...")

    if st.button("Auto-fill from link", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Paste a link first."
        elif not url.startswith(("http://","https://")):
            st.session_state.fetch_note = "URL must start with https://"
        else:
            with st.spinner("Reading page…"):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]: st.session_state.f_name = res["title"]
                if res["image"]: st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = RATES.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = f"✓ Got it. {cur} {res['price']:,.0f} ≈ {fmt_inr(st.session_state.f_price)}"
                    else:
                        st.session_state.fetch_note = "✓ Got it. Review below."
                else:
                    st.session_state.fetch_note = "Got name. Enter price below."
            else:
                st.session_state.fetch_note = "Couldn't read it. Enter details below."
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="e.g. Ombre Nomade 100ml")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
    st.text_input("Image URL (optional)", key="f_image", placeholder="Paste image link")
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
    '<p style="font-family:Inter; font-size:13px; font-weight:600; letter-spacing:0.12em; '
    'text-transform:uppercase; color:#4A3F38; margin-top:6px;">Wardrobe investment planner</p>'
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
