import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# -----------------------------------------------------------------------------
# 1. CORE CODE CONFIGURATION & INITIALIZATION
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Smart Budget Shopper Dashboard", layout="wide")

# Initialize session state to remember items across webpage updates
if "shopping_list" not in st.session_state:
    st.session_state.shopping_list = []
if "total_budget" not in st.session_state:
    st.session_state.total_budget = 0.0

# -----------------------------------------------------------------------------
# 2. BACKEND LOGIC: PRICE EXTRACTION ENGINE
# -----------------------------------------------------------------------------
def extract_product_details(url):
    """
    Attempts to scrape the product name and price from a given URL.
    Includes browser headers to prevent basic bot blocks.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }
    
    try:
        domain = urlparse(url).netloc
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return None, None
            
        soup = BeautifulSoup(response.content, "html.parser")
        
        # Generic parsing logic (Can be customized for specific domains)
        title = "Scraped Product"
        if soup.find("h1"):
            title = soup.find("h1").get_text().strip()
        elif soup.find("span", {"id": "productTitle"}): # Amazon fallback
            title = soup.find("span", {"id": "productTitle"}).get_text().strip()
            
        # Common price container elements across different e-commerce scripts
        price_selectors = [
            {"tag": "span", "class": "a-price-whole"},
            {"tag": "span", "class": "price-to-pay"},
            {"tag": "div", "class": "_30jeq3 _16Jk6d"}, # Flipkart fallback
            {"tag": "span", "class": "regular-price"}
        ]
        
        price = None
        for selector in price_selectors:
            element = soup.find(selector["tag"], class_=selector["class"])
            if element:
                raw_price = element.get_text().strip()
                # Extract numeric digits and decimals
                cleaned_price = "".join(c for c in raw_price if c.isdigit() or c == '.')
                if cleaned_price:
                    price = float(cleaned_price)
                    break
                    
        return title[:50] + "..." if len(title) > 50 else title, price
        
    except Exception:
        return None, None

# -----------------------------------------------------------------------------
# 3. FRONTEND USER INTERFACE & METRICS
# -----------------------------------------------------------------------------
st.title("🛍️ Smart Budget Shopper Dashboard")
st.markdown("Plan your purchases, track link expenses, and evaluate visual recommendations.")

# Top Configuration Bar
col_b1, col_b2 = st.columns([1, 2])
with col_b1:
    input_budget = st.number_input("Set Your Total Budget:", min_value=0.0, value=st.session_state.total_budget, step=100.0)
    st.session_state.total_budget = input_budget

# Calculations Setup
total_spent = sum(item["price"] for item in st.session_state.shopping_list)
remaining_budget = st.session_state.total_budget - total_spent

# Main Metrics Dashboard
m_col1, m_col2, m_col3 = st.columns(3)
m_col1.metric("Total Budget", f"${st.session_state.total_budget:,.2f}")
m_col2.metric("Total Cost", f"${total_spent:,.2f}")

if remaining_budget >= 0:
    m_col3.metric("Remaining Balance", f"${remaining_budget:,.2f}")
else:
    m_col3.metric("Over Budget By", f"${abs(remaining_budget):,.2f}", delta_color="inverse")

st.markdown("---")

# Layout Split: Inputs vs List View
left_panel, right_panel = st.columns([1, 1])

with left_panel:
    st.subheader("Add New Product Link")
    product_url = st.text_input("Paste Product URL here:")
    
    # Optional Manual Overrides if sites deploy anti-scraping firewalls
    manual_name = st.text_input("Custom Name (Optional / Override):")
    manual_price = st.number_input("Custom Price (Optional / Override):", min_value=0.0, step=10.0)
    
    if st.button("Process & Add to Dashboard"):
        if product_url:
            with st.spinner("Analyzing web link data..."):
                scraped_name, scraped_price = extract_product_details(product_url)
                
                # Assign values prioritising manual fields over scraper outputs
                final_name = manual_name if manual_name else (scraped_name if scraped_name else "Manual Link Entry")
                final_price = manual_price if manual_price > 0 else (scraped_price if scraped_price else 0.0)
                
                # Fetch base domain name for visual layout
                parsed_uri = urlparse(product_url)
                source_domain = parsed_uri.netloc.replace("www.", "")
                
                st.session_state.shopping_list.append({
                    "name": final_name,
                    "price": final_price,
                    "url": product_url,
                    "source": source_domain
                })
                st.rerun()
        else:
            st.error("Please insert a valid web address first.")

with right_panel:
    st.subheader("Your Purchase Breakdown")
    if not st.session_state.shopping_list:
        st.info("No items added yet. Paste a product link on the left to start calculations.")
    else:
        for index, item in enumerate(st.session_state.shopping_list):
            item_row = st.container()
            r_col1, r_col2, r_col3 = item_row.columns([3, 1, 1])
            r_col1.markdown(f"**[{item['name']}]({item['url']})** \n*Source: {item['source']}*")
            r_col2.markdown(f"**${item['price']:,.2f}**")
            if r_col3.button("Remove", key=f"del_{index}"):
                st.session_state.shopping_list.pop(index)
                st.rerun()

# -----------------------------------------------------------------------------
# 4. BACKEND SUGGESTIONS ENGINE
# -----------------------------------------------------------------------------
st.markdown("---")
st.subheader("💡 Automated Optimization Suggestions")

if remaining_budget < 0:
    excess = abs(remaining_budget)
    st.warning(f"Your selection path exceeds your set ceiling limit by **${excess:,.2f}**.")
    
    # Suggestion 1: Find the single most expensive item
    expensive_item = max(st.session_state.shopping_list, key=lambda x: x["price"])
    st.markdown(f"• **Budget Saver Action:** Removing your highest expense item (**{expensive_item['name']}** - ${expensive_item['price']:,.2f}) will immediately clean up your deficit.")
    
    # Suggestion 2: Find a single item that resolves the exact deficit if removed
    viable_drops = [i for i in st.session_state.shopping_list if i["price"] >= excess]
    if viable_drops:
        closest_drop = min(viable_drops, key=lambda x: x["price"])
        st.markdown(f"• **Precision Alternative:** Swapping out or eliminating just **{closest_drop['name']}** (${closest_drop['price']:,.2f}) saves enough to bring the entire pipeline back into standard parameters.")
else:
    if st.session_state.shopping_list:
        st.success("Excellent! Your current shopping layout is healthy and falls squarely within financial constraints.")
