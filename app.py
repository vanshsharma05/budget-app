import streamlit as st
import re
import json
import os
import uuid
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="The Atelier — Wardrobe Ledger",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="expanded",
)
DATA_FILE = "wishlist_data.json"

# =============================================================================
# 0. SECRETS  (optional pro-scraper keys — see notes at bottom of this file)
# =============================================================================
def _get_secret(name):
    """Read a key from Streamlit secrets first, then environment. Never raises."""
    try:
        if name in st.secrets:
            v = st.secrets[name]
            if v:
                return str(v)
    except Exception:
        pass
    v = os.environ.get(name)
    return v if v else None


SCRAPERAPI_KEY = _get_secret("SCRAPERAPI_KEY")
SCRAPINGBEE_KEY = _get_secret("SCRAPINGBEE_KEY")
ZENROWS_KEY = _get_secret("ZENROWS_KEY")
JINA_KEY = _get_secret("JINA_API_KEY")
HAS_PRO = any([SCRAPERAPI_KEY, SCRAPINGBEE_KEY, ZENROWS_KEY])


# =============================================================================
# 1. PERSISTENCE
# =============================================================================
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("items", [])
            # migrate older entries that have no stable id
            for it in items:
                if "id" not in it:
                    it["id"] = uuid.uuid4().hex[:10]
            return items, float(data.get("budget", 100000.0))
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


# =============================================================================
# 2. CURRENCY
# =============================================================================
RATES = {"INR": 1.0, "USD": 86.0, "EUR": 93.0, "GBP": 109.0, "AED": 23.0,
         "JPY": 0.57, "CNY": 12.0, "SGD": 64.0, "CAD": 62.0, "AUD": 56.0, "CHF": 98.0}


def fmt_inr(n):
    n = round(abs(n))
    s = str(n)
    if len(s) <= 3:
        return f"₹{s}"
    result = s[-3:]
    s = s[:-3]
    while len(s) > 2:
        result = s[-2:] + "," + result
        s = s[:-2]
    if s:
        result = s + "," + result
    return f"₹{result}"


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
    elif "." in s:
        parts = s.split(".")
        # dots used as thousands separators (e.g. European "3.500" -> 3500)
        if len(parts) > 1 and len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            s = s.replace(".", "")
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


# =============================================================================
# 3. SCRAPER — layered cascade
#    Pro APIs (if key present) -> curl_cffi (TLS impersonation) -> Jina ->
#    cloudscraper -> plain requests, with Microlink for OG fallback.
# =============================================================================
BRANDS_RE = (r'Louis Vuitton|Gucci|Prada|Chanel|Dior|Herm[eè]s|Burberry|Versace|Fendi|'
             r'Balenciaga|Bottega Veneta|Cartier|Tiffany|Saint Laurent|YSL|Celine|Valentino|'
             r'Givenchy|Bvlgari|Tom Ford|TataCLiQ|Tata CLiQ|Myntra|Ajio|Nykaa|Official')

_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


# ---- individual fetchers: each returns {"text": ..., "kind": "html"|"markdown"} or None

