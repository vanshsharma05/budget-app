import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re
import json
import os

# -----------------------------------------------------------------------------
# 0. PAGE CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Curated Lookbook",
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
# 3. SCRAPER
# -----------------------------------------------------------------------------
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
                    img = img.get("url")
                if img:
                    found["image"] = img
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


def extract_product_details(url):
    result = {"title": None, "price": None, "image": None, "currency": None, "ok": False}
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return result
        soup = BeautifulSoup(resp.content, "html.parser")

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

        def meta(prop, attr="property"):
            tag = soup.find("meta", {attr: prop})
            return tag.get("content").strip() if tag and tag.get("content") else None

        if not result["title"]:
            result["title"] = meta("og:title") or (
                soup.title.string.strip() if soup.title and soup.title.string else None
            )
        if not result["image"]:
            result["image"] = meta("og:image:secure_url") or meta("og:image")
        if not result["price"]:
            pm = (meta("product:price:amount") or meta("og:price:amount")
                  or meta("price", "itemprop"))
            if pm:
                result["price"] = parse_amount(pm)
            cm = meta("product:price:currency") or meta("og:price:currency")
            if cm and not result["currency"]:
                result["currency"] = cm

        if not result["price"]:
            ip = soup.find(attrs={"itemprop": "price"})
            if ip:
                result["price"] = parse_amount(ip.get("content") or ip.get_text())

        if not result["title"]:
            h1 = soup.find("h1")
            if h1:
                result["title"] = h1.get_text(strip=True)
        if not result["price"]:
            text = soup.get_text(" ", strip=True)
            m = re.search(r"(?:₹|Rs\.?|INR|\$|€|£)\s?([\d][\d.,]{2,})", text)
            if m:
                result["price"] = parse_amount(m.group(1))
                if not result["currency"]:
                    result["currency"] = detect_currency(m.group(0))

        if result["price"] and not result["currency"]:
            result["currency"] = "INR"
        if result["title"] or result["price"]:
            result["ok"] = True
        return result
    except Exception:
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
    "_clear_form": False, "_just_added": False,
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
# 5. LUXURY EDITORIAL STYLING
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;0,600;1,400&family=Inter:wght@300;400;500&display=swap');

    :root {
        --taupe: #4A3F3A;
        --taupe-light: #6B5E56;
        --cream: #FDF8F4;
        --warm-white: #FFFAF7;
        --blush: #F5E6E0;
        --blush-border: #E8D5CE;
        --accent-pink: #D4A0A0;
        --rose-muted: #C47A7A;
        --rose-deep: #A85C5C;
    }

    .stApp {
        background: linear-gradient(168deg, #FFFAF7 0%, #FDF8F4 40%, #FBF4EF 100%) !important;
    }

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, system-ui, sans-serif !important;
        color: var(--taupe) !important;
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 1100px;
    }

    footer { visibility: hidden; }

    /* — Typography — */
    h1, h2, h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--taupe) !important;
        font-weight: 500 !important;
        letter-spacing: 0.02em;
    }

    /* — Sidebar — */
    [data-testid="stSidebar"] {
        background: var(--cream) !important;
        border-right: 0.5px solid var(--blush-border) !important;
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {
        font-family: 'Playfair Display', Georgia, serif !important;
        color: var(--taupe) !important;
    }
    [data-testid="stSidebar"] label {
        font-family: 'Inter', sans-serif !important;
        font-weight: 400 !important;
        font-size: 11px !important;
        letter-spacing: 0.1em !important;
        text-transform: uppercase !important;
        color: var(--accent-pink) !important;
    }
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] .stCaption {
        color: var(--taupe-light) !important;
    }

    /* — Inputs — */
    .stTextInput input, .stNumberInput input {
        border-radius: 10px !important;
        border: 0.5px solid var(--blush-border) !important;
        background: white !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 13px !important;
        color: var(--taupe) !important;
        padding: 10px 14px !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: var(--accent-pink) !important;
        box-shadow: 0 0 0 2px rgba(212, 160, 160, 0.15) !important;
    }
    .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 10px !important;
        border: 0.5px solid var(--blush-border) !important;
        background: white !important;
    }
    label {
        font-family: 'Inter', sans-serif !important;
        font-weight: 400 !important;
        color: var(--taupe-light) !important;
    }

    /* — Buttons — */
    .stButton > button {
        border-radius: 10px !important;
        font-family: 'Playfair Display', serif !important;
        font-weight: 500 !important;
        font-size: 14px !important;
        letter-spacing: 0.03em !important;
        transition: all 0.2s ease !important;
        padding: 0.55rem 1.2rem !important;
    }
    .stButton > button[kind="primary"] {
        background: var(--blush) !important;
        color: var(--taupe) !important;
        border: 0.5px solid var(--blush-border) !important;
        box-shadow: none !important;
    }
    .stButton > button[kind="primary"]:hover {
        background: var(--accent-pink) !important;
        color: white !important;
        border-color: var(--accent-pink) !important;
    }
    .stButton > button[kind="secondary"] {
        background: transparent !important;
        color: var(--accent-pink) !important;
        border: 0.5px solid var(--blush-border) !important;
    }
    .stButton > button[kind="secondary"]:hover {
        background: var(--cream) !important;
        border-color: var(--accent-pink) !important;
        color: var(--taupe) !important;
    }

    /* — Metric cards — */
    .lux-metric {
        background: var(--cream);
        border-radius: 14px;
        padding: 22px 18px;
        text-align: center;
        border: 0.5px solid var(--blush-border);
    }
    .lux-metric.warn {
        background: #FDF0EE;
        border-color: var(--rose-muted);
    }
    .lux-metric-label {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: 400;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--accent-pink);
        margin: 0 0 8px;
    }
    .lux-metric.warn .lux-metric-label { color: var(--rose-muted); }
    .lux-metric-value {
        font-family: 'Playfair Display', serif;
        font-size: 24px;
        font-weight: 500;
        color: var(--taupe);
        margin: 0;
    }
    .lux-metric.warn .lux-metric-value { color: var(--rose-muted); }

    /* — Budget bar — */
    .budget-bar-wrap {
        background: var(--cream);
        border-radius: 14px;
        padding: 18px 22px;
        border: 0.5px solid var(--blush-border);
        margin: 1rem 0 0.5rem;
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
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--accent-pink);
    }
    .budget-bar-header .pct {
        font-family: 'Playfair Display', serif;
        font-size: 16px;
        color: var(--taupe);
    }
    .budget-track {
        height: 6px;
        border-radius: 6px;
        background: var(--blush);
        overflow: hidden;
    }
    .budget-fill {
        height: 100%;
        border-radius: 6px;
        background: var(--accent-pink);
        transition: width 0.5s ease;
    }
    .budget-fill.over { background: var(--rose-muted); }
    .budget-note {
        font-family: 'Playfair Display', serif;
        font-size: 13px;
        font-style: italic;
        color: var(--taupe-light);
        margin-top: 10px;
    }

    /* — Section labels — */
    .section-label {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        font-weight: 400;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--accent-pink);
        margin: 0 0 12px;
    }

    /* — Product cards — */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px !important;
        border: 0.5px solid var(--blush-border) !important;
        background: var(--cream) !important;
        box-shadow: none !important;
    }
    .prod-img-box {
        width: 100%;
        aspect-ratio: 1/1;
        border-radius: 10px;
        overflow: hidden;
        background: var(--blush);
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .prod-img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    .prod-img-ph {
        font-size: 1.5rem;
        color: var(--accent-pink);
    }
    .prod-name {
        font-family: 'Playfair Display', serif;
        font-weight: 500;
        font-size: 15px;
        color: var(--taupe);
        text-decoration: none;
    }
    a.prod-name {
        border-bottom: 0.5px solid var(--blush-border);
        padding-bottom: 1px;
    }
    a.prod-name:hover {
        color: var(--rose-deep);
        border-color: var(--rose-deep);
    }
    .prod-source {
        font-family: 'Inter', sans-serif;
        font-size: 11px;
        color: var(--accent-pink);
    }
    .prio-badge {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 20px;
        font-family: 'Inter', sans-serif;
        font-size: 10px;
        font-weight: 500;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }
    .prod-price {
        font-family: 'Playfair Display', serif;
        font-weight: 500;
        font-size: 18px;
        color: var(--taupe);
        text-align: right;
    }

    /* — Stylist notes — */
    .stylist-card {
        background: var(--cream);
        border-radius: 14px;
        padding: 20px;
        border: 0.5px solid var(--blush-border);
    }
    .stylist-card.warn {
        background: #FDF0EE;
        border-color: var(--rose-muted);
    }
    .stylist-text {
        font-family: 'Playfair Display', serif;
        font-size: 14px;
        line-height: 1.7;
        color: var(--taupe);
        margin-bottom: 8px;
    }
    .stylist-card.warn .stylist-text { color: var(--rose-muted); }

    /* — Dividers & spacing — */
    hr {
        border: none !important;
        border-top: 0.5px solid var(--blush-border) !important;
        margin: 0.8rem 0 !important;
    }
    .stDivider { border-color: var(--blush-border) !important; }

    /* — Header — */
    .lux-header {
        text-align: center;
        margin-bottom: 1.5rem;
    }
    .lux-title {
        font-family: 'Playfair Display', serif;
        font-size: 32px;
        font-weight: 500;
        letter-spacing: 0.04em;
        color: var(--taupe);
        margin: 0;
    }
    .lux-subtitle {
        font-family: 'Inter', sans-serif;
        font-size: 12px;
        font-weight: 300;
        letter-spacing: 0.15em;
        text-transform: uppercase;
        color: var(--accent-pink);
        margin-top: 4px;
    }
    .lux-divider {
        text-align: center;
        color: var(--blush-border);
        font-size: 10px;
        letter-spacing: 8px;
        margin: 8px 0 1.2rem;
    }

    /* remove streamlit branding clutter */
    #MainMenu { visibility: hidden; }
    header[data-testid="stHeader"] { background: transparent !important; }
    .stDeployButton { display: none !important; }

    @media (prefers-reduced-motion: reduce) {
        .budget-fill { transition: none !important; }
    }
</style>
""", unsafe_allow_html=True)


def priority_badge(p):
    palette = {
        "Must have": ("#FDF0EE", "#A85C5C"),
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
    st.markdown("&nbsp;", unsafe_allow_html=True)

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
        else:
            with st.spinner("Reading the page…"):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]:
                    st.session_state.f_name = res["title"][:80]
                if res["image"]:
                    st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = res["currency"] or "INR"
                    rate = APPROX_RATES_TO_INR.get(cur.upper(), 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur.upper() != "INR":
                        st.session_state.fetch_note = (
                            f"Found {cur.upper()} {res['price']:,.0f} — converted to approx. "
                            f"{fmt_inr(st.session_state.f_price)}. Verify the rate."
                        )
                    else:
                        st.session_state.fetch_note = "Details found. Review below and adjust if needed."
                else:
                    st.session_state.fetch_note = "Name found, but not the price — enter it manually."
            else:
                st.session_state.fetch_note = (
                    "This site blocked automatic reading. Enter the details manually below."
                )
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="Prada Re-Edition 2005")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
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
                "image": st.session_state.f_image,
                "priority": st.session_state.f_priority,
            })
            save_data()
            st.session_state._clear_form = True
            st.session_state._just_added = True
            st.session_state.fetch_note = ""
            st.rerun()
        else:
            st.warning("Please enter both a name and a price.")

# -----------------------------------------------------------------------------
# 7. HEADER
# -----------------------------------------------------------------------------
if st.session_state._just_added:
    st.balloons()
    st.session_state._just_added = False

st.markdown(
    '<div class="lux-header">'
    '<div class="lux-title">Curated Lookbook</div>'
    '<div class="lux-subtitle">Luxury wardrobe planner</div>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown('<div class="lux-divider">— ✦ —</div>', unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 8. BUDGET SUMMARY
# -----------------------------------------------------------------------------
budget = float(st.session_state.total_budget)
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

c1, c2, c3 = st.columns(3)
c1.markdown(
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Total allowance</p>'
    f'<p class="lux-metric-value">{fmt_inr(budget)}</p>'
    f'</div>',
    unsafe_allow_html=True,
)
c2.markdown(
    f'<div class="lux-metric">'
    f'<p class="lux-metric-label">Allocated funds</p>'
    f'<p class="lux-metric-value">{fmt_inr(total_spent)}</p>'
    f'</div>',
    unsafe_allow_html=True,
)
if over:
    c3.markdown(
        f'<div class="lux-metric warn">'
        f'<p class="lux-metric-label">Over budget</p>'
        f'<p class="lux-metric-value">{fmt_inr(abs(remaining))}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )
else:
    c3.markdown(
        f'<div class="lux-metric">'
        f'<p class="lux-metric-label">Available balance</p>'
        f'<p class="lux-metric-value">{fmt_inr(remaining)}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

# Budget bar
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
    f'{"Over the limit — consider removing a piece." if over else "Your curation is on track."}'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 9. COLLECTION + STYLIST NOTES
# -----------------------------------------------------------------------------
col_items, col_notes = st.columns([2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    st.markdown(
        f'<p class="section-label">The wardrobe — {count} piece{"s" if count != 1 else ""}</p>',
        unsafe_allow_html=True,
    )

    if not st.session_state.shopping_list:
        st.info("Your collection is empty. Add your first piece from the sidebar.")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            with st.container(border=True):
                ci, cn, cp = st.columns([1, 3.1, 1.7])
                with ci:
                    if item.get("image"):
                        st.markdown(
                            f'<div class="prod-img-box"><img class="prod-img" src=\'{item["image"]}\' '
                            f'onerror="this.style.display=\'none\'"/></div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(
                            '<div class="prod-img-box"><span class="prod-img-ph">✦</span></div>',
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
                        f'<div style="margin-top:6px;">{priority_badge(item.get("priority", "Considering"))}'
                        f'<span class="prod-source"> · {item["source"]}</span></div>',
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
            '<p class="stylist-text">Add a few pieces and insights will appear here.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
        avg = total_spent / count
        warn_cls = "warn" if over else ""

        notes_html = f'<div class="stylist-card {warn_cls}">'
        if over:
            notes_html += (
                f'<p class="stylist-text">You are over budget by <b>{fmt_inr(abs(remaining))}</b>.</p>'
                f'<p class="stylist-text">Consider pausing on the <b>{dearest["name"]}</b> '
                f'({fmt_inr(dearest["price"])}) to rebalance your wardrobe.</p>'
            )
        elif pct > 0.85:
            notes_html += (
                f'<p class="stylist-text">Nearly at capacity — <b>{fmt_inr(remaining)}</b> remaining.</p>'
                f'<p class="stylist-text">Consider holding off on the next addition.</p>'
            )
        else:
            notes_html += (
                f'<p class="stylist-text">Your curation is perfectly balanced.</p>'
                f'<p class="stylist-text">You have <b>{fmt_inr(remaining)}</b> of room remaining.</p>'
            )
        notes_html += '<hr>'
        notes_html += (
            f'<p class="stylist-text" style="font-size:13px; color: var(--taupe-light);">'
            f'Statement piece: <b>{dearest["name"]}</b><br>'
            f'Average per item: <b>{fmt_inr(avg)}</b></p>'
        )
        notes_html += '</div>'
        st.markdown(notes_html, unsafe_allow_html=True)
