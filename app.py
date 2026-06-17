import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re

# -----------------------------------------------------------------------------
# 1. LUXURY UI CONFIGURATION & CUSTOM CSS
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Curated Wishlist", page_icon="✨", layout="wide", initial_sidebar_state="expanded")

# Inject Custom CSS for an elegant, feminine, editorial aesthetic
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Outfit:wght@300;400;500&display=swap');
    
    /* Global Font Settings */
    html, body, [class*="css"] {
        font-family: 'Outfit', sans-serif;
        color: #5A4A42; 
    }
    
    /* Elegant Serif Headers */
    h1, h2, h3 {
        font-family: 'Playfair Display', serif !important;
        color: #3B2F2F !important;
        font-weight: 500;
        letter-spacing: 0.5px;
    }
    
    /* Soft Blush Backgrounds */
    .stApp {
        background-color: #FFF9FA;
    }
    
    /* Sidebar Styling */
    [data-testid="stSidebar"] {
        background-color: #FDF5F6;
        border-right: 1px solid #F5E6E8;
    }
    
    /* Chic Buttons */
    div.stButton > button:first-child {
        background-color: #E8C1C5;
        color: white;
        border: none;
        border-radius: 30px;
        padding: 10px 24px;
        font-weight: 500;
        letter-spacing: 1px;
        transition: all 0.3s ease;
    }
    div.stButton > button:first-child:hover {
        background-color: #DDA7B0;
        color: white;
        box-shadow: 0 4px 12px rgba(221, 167, 176, 0.4);
    }
    
    /* Metric Cards */
    div[data-testid="metric-container"] {
        background-color: white;
        border: 1px solid #F5E6E8;
        border-radius: 15px;
        padding: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.02);
    }
    
    /* Product Cards */
    div[data-testid="stVerticalBlock"] div[style*="border"] {
        border: 1px solid #F5E6E8 !important;
        border-radius: 15px !important;
        background-color: white;
        box-shadow: 0 2px 10px rgba(0,0,0,0.01);
    }
    
    /* Input Fields */
    .stTextInput>div>div>input, .stNumberInput>div>div>input {
        border-radius: 10px;
        border: 1px solid #EAD8DB;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state memory
if "shopping_list" not in st.session_state:
    st.session_state.shopping_list = []
if "total_budget" not in st.session_state:
    st.session_state.total_budget = 100000.0  # Default 1 Lakh INR

# -----------------------------------------------------------------------------
# 2. STEALTH SCRAPER (WITH GRACEFUL FALLBACK)
# -----------------------------------------------------------------------------
def extract_product_details(url):
    """Attempts to scrape, but fails gracefully for high-security sites."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
        "Accept-Language": "en-US,en;q=0.9"
    }
    try:
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code != 200:
            return None, None
            
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Generic Title Fetching
        title = soup.find("h1")
        title_text = title.get_text().strip() if title else "Couture Item"
                
        # Generic Price Fetching
        price = None
        price_tags = soup.find_all(text=re.compile(r'₹|INR|Rs\.?'))
        for tag in price_tags:
            cleaned = re.sub(r'[^\d.]', '', tag)
            if cleaned:
                price = float(cleaned)
                break
                    
        return title_text[:65] + "..." if len(title_text) > 65 else title_text, price
    except Exception:
        return None, None

# -----------------------------------------------------------------------------
# 3. SIDEBAR: WARDROBE PLANNER
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## ✨ Atelier Controls")
    st.markdown("---")
    
    st.session_state.total_budget = st.number_input(
        "💎 Set Wardrobe Budget (₹)", 
        min_value=0.0, 
        value=float(st.session_state.total_budget), 
        step=5000.0
    )
    
    st.markdown("---")
    st.markdown("### 🤍 Add to Lookbook")
    st.caption("High-end sites block automated tracking. Add your pieces manually for a flawless experience.")
    
    with st.form("add_product_form", clear_on_submit=True):
        manual_name = st.text_input("Item Name (e.g., Quilted Leather Bag):")
        manual_price = st.number_input("Price (₹):", min_value=0.0, step=1000.0)
        product_url = st.text_input("🔗 Link to item (Optional):")
            
        submitted = st.form_submit_button("Add to Collection", use_container_width=True)
        
        if submitted:
            if manual_name and manual_price > 0:
                domain = urlparse(product_url).netloc.replace("www.", "") if product_url else "Curated Piece"
                
                st.session_state.shopping_list.append({
                    "name": manual_name,
                    "price": manual_price,
                    "url": product_url if product_url else "#",
                    "source": domain
                })
                st.rerun()
            else:
                st.error("Please enter the item name and price.")

# -----------------------------------------------------------------------------
# 4. MAIN DASHBOARD: THE LOOKBOOK
# -----------------------------------------------------------------------------
st.markdown("<h1>Curated Wardrobe Wishlist</h1>", unsafe_allow_html=True)
st.markdown("Plan your luxury purchases and track your investment pieces.")

# Financial Calculations
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining_budget = st.session_state.total_budget - total_spent

# High-End Metric Cards
m1, m2, m3 = st.columns(3)
m1.metric("Total Budget", f"₹ {st.session_state.total_budget:,.2f}")
m2.metric("Allocated Funds", f"₹ {total_spent:,.2f}")

if remaining_budget >= 0:
    m3.metric("Available Balance", f"₹ {remaining_budget:,.2f}")
else:
    m3.metric("Over Budget", f"₹ {abs(remaining_budget):,.2f}")

st.markdown("<br>", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 5. PRODUCT LIST & SUGGESTIONS
# -----------------------------------------------------------------------------
col_items, col_sugg = st.columns([2, 1])

with col_items:
    st.markdown("### 🛍️ Your Collection")
    if not st.session_state.shopping_list:
        st.info("Your lookbook is currently empty. Begin curating from the sidebar!")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            with st.container(border=True):
                r1, r2, r3 = st.columns([4, 2, 1])
                with r1:
                    if item['url'] != "#":
                        st.markdown(f"**[{item['name']}]({item['url']})**")
                    else:
                        st.markdown(f"**{item['name']}**")
                    st.caption(f"{item['source']}")
                with r2:
                    st.markdown(f"### ₹{item['price']:,.2f}")
                with r3:
                    if st.button("✕", key=f"del_{index}", help="Remove item"):
                        st.session_state.shopping_list.pop(index)
                        st.rerun()

with col_sugg:
    st.markdown("### 💌 Stylist Notes")
    with st.container(border=True):
        if not st.session_state.shopping_list:
            st.write("Add pieces to receive budgeting insights.")
        elif remaining_budget < 0:
            st.markdown(f"**Re-evaluate needed.** You are over your allowance by **₹{abs(remaining_budget):,.2f}**.")
            expensive_item = max(st.session_state.shopping_list, key=lambda x: x["price"])
            st.markdown(f"Consider pausing on the **{expensive_item['name']}** to balance your curation.")
        else:
            st.markdown("✨ **Perfectly balanced.** Your current curation sits beautifully within your financial limits.")
