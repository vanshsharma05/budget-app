import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import re

# -----------------------------------------------------------------------------
# 1. PROFESSIONAL UI CONFIGURATION
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Smart Budget Shopper", page_icon="🛍️", layout="wide", initial_sidebar_state="expanded")

# Initialize session state memory
if "shopping_list" not in st.session_state:
    st.session_state.shopping_list = []
if "total_budget" not in st.session_state:
    st.session_state.total_budget = 50000.0  # Default 50k INR

# -----------------------------------------------------------------------------
# 2. UPGRADED STEALTH SCRAPER (INDIAN E-COMMERCE FOCUS)
# -----------------------------------------------------------------------------
def extract_product_details(url):
    """Upgraded scraper with stealth headers to bypass basic bot protection."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": "https://www.google.com/"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return None, None
            
        soup = BeautifulSoup(response.content, "html.parser")
        
        # 1. Fetch Title
        title = "Unknown Product"
        title_tags = [
            soup.find("span", {"id": "productTitle"}), # Amazon
            soup.find("span", class_="B_NuCI"), # Old Flipkart
            soup.find("span", class_="VU-ZEz"), # New Flipkart
            soup.find("h1", class_="pdp-title"), # Myntra
            soup.find("h1") # Generic
        ]
        for tag in title_tags:
            if tag:
                title = tag.get_text().strip()
                break
                
        # 2. Fetch Price (INR Focus)
        price = None
        price_tags = [
            soup.find("span", class_="a-price-whole"), # Amazon
            soup.find("div", class_="_30jeq3 _16Jk6d"), # Old Flipkart
            soup.find("div", class_="Nx9bqj CxhGGd"), # New Flipkart
            soup.find("span", class_="pdp-price"), # Myntra
            soup.find("span", class_="price") # Generic
        ]
        
        for tag in price_tags:
            if tag:
                raw_price = tag.get_text().strip()
                # Strip commas and ₹ signs, keep digits
                cleaned_price = re.sub(r'[^\d.]', '', raw_price)
                if cleaned_price:
                    price = float(cleaned_price)
                    break
                    
        return title[:65] + "..." if len(title) > 65 else title, price
        
    except Exception:
        return None, None

# -----------------------------------------------------------------------------
# 3. SIDEBAR: CONTROL PANEL
# -----------------------------------------------------------------------------
with st.sidebar:
    st.title("⚙️ Control Panel")
    st.markdown("---")
    
    # Budget Input
    st.session_state.total_budget = st.number_input(
        "💰 Set Total Budget (₹)", 
        min_value=0.0, 
        value=float(st.session_state.total_budget), 
        step=1000.0
    )
    
    st.markdown("---")
    st.subheader("➕ Add Product")
    
    with st.form("add_product_form", clear_on_submit=True):
        product_url = st.text_input("🔗 Paste Link Here (Amazon, Flipkart, etc.):")
        
        with st.expander("🛠️ Manual Override (If Link Fails)"):
            st.info("E-commerce sites sometimes block automated fetching. Type it manually here if the link fails.")
            manual_name = st.text_input("Product Name:")
            manual_price = st.number_input("Price (₹):", min_value=0.0, step=100.0)
            
        submitted = st.form_submit_button("Fetch & Add to Dashboard", use_container_width=True)
        
        if submitted:
            if product_url or manual_name:
                with st.spinner("Fetching data from website..."):
                    scraped_name, scraped_price = extract_product_details(product_url) if product_url else (None, None)
                    
                    final_name = manual_name if manual_name else (scraped_name if scraped_name else "Scraping Blocked - Please enter manually")
                    final_price = manual_price if manual_price > 0 else (scraped_price if scraped_price else 0.0)
                    
                    domain = urlparse(product_url).netloc.replace("www.", "") if product_url else "Manual Entry"
                    
                    st.session_state.shopping_list.append({
                        "name": final_name,
                        "price": final_price,
                        "url": product_url if product_url else "#",
                        "source": domain
                    })
                    st.rerun()
            else:
                st.error("Please provide a URL or manual details.")

# -----------------------------------------------------------------------------
# 4. MAIN DASHBOARD: UI / UX
# -----------------------------------------------------------------------------
st.title("🛍️ Smart Budget Dashboard")
st.markdown("Track your wishlist and manage your finances seamlessly.")

# Financial Calculations
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining_budget = st.session_state.total_budget - total_spent

# High-End Metric Cards
m1, m2, m3 = st.columns(3)
with m1:
    st.info(f"**Total Budget**\n### ₹ {st.session_state.total_budget:,.2f}")
with m2:
    st.warning(f"**Total Cost**\n### ₹ {total_spent:,.2f}")
with m3:
    if remaining_budget >= 0:
        st.success(f"**Remaining Balance**\n### ₹ {remaining_budget:,.2f}")
    else:
        st.error(f"**Over Budget By**\n### ₹ {abs(remaining_budget):,.2f}")

st.markdown("---")

# -----------------------------------------------------------------------------
# 5. PRODUCT LIST & SUGGESTIONS
# -----------------------------------------------------------------------------
col_items, col_sugg = st.columns([2, 1])

with col_items:
    st.subheader("🛒 Your Shopping List")
    if not st.session_state.shopping_list:
        st.caption("Your list is empty. Add items from the sidebar to begin!")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            # Professional Card UI for each item
            with st.container(border=True):
                r1, r2, r3 = st.columns([4, 2, 1])
                with r1:
                    st.markdown(f"**[{item['name']}]({item['url']})**")
                    st.caption(f"Source: {item['source']}")
                with r2:
                    st.subheader(f"₹ {item['price']:,.2f}")
                with r3:
                    if st.button("🗑️ Remove", key=f"del_{index}"):
                        st.session_state.shopping_list.pop(index)
                        st.rerun()

with col_sugg:
    st.subheader("💡 Smart AI Insights")
    with st.container(border=True):
        if not st.session_state.shopping_list:
            st.write("Awaiting data to provide insights...")
        elif remaining_budget < 0:
            excess = abs(remaining_budget)
            st.error(f"**Action Required!** You are ₹{excess:,.2f} over budget.")
            expensive_item = max(st.session_state.shopping_list, key=lambda x: x["price"])
            st.markdown(f"📉 **Quick Fix:** Removing your most expensive item (**{expensive_item['name']}** at ₹{expensive_item['price']:,.2f}) will bring you back into the green.")
        else:
            st.success("✅ **Budget is Healthy!** You are well within your limits.")
            st.markdown(f"You still have **₹{remaining_budget:,.2f}** available to allocate.")