def _fetch_scraperapi(url, timeout=70):
    if not SCRAPERAPI_KEY:
        return None
    try:
        params = {"api_key": SCRAPERAPI_KEY, "url": url, "render": "true", "country_code": "in"}
        r = requests.get("https://api.scraperapi.com/", params=params, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return {"text": r.text, "kind": "html", "via": "ScraperAPI"}
    except Exception:
        pass
    return None


def _fetch_scrapingbee(url, timeout=70):
    if not SCRAPINGBEE_KEY:
        return None
    try:
        params = {"api_key": SCRAPINGBEE_KEY, "url": url,
                  "render_js": "true", "premium_proxy": "true", "country_code": "in"}
        r = requests.get("https://app.scrapingbee.com/api/v1/", params=params, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return {"text": r.text, "kind": "html", "via": "ScrapingBee"}
    except Exception:
        pass
    return None


def _fetch_zenrows(url, timeout=70):
    if not ZENROWS_KEY:
        return None
    try:
        params = {"apikey": ZENROWS_KEY, "url": url, "js_render": "true", "premium_proxy": "true"}
        r = requests.get("https://api.zenrows.com/v1/", params=params, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return {"text": r.text, "kind": "html", "via": "ZenRows"}
    except Exception:
        pass
    return None


def _fetch_curl_cffi(url, timeout=22):
    """Impersonates a real Chrome TLS/HTTP2 fingerprint. Best free option."""
    try:
        from curl_cffi import requests as creq
    except Exception:
        return None
    for profile in ("chrome", "chrome124", "chrome120"):
        try:
            r = creq.get(url, impersonate=profile, timeout=timeout,
                         headers={"Accept-Language": "en-IN,en;q=0.9"}, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 500:
                return {"text": r.text, "kind": "html", "via": "curl_cffi"}
        except Exception:
            continue
    return None


def _fetch_jina(url, timeout=25):
    """Jina Reader renders JS pages to markdown. Free; key improves reliability."""
    try:
        headers = {"Accept": "text/markdown", "User-Agent": "Mozilla/5.0",
                   "X-Return-Format": "markdown"}
        if JINA_KEY:
            headers["Authorization"] = f"Bearer {JINA_KEY}"
        r = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 100:
            return {"text": r.text, "kind": "markdown", "via": "Jina"}
    except Exception:
        pass
    return None


def _fetch_cloudscraper(url, timeout=20):
    """Solves Cloudflare's JS challenge without a browser."""
    try:
        import cloudscraper
    except Exception:
        return None
    try:
        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False})
        r = scraper.get(url, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 500:
            return {"text": r.text, "kind": "html", "via": "cloudscraper"}
    except Exception:
        pass
    return None


def _fetch_requests(url, timeout=10):
    try:
        r = requests.get(url, headers=_BROWSER_HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and len(r.text) > 500:
            return {"text": r.text, "kind": "html", "via": "requests"}
    except Exception:
        pass
    return None


def _try_microlink(url, timeout=12):
    """Free OG-metadata fetcher — good for image/title on protected sites."""
    try:
        r = requests.get("https://api.microlink.io/", params={"url": url}, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                d = data.get("data", {})
                img = d.get("image")
                if isinstance(img, dict):
                    img = img.get("url")
                return {"title": d.get("title"), "image": img,
                        "description": d.get("description")}
    except Exception:
        pass
    return None


# ---- parsers

_PRICE_PATTERNS = [
    (r"₹\s*([\d][\d.,]*\d)", "INR"),
    (r"Rs\.?\s*([\d][\d.,]*\d)", "INR"),
    (r"INR\s*([\d][\d.,]*\d)", "INR"),
    (r"\$\s*([\d][\d.,]*\d)", "USD"),
    (r"USD\s*([\d][\d.,]*\d)", "USD"),
    (r"€\s*([\d][\d.,]*\d)", "EUR"),
    (r"£\s*([\d][\d.,]*\d)", "GBP"),
]


def _scan_price(text):
    for pat, cur in _PRICE_PATTERNS:
        m = re.search(pat, text)
        if m:
            v = parse_amount(m.group(1))
            if v and 50 < v < 50_000_000:
                return v, cur
    return None, None


def _extract_from_markdown(content):
    r = {"title": None, "price": None, "image": None, "currency": None}

    m = re.search(r'^Title:\s*(.+?)$', content, re.M)
    if m:
        r["title"] = m.group(1).strip()
    if not r["title"]:
        m = re.search(r'^#+\s+(.+?)$', content, re.M)
        if m:
            r["title"] = m.group(1).strip()

    img_matches = re.findall(r'!\[[^\]]*\]\((https?://[^\)]+)\)', content)
    for img in img_matches:
        low = img.lower()
        if any(skip in low for skip in ["logo", "icon", "favicon", "data:image", "sprite"]):
            continue
        if any(ext in low for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            r["image"] = img
            break
    if not r["image"] and img_matches:
        r["image"] = img_matches[0]

    price, cur = _scan_price(content)
    r["price"], r["currency"] = price, cur
    return r


def _extract_from_html(html_str):
    soup = BeautifulSoup(html_str, "html.parser")
    r = {"title": None, "price": None, "image": None, "currency": None}

    def meta(prop, attr="property"):
        tag = soup.find("meta", {attr: prop})
        return tag.get("content", "").strip() if tag and tag.get("content") else None

    r["title"] = meta("og:title") or meta("twitter:title")
    r["image"] = meta("og:image:secure_url") or meta("og:image") or meta("twitter:image")
    pm = meta("product:price:amount") or meta("og:price:amount")
    if pm:
        r["price"] = parse_amount(pm)
    cm = meta("product:price:currency") or meta("og:price:currency")
    if cm:
        r["currency"] = cm

    # JSON-LD structured data
    if not r["price"] or not r["title"]:
        for sc in soup.find_all("script", type="application/ld+json"):
            raw = sc.string or sc.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            def walk(node):
                if isinstance(node, dict):
                    if "Product" in str(node.get("@type", "")):
                        if not r["title"] and node.get("name"):
                            r["title"] = str(node["name"]).strip()
                        img = node.get("image")
                        if img and not r["image"]:
                            if isinstance(img, list):
                                img = img[0]
                            if isinstance(img, dict):
                                img = img.get("url")
                            if img:
                                r["image"] = str(img)
                    offers = node.get("offers")
                    if offers and not r["price"]:
                        offs = offers if isinstance(offers, list) else [offers]
                        for off in offs:
                            if isinstance(off, dict):
                                p = off.get("price") or off.get("lowPrice")
                                if p:
                                    pv = parse_amount(p)
                                    if pv:
                                        r["price"] = pv
                                    if off.get("priceCurrency"):
                                        r["currency"] = off["priceCurrency"]
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for i in node:
                        walk(i)

            walk(data)
            if r["price"] and r["title"]:
                break

    # Embedded app-state JSON (Next.js / generic) as a last structured attempt
    if not r["price"]:
        m = re.search(r'"price"\s*:\s*"?([\d]+(?:[.,]\d+)?)"?', html_str)
        if m:
            pv = parse_amount(m.group(1))
            if pv and 50 < pv < 50_000_000:
                r["price"] = pv
        cm2 = re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', html_str)
        if cm2 and not r["currency"]:
            r["currency"] = cm2.group(1)

    if not r["title"]:
        h1 = soup.find("h1")
        if h1:
            r["title"] = h1.get_text(strip=True)[:100]

    # visible-text price scan
    if not r["price"]:
        text = soup.get_text(" ", strip=True)
        price, cur = _scan_price(text)
        r["price"], r["currency"] = price, cur

    return r


def _is_garbage_title(title):
    if not title or len(title.strip()) < 3:
        return True
    bad = ["page unavailable", "access denied", "forbidden", "404", "page not found",
           "just a moment", "verify you are human", "are you a robot", "checking your browser",
           "service unavailable", "cloudflare", "sucuri", "bot verification", "please wait",
           "captcha", "ddos-guard", "attention required", "unauthorized", "error",
           "blocked", "incapsula", "imperva", "akamai", "request denied"]
    t = title.lower().strip()
    return any(b in t for b in bad)


def _clean_title(title):
    if not title or _is_garbage_title(title):
        return None
    title = re.split(rf'\s*[|\-–—:]\s*(?:{BRANDS_RE})', title, flags=re.I)[0].strip()
    title = re.sub(r'^Buy\s+', '', title, flags=re.I).strip()
    title = re.sub(r'\s+(?:Online|at Best Price).*$', '', title, flags=re.I).strip()
    if " | " in title:
        title = title.split(" | ")[0].strip()
    if _is_garbage_title(title):
        return None
    return title[:80] if title else None


def extract_product_details(url):
    """Run fetchers in order, merging fields until we have title + price."""
    result = {"title": None, "price": None, "image": None, "currency": None,
              "ok": False, "via": []}
    domain = urlparse(url).netloc.lower().replace("www.", "")

    # Pro fetchers first (they no-op without a key), then the best free ones.
    fetchers = [_fetch_scraperapi, _fetch_scrapingbee, _fetch_zenrows,
                _fetch_curl_cffi, _fetch_jina, _fetch_cloudscraper, _fetch_requests]

    for fetch in fetchers:
        if result["price"] and result["title"]:
            break
        got = fetch(url)
        if not got:
            continue
        parsed = (_extract_from_markdown(got["text"]) if got["kind"] == "markdown"
                  else _extract_from_html(got["text"]))
        if parsed.get("title") and _is_garbage_title(parsed["title"]):
            parsed["title"] = None
        filled = False
        for k in ("title", "price", "image", "currency"):
            if parsed.get(k) and not result.get(k):
                result[k] = parsed[k]
                filled = True
        if filled:
            result["via"].append(got.get("via", "?"))

    # Microlink as an OG-metadata supplement
    if not result["title"] or not result["image"] or not result["price"]:
        ml = _try_microlink(url)
        if ml:
            if ml.get("title") and not _is_garbage_title(ml["title"]) and not result["title"]:
                result["title"] = ml["title"]
                result["via"].append("Microlink")
            if not result["image"] and ml.get("image"):
                result["image"] = ml["image"]
            if not result["price"] and ml.get("description"):
                price, cur = _scan_price(ml["description"])
                if price:
                    result["price"], result["currency"] = price, cur

    # Louis Vuitton image fallback from SKU in URL
    if not result["image"] and "louisvuitton.com" in domain:
        sku = re.search(r'/([A-Z]{1,3}\d{4,6})', url)
        if sku:
            result["image"] = (f"https://in.louisvuitton.com/images/is/image/lv/1/PP_VP_L/"
                               f"louis-vuitton--{sku.group(1)}_PM2_Front%20view.jpg?wid=400&hei=400")

    result["title"] = _clean_title(result.get("title"))
    if result["price"] and not result["currency"]:
        result["currency"] = "INR"
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
PRIORITY_RANK = {"Must have": 0, "Considering": 1, "Someday": 2}

_defaults = {"f_url": "", "f_name": "", "f_price": 0.0, "f_image": "",
             "f_priority": "Considering", "fetch_note": "", "fetch_kind": "",
             "arrange": "Priority", "_clear_form": False}
for k, v in _defaults.items():
    st.session_state.setdefault(k, v)

if st.session_state._clear_form:
    for k in ("f_url", "f_name", "f_image", "fetch_note", "fetch_kind"):
        st.session_state[k] = ""
    st.session_state.f_price = 0.0
    st.session_state.f_priority = "Considering"
    st.session_state._clear_form = False


# =============================================================================
# 5. STYLING — couture atelier: bone paper, ink, antique gold, garnet
# =============================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Marcellus&family=Marcellus+SC&family=Inter:wght@400;500;600;700&display=swap');

:root{
  --paper:#F2ECE1;
  --paper-2:#FBF8F2;
  --card:#FFFFFF;
  --ink:#211A14;
  --ink-soft:#6A5C4F;
  --ink-faint:#938577;
  --line:#DACDBA;
  --line-soft:#E7DDCE;
  --gold:#9C7C3C;
  --gold-deep:#7A5E27;
  --gold-light:#C7A964;
  --gold-wash:#EFE4CC;
  --garnet:#7E2A33;
  --garnet-soft:#A14852;
  --garnet-bg:#F4E3E1;
  --sage:#46584A;
}

.stApp{ background:var(--paper) !important; }
html{ font-size:16px; }
html, body, [class*="css"]{
  font-family:'Inter',-apple-system,system-ui,sans-serif !important;
  color:var(--ink) !important;
}
.block-container{ padding:2rem 1.25rem 2.5rem !important; max-width:1100px; }
footer, #MainMenu, .stDeployButton{ display:none !important; visibility:hidden !important; }
header[data-testid="stHeader"]{ background:transparent !important; }

h1,h2,h3{ font-family:'Marcellus',Georgia,serif !important; color:var(--ink) !important; font-weight:400 !important; letter-spacing:0.01em; }

/* ---------- HEADER ---------- */
.atelier-head{ text-align:center; margin:0 auto 2rem; }
.eyebrow{
  font-family:'Inter'; font-size:11px; font-weight:700; letter-spacing:0.34em;
  text-transform:uppercase; color:var(--gold-deep); margin-bottom:14px;
  display:inline-flex; align-items:center; gap:12px;
}
.eyebrow::before, .eyebrow::after{
  content:""; width:34px; height:1px; background:var(--gold); opacity:.6;
}
.atelier-title{ font-family:'Marcellus',serif; font-size:48px; line-height:1; color:var(--ink);
  margin:0; letter-spacing:0.02em; }
.atelier-sub{ font-family:'Inter'; font-size:12px; font-weight:600; letter-spacing:0.22em;
  text-transform:uppercase; color:var(--ink-soft); margin-top:14px; }

/* ---------- METRIC TAGS ---------- */
.tags{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:20px; }
.tag{
  position:relative; background:var(--card); border:1px solid var(--line-soft);
  border-radius:4px; padding:26px 20px 22px; text-align:center;
  box-shadow:0 1px 2px rgba(33,26,20,.04);
}
.tag::before{ content:""; position:absolute; top:14px; left:50%; transform:translateX(-50%);
  width:7px; height:7px; border-radius:50%; background:var(--paper); border:1px solid var(--line); }
.tag .lab{ font-family:'Inter'; font-size:11px; font-weight:700; letter-spacing:0.16em;
  text-transform:uppercase; color:var(--ink-soft); margin:8px 0 12px; }
.tag .val{ font-family:'Marcellus',serif; font-size:30px; color:var(--ink); line-height:1; }
.tag.warn{ background:var(--garnet-bg); border-color:#D7B3B2; }
.tag.warn .lab{ color:var(--garnet); }
.tag.warn .val{ color:var(--garnet); }

/* ---------- SIGNATURE: TAILOR'S TAPE METER ---------- */
.tape{ background:var(--card); border:1px solid var(--line-soft); border-radius:4px;
  padding:22px 26px 20px; margin-bottom:26px; box-shadow:0 1px 2px rgba(33,26,20,.04); }
.tape-top{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:14px; }
.tape-top .lab{ font-family:'Inter'; font-size:11px; font-weight:700; letter-spacing:0.16em;
  text-transform:uppercase; color:var(--ink-soft); }
.tape-top .pct{ font-family:'Marcellus',serif; font-size:22px; color:var(--gold-deep); }
.tape-top .pct.over{ color:var(--garnet); }
.tape-track{ position:relative; height:30px; border-radius:3px; overflow:hidden;
  background:var(--paper-2);
  border:1px solid var(--line);
  background-image:
    repeating-linear-gradient(90deg, transparent 0, transparent 19px, var(--line) 19px, var(--line) 20px),
    repeating-linear-gradient(90deg, transparent 0, transparent 99px, var(--gold-light) 99px, var(--gold-light) 100px);
}
.tape-fill{ position:absolute; top:0; left:0; height:100%;
  background:linear-gradient(180deg, var(--gold-light), var(--gold));
  opacity:.92; transition:width .5s cubic-bezier(.2,.7,.2,1);
  box-shadow:inset 0 -2px 0 rgba(0,0,0,.06); }
.tape-fill.over{ background:linear-gradient(180deg, var(--garnet-soft), var(--garnet)); }
.tape-marker{ position:absolute; top:-3px; width:2px; height:36px; background:var(--ink);
  transition:left .5s cubic-bezier(.2,.7,.2,1); }
.tape-marker::after{ content:""; position:absolute; top:-5px; left:-4px;
  border-left:5px solid transparent; border-right:5px solid transparent; border-top:6px solid var(--ink); }
.tape-ends{ display:flex; justify-content:space-between; margin-top:9px;
  font-family:'Inter'; font-size:11px; font-weight:600; color:var(--ink-faint); letter-spacing:.04em; }
.tape-note{ font-family:'Inter'; font-size:13.5px; color:var(--ink-soft); margin-top:13px;
  line-height:1.55; font-weight:500; }

/* ---------- SECTION LABELS ---------- */
.sec{ font-family:'Inter'; font-size:11px; font-weight:700; letter-spacing:0.18em;
  text-transform:uppercase; color:var(--ink-soft); margin:0 0 16px;
  display:flex; align-items:center; gap:12px; }
.sec::after{ content:""; flex:1; height:1px; background:var(--line-soft); }

/* ---------- DOCKET CARDS ---------- */
div[data-testid="stVerticalBlockBorderWrapper"]{
  border:1px solid var(--line-soft) !important; border-left:3px dashed var(--gold) !important;
  border-radius:4px !important; background:var(--card) !important;
  box-shadow:0 1px 2px rgba(33,26,20,.04) !important;
}
.docket{ display:flex; align-items:center; gap:16px; width:100%; }
.thumb{ width:62px; height:62px; border-radius:3px; overflow:hidden; flex-shrink:0;
  background:var(--gold-wash); display:flex; align-items:center; justify-content:center;
  border:1px solid var(--line-soft); }
.thumb img{ width:100%; height:100%; object-fit:cover; display:block; }
.thumb-ph{ font-size:20px; color:var(--gold-deep); }
.dk-mid{ flex:1; min-width:0; }
.p-name{ font-family:'Marcellus',serif; font-size:18px; color:var(--ink);
  text-decoration:none; line-height:1.25; }
a.p-name:hover{ color:var(--gold-deep); }
.dk-meta{ margin-top:8px; display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.p-src{ font-family:'Inter'; font-size:11.5px; color:var(--ink-faint); font-weight:500;
  letter-spacing:.02em; }
.p-price{ font-family:'Marcellus',serif; font-size:21px; color:var(--ink);
  text-align:right; white-space:nowrap; padding-left:6px; }

/* fabric-swatch priority chips */
.swatch{ display:inline-flex; align-items:center; gap:7px; font-family:'Inter';
  font-size:10.5px; font-weight:700; letter-spacing:0.08em; text-transform:uppercase;
  color:var(--ink-soft); }
.swatch .dot{ width:11px; height:11px; border-radius:2px; display:inline-block;
  border:1px solid rgba(0,0,0,.12); }

/* ---------- STYLIST NOTES ---------- */
.notes{ background:var(--card); border:1px solid var(--line-soft); border-radius:4px;
  padding:22px; box-shadow:0 1px 2px rgba(33,26,20,.04); }
.notes.warn{ background:var(--garnet-bg); border-color:#D7B3B2; }
.n-text{ font-family:'Inter'; font-size:13.5px; line-height:1.7; color:var(--ink-soft);
  margin-bottom:9px; font-weight:500; }
.notes.warn .n-text{ color:var(--garnet); }
.notes b{ color:var(--ink); font-weight:700; }
.notes.warn b{ color:var(--garnet); }
.notes hr{ border:none; border-top:1px solid var(--line-soft); margin:14px 0; }

/* ---------- SIDEBAR ---------- */
[data-testid="stSidebar"]{ background:var(--paper-2) !important; border-right:1px solid var(--line) !important; }
[data-testid="stSidebar"] > div{ padding:1.6rem 1.3rem !important; }
[data-testid="stSidebar"] h3{ font-family:'Marcellus',serif !important; font-size:25px !important;
  color:var(--ink) !important; margin-bottom:4px !important; }
[data-testid="stSidebar"] h4{ font-family:'Inter' !important; font-size:11px !important;
  font-weight:700 !important; letter-spacing:0.16em !important; text-transform:uppercase !important;
  color:var(--gold-deep) !important; margin:22px 0 12px !important; }
[data-testid="stSidebar"] label{ font-family:'Inter' !important; font-weight:600 !important;
  font-size:13px !important; color:var(--ink) !important; }

.scrape-mode{ font-family:'Inter'; font-size:12px; font-weight:600; padding:11px 14px;
  border-radius:4px; margin:10px 0 4px; display:flex; align-items:center; gap:9px; line-height:1.35; }
.scrape-mode.pro{ background:var(--gold-wash); color:var(--gold-deep); border:1px solid var(--gold-light); }
.scrape-mode.std{ background:#EFEAE0; color:var(--ink-soft); border:1px solid var(--line); }

[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{
  background:var(--card) !important; border:1px solid var(--line) !important; border-radius:4px !important;
  padding:11px 13px !important; font-size:12.5px !important; line-height:1.5 !important;
  color:var(--ink) !important; font-weight:600 !important; margin:10px 0 12px !important;
  white-space:normal !important; display:block !important; }

[data-testid="stSpinner"] p, [data-testid="stSpinner"] span, .stSpinner p{
  color:var(--ink) !important; font-weight:600 !important; font-size:13.5px !important; }
[data-testid="stSpinner"] i, .stSpinner i{ border-top-color:var(--gold) !important; border-left-color:var(--gold) !important; }

[data-baseweb="popover"] li, [data-baseweb="menu"] li{ color:var(--ink) !important; font-weight:500 !important; }

/* ---------- INPUTS ---------- */
.stTextInput input, .stNumberInput input{
  border-radius:4px !important; border:1px solid var(--line) !important; background:var(--card) !important;
  font-size:15px !important; color:var(--ink) !important; padding:12px 14px !important; min-height:46px !important; }
.stTextInput input::placeholder, .stNumberInput input::placeholder{ color:var(--ink-faint) !important; }
.stTextInput input:focus, .stNumberInput input:focus{
  border-color:var(--gold) !important; box-shadow:0 0 0 3px rgba(156,124,60,.18) !important; }
.stSelectbox div[data-baseweb="select"] > div{
  border-radius:4px !important; border:1px solid var(--line) !important; background:var(--card) !important;
  min-height:46px !important; font-size:15px !important; color:var(--ink) !important; }
.stSelectbox div[data-baseweb="select"] span{ color:var(--ink) !important; font-weight:500 !important; }

/* ---------- BUTTONS ---------- */
.stButton > button{ border-radius:4px !important; font-family:'Inter' !important; font-weight:600 !important;
  font-size:14px !important; letter-spacing:.02em !important; padding:12px 18px !important;
  min-height:48px !important; transition:all .18s ease !important; }
.stButton > button[kind="primary"]{ background:var(--ink) !important; color:var(--paper) !important;
  border:none !important; box-shadow:0 2px 5px rgba(33,26,20,.14); }
.stButton > button[kind="primary"]:hover{ background:var(--gold-deep) !important; transform:translateY(-1px);
  box-shadow:0 5px 12px rgba(122,94,39,.24); }
.stButton > button:not([kind="primary"]){ background:var(--card) !important; color:var(--ink-soft) !important;
  border:1px solid var(--line) !important; min-height:40px !important; font-size:12.5px !important; }
.stButton > button:not([kind="primary"]):hover{ border-color:var(--garnet) !important;
  color:var(--garnet) !important; background:var(--garnet-bg) !important; }

.stAlert{ border-radius:4px !important; font-size:13.5px !important; font-weight:500 !important; }
hr{ border:none !important; border-top:1px solid var(--line-soft) !important; margin:1rem 0 !important; }

.empty{ background:var(--card); border:1px dashed var(--line); border-radius:4px; padding:34px 26px;
  text-align:center; }
.empty .ic{ font-size:26px; color:var(--gold); }
.empty .t{ font-family:'Marcellus',serif; font-size:19px; color:var(--ink); margin:10px 0 6px; }
.empty .s{ font-family:'Inter'; font-size:13px; color:var(--ink-soft); font-weight:500; }

.signoff{ text-align:center; padding:3rem 0 1rem; font-family:'Inter'; font-size:12.5px;
  color:var(--ink-faint); font-weight:500; letter-spacing:.04em; }
.signoff .nm{ color:var(--gold-deep); font-family:'Marcellus',serif; font-size:14px; letter-spacing:.04em; }

/* ---------- RESPONSIVE ---------- */
@media (max-width:768px){
  .block-container{ padding:1.25rem .8rem 1.5rem !important; }
  .tags{ grid-template-columns:1fr !important; gap:11px !important; }
  .tag{ padding:20px 18px 18px !important; }
  .tag .val{ font-size:26px !important; }
  .atelier-title{ font-size:34px !important; }
  .tape{ padding:18px 18px 16px !important; }
  .p-name{ font-size:16px !important; }
  .p-price{ font-size:19px !important; }
  .thumb{ width:52px; height:52px; }
}
@media (max-width:480px){
  .atelier-title{ font-size:29px !important; }
  .tag .val{ font-size:23px !important; }
  .docket{ gap:12px; }
}
@media (max-width:640px){ [data-testid="column"]{ padding:0 4px !important; } }
</style>
""", unsafe_allow_html=True)

SWATCH_COLORS = {"Must have": "#7E2A33", "Considering": "#9C7C3C", "Someday": "#8A7E9C"}


def swatch(p):
    c = SWATCH_COLORS.get(p, "#9C7C3C")
    return f'<span class="swatch"><span class="dot" style="background:{c};"></span>{p}</span>'


# =============================================================================
# 6. SIDEBAR
# =============================================================================
with st.sidebar:
    st.markdown("### The Atelier")
    if HAS_PRO:
        st.markdown('<div class="scrape-mode pro">◆ Enhanced fetch active — pro proxy connected</div>',
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="scrape-mode std">◇ Standard fetch — works on most stores. '
                    'Add a free key for locked luxury sites.</div>', unsafe_allow_html=True)

    def _sb():
        save_data()

    st.number_input("Wardrobe budget (₹)", min_value=0.0, step=5000.0,
                    key="total_budget", on_change=_sb, format="%.0f")

    st.markdown("#### Add a piece")
    st.text_input("Product link", key="f_url", placeholder="https://...")

    if st.button("Fetch details", type="secondary", use_container_width=True):
        url = st.session_state.f_url.strip()
        if not url:
            st.session_state.fetch_note = "Paste a product link first."
            st.session_state.fetch_kind = "warn"
        elif not url.startswith(("http://", "https://")):
            st.session_state.fetch_note = "The link must start with https://"
            st.session_state.fetch_kind = "warn"
        else:
            with st.spinner("Reading the boutique page…"):
                res = extract_product_details(url)
            if res["ok"]:
                if res["title"]:
                    st.session_state.f_name = res["title"]
                if res["image"]:
                    st.session_state.f_image = res["image"]
                if res["price"]:
                    cur = (res["currency"] or "INR").upper()
                    rate = RATES.get(cur, 1.0)
                    st.session_state.f_price = round(res["price"] * rate, 2)
                    if cur != "INR":
                        st.session_state.fetch_note = (
                            f"Found it. {cur} {res['price']:,.0f} ≈ {fmt_inr(st.session_state.f_price)}. "
                            "Check the details, then add.")
                    else:
                        st.session_state.fetch_note = "Found it — check the details below, then add."
                    st.session_state.fetch_kind = "ok"
                else:
                    st.session_state.fetch_note = "Got the name. Add the price by hand below."
                    st.session_state.fetch_kind = "warn"
            else:
                st.session_state.fetch_note = ("This store blocked the reader. "
                                               "Copy the name and price in by hand below.")
                st.session_state.fetch_kind = "warn"
        st.rerun()

    if st.session_state.fetch_note:
        if st.session_state.fetch_kind == "ok":
            st.success(st.session_state.fetch_note, icon="✦")
        else:
            st.warning(st.session_state.fetch_note, icon="✎")

    st.text_input("Item name", key="f_name", placeholder="e.g. Ombré Nomade 100ml")
    st.number_input("Price (₹)", min_value=0.0, step=500.0, key="f_price", format="%.0f")
    st.text_input("Image link (optional)", key="f_image", placeholder="Paste image URL")
    st.selectbox("Priority", PRIORITIES, key="f_priority")

    if st.button("Add to the collection", type="primary", use_container_width=True):
        if st.session_state.f_name.strip() and st.session_state.f_price > 0:
            url = st.session_state.f_url.strip()
            domain = urlparse(url).netloc.replace("www.", "") if url else "Added by hand"
            st.session_state.shopping_list.append({
                "id": uuid.uuid4().hex[:10],
                "name": st.session_state.f_name.strip(),
                "price": float(st.session_state.f_price),
                "url": url if url else "#",
                "source": domain or "Added by hand",
                "image": st.session_state.f_image.strip(),
                "priority": st.session_state.f_priority,
            })
            save_data()
            st.session_state._clear_form = True
            st.rerun()
        else:
            st.warning("Add both a name and a price.", icon="✎")


# =============================================================================
# 7. MAIN
# =============================================================================
st.markdown(
    '<div class="atelier-head">'
    '<div class="eyebrow">Vansh · for Didi</div>'
    '<h1 class="atelier-title">The Atelier</h1>'
    '<div class="atelier-sub">A luxury wardrobe investment ledger</div>'
    '</div>', unsafe_allow_html=True)

budget = float(st.session_state.total_budget)
total_spent = sum(i["price"] for i in st.session_state.shopping_list)
remaining = budget - total_spent
over = remaining < 0

st.markdown(
    f'<div class="tags">'
    f'<div class="tag"><div class="lab">Wardrobe budget</div><div class="val">{fmt_inr(budget)}</div></div>'
    f'<div class="tag"><div class="lab">Allocated</div><div class="val">{fmt_inr(total_spent)}</div></div>'
    f'<div class="tag{" warn" if over else ""}"><div class="lab">{"Over by" if over else "Still available"}</div>'
    f'<div class="val">{fmt_inr(abs(remaining))}</div></div>'
    f'</div>', unsafe_allow_html=True)

pct = (total_spent / budget) if budget > 0 else 0.0
fill_pct = min(pct, 1.0) * 100
note = ("You're over the budget — easing off one piece will bring the ledger back in line."
        if over else
        ("Almost at capacity — there's a little room left." if pct > 0.85 else
         "Beautifully balanced — there's still room to add."))
st.markdown(
    f'<div class="tape">'
    f'<div class="tape-top"><span class="lab">Budget allocated</span>'
    f'<span class="pct{" over" if over else ""}">{pct*100:.0f}%</span></div>'
    f'<div class="tape-track">'
    f'<div class="tape-fill {"over" if over else ""}" style="width:{fill_pct:.1f}%"></div>'
    f'<div class="tape-marker" style="left:calc({fill_pct:.1f}% - 1px)"></div>'
    f'</div>'
    f'<div class="tape-ends"><span>₹0</span><span>{fmt_inr(budget)}</span></div>'
    f'<div class="tape-note">{note}</div>'
    f'</div>', unsafe_allow_html=True)

col_items, col_notes = st.columns([2.2, 1])

with col_items:
    count = len(st.session_state.shopping_list)
    head_l, head_r = st.columns([1.6, 1])
    with head_l:
        st.markdown(f'<p class="sec">The collection · {count} piece{"s" if count != 1 else ""}</p>',
                    unsafe_allow_html=True)
    with head_r:
        if count:
            st.selectbox("Arrange by", ["Priority", "Price (high to low)", "Recently added"],
                         key="arrange", label_visibility="collapsed")

    if not st.session_state.shopping_list:
        st.markdown('<div class="empty"><div class="ic">✦</div>'
                    '<div class="t">The collection is waiting</div>'
                    '<div class="s">Add your first piece from the panel on the left.</div></div>',
                    unsafe_allow_html=True)
    else:
        display = list(st.session_state.shopping_list)
        if st.session_state.arrange == "Priority":
            display.sort(key=lambda x: PRIORITY_RANK.get(x.get("priority", "Considering"), 1))
        elif st.session_state.arrange == "Price (high to low)":
            display.sort(key=lambda x: x["price"], reverse=True)
        else:
            display = list(reversed(display))

        for item in display:
            with st.container(border=True):
                c_main, c_btn = st.columns([5, 1])
                with c_main:
                    if item.get("image"):
                        thumb = (f'<div class="thumb"><img src="{item["image"]}" '
                                 f'onerror="this.style.display=\'none\';this.parentElement.innerHTML='
                                 f'\'<span class=thumb-ph>✦</span>\'"/></div>')
                    else:
                        thumb = '<div class="thumb"><span class="thumb-ph">✦</span></div>'
                    if item["url"] != "#":
                        name_html = f'<a class="p-name" href="{item["url"]}" target="_blank">{item["name"]}</a>'
                    else:
                        name_html = f'<span class="p-name">{item["name"]}</span>'
                    st.markdown(
                        f'<div class="docket">{thumb}'
                        f'<div class="dk-mid">{name_html}'
                        f'<div class="dk-meta">{swatch(item.get("priority","Considering"))}'
                        f'<span class="p-src">· {item["source"]}</span></div></div>'
                        f'<div class="p-price">{fmt_inr(item["price"])}</div>'
                        f'</div>', unsafe_allow_html=True)
                with c_btn:
                    if st.button("Remove", key=f"del_{item['id']}", use_container_width=True):
                        st.session_state.shopping_list = [
                            x for x in st.session_state.shopping_list if x.get("id") != item["id"]]
                        save_data()
                        st.rerun()

with col_notes:
    st.markdown('<p class="sec">Stylist notes</p>', unsafe_allow_html=True)
    if not st.session_state.shopping_list:
        st.markdown('<div class="notes"><p class="n-text">Add pieces to see budget insights '
                    'and a gentle read on your curation.</p></div>', unsafe_allow_html=True)
    else:
        dearest = max(st.session_state.shopping_list, key=lambda x: x["price"])
        avg = total_spent / count
        must = [i for i in st.session_state.shopping_list if i.get("priority") == "Must have"]
        w = "warn" if over else ""
        n = f'<div class="notes {w}">'
        if over:
            n += (f'<p class="n-text"><b>Over budget by {fmt_inr(abs(remaining))}.</b></p>'
                  f'<p class="n-text">Pausing on <b>{dearest["name"]}</b> ({fmt_inr(dearest["price"])}) '
                  f'would bring the ledger back into balance.</p>')
        elif pct > 0.85:
            n += (f'<p class="n-text">Nearly at capacity, with <b>{fmt_inr(remaining)}</b> left.</p>'
                  f'<p class="n-text">Worth holding before the next addition.</p>')
        else:
            n += (f'<p class="n-text">The curation is well balanced.</p>'
                  f'<p class="n-text"><b>{fmt_inr(remaining)}</b> of room remaining.</p>')
        n += '<hr>'
        n += (f'<p class="n-text" style="font-size:12.5px;">'
              f'Centrepiece &nbsp;<b>{dearest["name"]}</b> — {fmt_inr(dearest["price"])}<br>'
              f'Average per piece &nbsp;<b>{fmt_inr(avg)}</b><br>'
              f'Must-haves &nbsp;<b>{len(must)}</b> of {count}</p></div>')
        st.markdown(n, unsafe_allow_html=True)

st.markdown('<div class="signoff">Made with care by <span class="nm">Vansh</span> · for Didi Gupta</div>',
            unsafe_allow_html=True)
