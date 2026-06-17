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
    page_title="My Dreamy Wishlist",
    page_icon="🎀",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_FILE = "wishlist_data.json"

# -----------------------------------------------------------------------------
# 1. PERSISTENCE (saves to a little JSON file so nothing is lost on refresh)
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
        pass  # never crash the app over a save hiccup


# -----------------------------------------------------------------------------
# 2. CURRENCY HELPERS (luxury links are often $/€/£ — convert to ₹ approximately)
# -----------------------------------------------------------------------------
# Approximate rates -> editable. These are estimates for convenience only.
APPROX_RATES_TO_INR = {
    "INR": 1.0, "USD": 86.0, "EUR": 93.0, "GBP": 109.0, "AED": 23.0,
    "JPY": 0.57, "CNY": 12.0, "SGD": 64.0, "CAD": 62.0, "AUD": 56.0, "CHF": 98.0,
}
CURRENCY_SYMBOLS = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}


def parse_amount(raw):
    """Turn messy price text ('₹1,29,900.00', '$1,299.00', '1.299,00 €') into a float."""
    s = re.sub(r"[^\d.,]", "", str(raw)).strip(".,")  # drop stray seps from 'Rs.', trailing dots, etc.
    if not s:
        return None
    if "," in s and "." in s:
        # whichever separator comes last is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # European 1.299,00
        else:
            s = s.replace(",", "")                    # US/Indian 1,299.00
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 2:
            s = s.replace(",", ".")  # likely "1299,00" decimal
        else:
            s = s.replace(",", "")   # thousands separators
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
# 3. SCRAPER — reads structured data first (works on many real stores)
#    Priority: JSON-LD (schema.org) -> Open Graph / meta -> microdata -> guess
# -----------------------------------------------------------------------------
def _walk_jsonld(node, found):
    """Recursively pull name / image / price / currency out of JSON-LD."""
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

        # --- 1) JSON-LD structured data (the most reliable source) ---
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

        # --- 2) Open Graph / standard meta tags ---
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

        # --- 3) Microdata itemprop="price" ---
        if not result["price"]:
            ip = soup.find(attrs={"itemprop": "price"})
            if ip:
                result["price"] = parse_amount(ip.get("content") or ip.get_text())

        # --- 4) Last-resort heuristics ---
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

PRIORITIES = ["Must have 💖", "Wishing 🌸", "Someday 🌙"]
_defaults = {
    "f_url": "", "f_name": "", "f_price": 0.0, "f_image": "",
    "f_priority": "Wishing 🌸", "fetch_note": "",
    "_clear_form": False, "_just_added": False,
}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

# Reset the add-form BEFORE any widget is drawn (Streamlit requirement)
if st.session_state._clear_form:
    st.session_state.f_url = ""
    st.session_state.f_name = ""
    st.session_state.f_price = 0.0
    st.session_state.f_image = ""
    st.session_state.f_priority = "Wishing 🌸"
    st.session_state._clear_form = False

# -----------------------------------------------------------------------------
# 5. CUTE STYLING
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Pacifico&family=Quicksand:wght@400;500;600;700&display=swap');

    :root{
        --pink:#FF8FB8; --pink-deep:#F25C97; --lav:#C9A6FF; --mint:#62C6A8;
        --coral:#FF6B8A; --ink:#6B4F5C; --ink-deep:#4A2F3D; --card-shadow:0 10px 30px rgba(255,143,184,.14);
    }

    .stApp{
        background:
            radial-gradient(1200px 500px at 12% -8%, #FFE6F1 0%, rgba(255,230,241,0) 55%),
            radial-gradient(1100px 520px at 100% 0%, #EDE4FF 0%, rgba(237,228,255,0) 55%),
            linear-gradient(160deg,#FFF6FB 0%, #FFF3F8 45%, #F7F1FF 100%);
        background-attachment:fixed;
    }
    html, body, [class*="css"]{ font-family:'Quicksand',sans-serif; color:var(--ink); }
    .block-container{ padding-top:2.2rem; padding-bottom:3rem; max-width:1200px; }
    footer{ visibility:hidden; }

    h1,h2,h3{ font-family:'Quicksand',sans-serif !important; color:var(--ink-deep) !important; font-weight:700; letter-spacing:.2px; }

    /* ---- Hero wordmark (the signature) ---- */
    .brand{ text-align:center; margin:0 0 .25rem; }
    .brand-title{
        font-family:'Pacifico',cursive; font-size:3.1rem; line-height:1.05;
        background:linear-gradient(100deg,var(--pink-deep),var(--lav));
        -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent;
        filter:drop-shadow(0 4px 10px rgba(255,143,184,.25));
    }
    .brand-sub{ color:var(--ink); font-weight:500; opacity:.8; margin-top:.1rem; }
    .divider{ text-align:center; color:var(--pink); opacity:.7; letter-spacing:6px; margin:.4rem 0 1.4rem; }

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"]{
        background:linear-gradient(180deg,#FFF4F9 0%, #FBF2FF 100%);
        border-right:1px solid #F6E1EC;
    }
    [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] p{ color:var(--ink); }

    /* ---- Buttons ---- */
    .stButton > button{ border-radius:30px; font-weight:600; letter-spacing:.3px; transition:transform .15s ease, box-shadow .2s ease, background .2s ease; }
    .stButton > button[kind="primary"]{
        background:linear-gradient(100deg,var(--pink),var(--pink-deep)); color:#fff; border:none;
        box-shadow:0 8px 18px rgba(242,92,151,.32); padding:.6rem 1rem;
    }
    .stButton > button[kind="primary"]:hover{ transform:translateY(-2px); box-shadow:0 12px 24px rgba(242,92,151,.42); }
    .stButton > button[kind="secondary"]{
        background:#fff; color:var(--lav); border:1.5px solid #E6D4FF; box-shadow:0 4px 12px rgba(201,166,255,.18);
    }
    .stButton > button[kind="secondary"]:hover{ transform:translateY(-2px); border-color:var(--lav); color:#8A5CF0; }

    /* ---- Inputs ---- */
    .stTextInput input, .stNumberInput input, .stSelectbox div[data-baseweb="select"] > div{
        border-radius:14px !important; border:1.5px solid #F0DDE8 !important; background:#fff !important;
    }
    .stTextInput input:focus, .stNumberInput input:focus{ border-color:var(--pink) !important; box-shadow:0 0 0 3px rgba(255,143,184,.18) !important; }
    label, .stSelectbox label, .stNumberInput label, .stTextInput label{ font-weight:600 !important; color:var(--ink-deep) !important; }

    /* ---- Metric cards ---- */
    .metric-card{
        background:#fff; border-radius:24px; padding:18px 20px; text-align:center;
        border-top:4px solid var(--accent); box-shadow:var(--card-shadow); height:100%;
    }
    .metric-emoji{ font-size:1.7rem; }
    .metric-label{ font-size:.82rem; font-weight:600; letter-spacing:.6px; text-transform:uppercase; opacity:.65; margin-top:.2rem; }
    .metric-value{ font-size:1.7rem; font-weight:700; color:var(--accent); margin-top:.1rem; }

    /* ---- Budget bar (signature element) ---- */
    .bar-card{ background:#fff; border-radius:26px; padding:20px 24px; box-shadow:var(--card-shadow); margin:1.1rem 0 .4rem; }
    .bar-top{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:.55rem; font-weight:600; }
    .bar-top .pct{ font-family:'Pacifico',cursive; color:var(--pink-deep); font-size:1.2rem; }
    .bar-wrap{ position:relative; height:22px; border-radius:20px; background:#FCE7F1; overflow:hidden; box-shadow:inset 0 2px 5px rgba(242,92,151,.12); }
    .bar-fill{ height:100%; border-radius:20px; transition:width .6s cubic-bezier(.2,.8,.2,1); }
    .bar-note{ margin-top:.55rem; font-size:.9rem; opacity:.8; }

    /* ---- Product cards ---- */
    div[data-testid="stVerticalBlockBorderWrapper"]{
        border-radius:22px !important; border:1px solid #F7E3EC !important;
        background:#fff !important; box-shadow:0 6px 18px rgba(255,143,184,.10) !important;
    }
    .prod-img-box{ width:100%; aspect-ratio:1/1; border-radius:16px; overflow:hidden; background:linear-gradient(135deg,#FFF0F6,#F4ECFF); display:flex; align-items:center; justify-content:center; }
    .prod-img{ width:100%; height:100%; object-fit:cover; }
    .prod-img-ph{ font-size:2rem; }
    .prod-name{ font-weight:700; font-size:1.05rem; color:var(--ink-deep); text-decoration:none; }
    a.prod-name:hover{ color:var(--pink-deep); text-decoration:underline; }
    .prod-meta{ margin-top:.35rem; }
    .prod-source{ font-size:.8rem; opacity:.6; }
    .prio-badge{ display:inline-block; padding:3px 11px; border-radius:20px; font-size:.74rem; font-weight:700; }
    .prod-price{ font-family:'Quicksand'; font-weight:700; font-size:1.35rem; color:var(--pink-deep); text-align:right; }

    /* ---- Notes panel ---- */
    .note-line{ margin-bottom:.5rem; line-height:1.5; }

    @media (prefers-reduced-motion: reduce){
        .stButton > button, .bar-fill{ transition:none !important; }
    }
</style>
""", unsafe_allow_html=True)


def priority_badge(p):
    palette = {
        "Must have 💖": ("#FFE3EE", "#D63384"),
        "Wishing 🌸": ("#F0E3FF", "#7B4FC9"),
        "Someday 🌙": ("#E3EEFF", "#3D6FD6"),
    }
    bg, fg = palette.get(p, ("#F0E3FF", "#7B4FC9"))
    return f'<span class="prio-badge" style="background:{bg};color:{fg};">{p}</span>'


# -----------------------------------------------------------------------------
# 6. SIDEBAR — add treasures
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🎀 Atelier")
    st.markdown("&nbsp;", unsafe_allow_html=True)

    def _save_budget():
        save_data()

    st.number_input(
        "💎 Wardrobe budget (₹)",
        min_value=0.0, step=5000.0, key="total_budget",
        on_change=_save_budget, format="%.2f",
    )

    st.markdown("---")
    st.markdown("#### Add a treasure ✨")

    st.text_input("Paste a product link", key="f_url", placeholder="https://...")

    if st.button("✨ Auto-fill from link", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Pop a link in first, lovely 🩷"
        else:
            with st.spinner("Peeking at the page…"):
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
                            f"Found {cur.upper()} {res['price']:,.0f} → ≈ ₹{st.session_state.f_price:,.0f} "
                            f"(approx. rate — do double-check) 🩷"
                        )
                    else:
                        st.session_state.fetch_note = "Found it! Have a peek below and tweak away 🩷"
                else:
                    st.session_state.fetch_note = "Got the name but not the price — just type it in 🤍"
            else:
                st.session_state.fetch_note = (
                    "This boutique keeps its doors locked to robots 🔒 — "
                    "no worries, add the details by hand below 🤍"
                )
        st.rerun()

    if st.session_state.fetch_note:
        st.caption(st.session_state.fetch_note)

    st.text_input("Item name", key="f_name", placeholder="Quilted leather bag")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.2f")
    st.selectbox("How badly do we want it?", PRIORITIES, key="f_priority")

    if st.button("💕 Add to wishlist", type="primary", use_container_width=True):
        if st.session_state.f_name.strip() and st.session_state.f_price > 0:
            url = st.session_state.f_url.strip()
            domain = urlparse(url).netloc.replace("www.", "") if url else "Hand-picked"
            st.session_state.shopping_list.append({
                "name": st.session_state.f_name.strip(),
                "price": float(st.session_state.f_price),
                "url": url if url else "#",
                "source": domain or "Hand-picked",
                "image": st.session_state.f_image,
                "priority": st.session_state.f_priority,
            })
            save_data()
            st.session_state._clear_form = True
            st.session_state._just_added = True
            st.session_state.fetch_note = ""
            st.rerun()
        else:
            st.warning("A name and a price, please 💗")

# -----------------------------------------------------------------------------
# 7. HEADER
# -----------------------------------------------------------------------------
if st.session_state._just_added:
    st.balloons()
    st.session_state._just_added = False

st.markdown(
    "<div class='brand'><div class='brand-title'>My Dreamy Wishlist</div>"
    "<div class='brand-sub'>a soft little place to dream, plan, and treat yourself ✨</div></div>",
    unsafe_allow_html=True,
)
st.markdown("<div class='divider'>✿ ❀ ✿</div>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 8. BUDGET SUMMARY + SIGNATURE BAR
# -----------------------------------------------------------------------------
budget = float(st.session_state.total_budget)
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

c1, c2, c3 = st.columns(3)
c1.markdown(
    f"<div class='metric-card' style='--accent:var(--lav);'><div class='metric-emoji'>💰</div>"
    f"<div class='metric-label'>Budget</div><div class='metric-value'>₹{budget:,.0f}</div></div>",
    unsafe_allow_html=True,
)
c2.markdown(
    f"<div class='metric-card' style='--accent:var(--pink);'><div class='metric-emoji'>🛍️</div>"
    f"<div class='metric-label'>Dreamed up</div><div class='metric-value'>₹{total_spent:,.0f}</div></div>",
    unsafe_allow_html=True,
)
if over:
    c3.markdown(
        f"<div class='metric-card' style='--accent:var(--coral);'><div class='metric-emoji'>🙈</div>"
        f"<div class='metric-label'>Over by</div><div class='metric-value'>₹{abs(remaining):,.0f}</div></div>",
        unsafe_allow_html=True,
    )
else:
    c3.markdown(
        f"<div class='metric-card' style='--accent:var(--mint);'><div class='metric-emoji'>💝</div>"
        f"<div class='metric-label'>Still to play with</div><div class='metric-value'>₹{remaining:,.0f}</div></div>",
        unsafe_allow_html=True,
    )

pct = min(total_spent / budget, 1.0) if budget > 0 else 0.0
fill = ("linear-gradient(90deg,#FF8FB8,#FF6B8A)" if over
        else "linear-gradient(90deg,#FFB3D1,#C9A6FF)")
emoji = "🙈" if over else ("🌷" if pct > 0.85 else "💕")
st.markdown(
    f"<div class='bar-card'><div class='bar-top'><span>Budget glow-meter</span>"
    f"<span class='pct'>{pct*100:.0f}%</span></div>"
    f"<div class='bar-wrap'><div class='bar-fill' style='width:{pct*100:.1f}%;background:{fill};'></div></div>"
    f"<div class='bar-note'>{'A touch over for now ' + emoji + ' — maybe pause one piece.' if over else 'You are doing beautifully ' + emoji}</div></div>",
    unsafe_allow_html=True,
)

st.markdown("<br>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 9. COLLECTION + STYLIST NOTES
# -----------------------------------------------------------------------------
col_items, col_notes = st.columns([2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    st.markdown(f"### 🛍️ Your collection &nbsp;<span style='font-size:.9rem;opacity:.6;'>"
                f"{count} treasure{'s' if count != 1 else ''}</span>", unsafe_allow_html=True)

    if not st.session_state.shopping_list:
        st.info("Your wishlist is a blank page right now — add your first treasure from the left 🎀")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            with st.container(border=True):
                ci, cn, cp = st.columns([1, 3.1, 1.7])
                with ci:
                    if item.get("image"):
                        st.markdown(
                            f"<div class='prod-img-box'><img class='prod-img' src='{item['image']}' "
                            f"onerror=\"this.style.display='none'\"/></div>",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown("<div class='prod-img-box'><span class='prod-img-ph'>🎁</span></div>",
                                    unsafe_allow_html=True)
                with cn:
                    if item["url"] != "#":
                        st.markdown(f"<a class='prod-name' href='{item['url']}' target='_blank'>{item['name']}</a>",
                                    unsafe_allow_html=True)
                    else:
                        st.markdown(f"<span class='prod-name'>{item['name']}</span>", unsafe_allow_html=True)
                    st.markdown(
                        f"<div class='prod-meta'>{priority_badge(item.get('priority','Wishing 🌸'))}"
                        f"<span class='prod-source'> · {item['source']}</span></div>",
                        unsafe_allow_html=True,
                    )
                with cp:
                    st.markdown(f"<div class='prod-price'>₹{item['price']:,.0f}</div>", unsafe_allow_html=True)
                    if st.button("Remove", key=f"del_{index}", use_container_width=True):
                        st.session_state.shopping_list.pop(index)
                        save_data()
                        st.rerun()

with col_notes:
    st.markdown("### 💌 Stylist notes")
    with st.container(border=True):
        if not st.session_state.shopping_list:
            st.markdown("<div class='note-line'>Add a few pieces and I'll whisper little budgeting "
                        "thoughts here 🌸</div>", unsafe_allow_html=True)
        else:
            dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
            avg = total_spent / count
            if over:
                st.markdown(f"<div class='note-line'>We're <b>₹{abs(remaining):,.0f}</b> past the budget — "
                            f"so close, though! 🙈</div>", unsafe_allow_html=True)
                st.markdown(f"<div class='note-line'>Pausing on the <b>{dearest['name']}</b> "
                            f"(₹{dearest['price']:,.0f}) would bring it right back into balance.</div>",
                            unsafe_allow_html=True)
            elif pct > 0.85:
                st.markdown(f"<div class='note-line'>Almost at the top of the budget — "
                            f"<b>₹{remaining:,.0f}</b> left to play with. 🌷</div>", unsafe_allow_html=True)
                st.markdown("<div class='note-line'>Maybe save the next find for payday? 💭</div>",
                            unsafe_allow_html=True)
            else:
                st.markdown("<div class='note-line'>✨ Beautifully balanced — everything sits comfortably "
                            "inside your budget.</div>", unsafe_allow_html=True)
                st.markdown(f"<div class='note-line'>Still <b>₹{remaining:,.0f}</b> of room if something "
                            f"catches your eye. 💕</div>", unsafe_allow_html=True)
            st.markdown("<hr style='border:none;border-top:1px solid #F6E1EC;margin:.6rem 0;'>",
                        unsafe_allow_html=True)
            st.markdown(f"<div class='note-line' style='opacity:.75;font-size:.9rem;'>"
                        f"💗 Most-wanted: <b>{dearest['name']}</b><br>"
                        f"🫧 Average piece: <b>₹{avg:,.0f}</b></div>", unsafe_allow_html=True)
