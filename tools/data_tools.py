import yfinance as yf
import streamlit as st
import plotly.graph_objects as go
from typing import Optional
import requests

# Crypto ticker normalization
CRYPTO_SYMBOLS = {
    'BTC': 'BTC-USD', 'BITCOIN': 'BTC-USD',
    'ETH': 'ETH-USD', 'ETHEREUM': 'ETH-USD',
    'SOL': 'SOL-USD', 'SOLANA': 'SOL-USD',
    'ADA': 'ADA-USD', 'CARDANO': 'ADA-USD',
    'DOGE': 'DOGE-USD', 'DOGECOIN': 'DOGE-USD',
    'XRP': 'XRP-USD', 'RIPPLE': 'XRP-USD',
    'DOT': 'DOT-USD', 'POLKADOT': 'DOT-USD',
    'MATIC': 'MATIC-USD', 'POLYGON': 'MATIC-USD',
    'AVAX': 'AVAX-USD', 'AVALANCHE': 'AVAX-USD',
    'LINK': 'LINK-USD', 'CHAINLINK': 'LINK-USD'
}

def normalize_ticker(symbol: str) -> tuple[str, bool]:
    """
    Normalize ticker symbol and detect if it's crypto.
    Returns: (normalized_symbol, is_crypto)
    """
    symbol_upper = symbol.upper().strip()
    
    # Check if already in crypto format (ends with -USD)
    if symbol_upper.endswith('-USD'):
        return symbol_upper, True
    
    # Check if it's a known crypto name
    if symbol_upper in CRYPTO_SYMBOLS:
        return CRYPTO_SYMBOLS[symbol_upper], True
    
    # Otherwise, assume it's a stock
    return symbol_upper, False

def get_crypto_sentiment() -> dict:
    """
    Fetches the Crypto Fear & Greed Index from alternative.me API.
    Returns sentiment score (0-100) and label.
    """
    try:
        response = requests.get('https://api.alternative.me/fng/', timeout=5)
        data = response.json()
        score = int(data['data'][0]['value'])
        label = data['data'][0]['value_classification']
        return {
            'score': score,
            'label': label,
            'interpretation': get_sentiment_interpretation(score)
        }
    except Exception:
        return {
            'score': 50,
            'label': 'Neutral',
            'interpretation': 'Unable to fetch sentiment data'
        }

def get_sentiment_interpretation(score: int) -> str:
    """Interpret sentiment score for risk management."""
    if score >= 75:
        return "⚠️ **HIGH RISK** - Extreme Greed detected. Market may be overheated."
    elif score >= 55:
        return "⚡ **MODERATE RISK** - Greed present. Exercise caution."
    elif score >= 45:
        return "✅ **BALANCED** - Neutral sentiment. Normal market conditions."
    elif score >= 25:
        return "📉 **FEAR ZONE** - Market fear detected. Potential buying opportunity."
    else:
        return "💎 **EXTREME FEAR** - Capitulation zone. Strong buying opportunity for risk-tolerant investors."

def get_market_data(symbol: str) -> str:
    """
    Fetches market data for stocks OR crypto (context-aware).
    For stocks: Returns P/E, Market Cap, etc.
    For crypto: Returns Sentiment, Volatility, ATH distance.
    """
    normalized_symbol, is_crypto = normalize_ticker(symbol)
    
    try:
        stock = yf.Ticker(normalized_symbol)
        info = stock.info
        
        # Validate we got real data
        if not info or ('regularMarketPrice' not in info and 'currentPrice' not in info):
            raise ValueError("No valid data returned from API")

        price = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))
        currency = info.get('currency', 'USD')
        name = info.get('longName', info.get('shortName', normalized_symbol))
        
        # Format price
        if isinstance(price, (int, float)):
            price_formatted = f"{price:.2f}"
        else:
            price_formatted = str(price)
        
        if is_crypto:
            # CRYPTO-SPECIFIC ANALYSIS
            market_cap = info.get('marketCap', 'N/A')
            volume_24h = info.get('volume24Hr', info.get('volume', 'N/A'))
            
            # Get volatility (52w range)
            low_52w = info.get('fiftyTwoWeekLow', price)
            high_52w = info.get('fiftyTwoWeekHigh', price)
            
            # Calculate distance from ATH
            if isinstance(price, (int, float)) and isinstance(high_52w, (int, float)) and high_52w > 0:
                ath_distance = ((high_52w - price) / high_52w) * 100
                ath_str = f"{ath_distance:.1f}% below ATH"
            else:
                ath_str = "N/A"
            
            # Calculate volatility percentage
            if isinstance(low_52w, (int, float)) and isinstance(high_52w, (int, float)) and low_52w > 0:
                volatility = ((high_52w - low_52w) / low_52w) * 100
                volatility_str = f"{volatility:.1f}%"
            else:
                volatility_str = "N/A"
            
            # Get crypto sentiment
            sentiment = get_crypto_sentiment()
            
            # Format volume for readability
            if isinstance(volume_24h, (int, float)):
                if volume_24h >= 1e9:
                    volume_24h_str = f"${volume_24h/1e9:.2f}B"
                elif volume_24h >= 1e6:
                    volume_24h_str = f"${volume_24h/1e6:.2f}M"
                else:
                    volume_24h_str = f"${volume_24h:,.0f}"
            else:
                volume_24h_str = str(volume_24h)
            
            return f"""**📊 Crypto Asset Profile: {name} ({normalized_symbol})**

| Metric | Value |
|--------|-------|
| Current Price | ${price_formatted} {currency} |
| 24h Volume | {volume_24h_str} |
| ATH Distance | {ath_str} |
| 52-Week Volatility | {volatility_str} |
| Fear & Greed Index | {sentiment['score']}/100 ({sentiment['label']}) |

**Market Sentiment:** {sentiment['interpretation']}

⚠️ **Risk Note:** Cryptocurrencies are highly volatile."""
        
        else:
            # STOCK-SPECIFIC ANALYSIS
            pe_ratio = info.get('trailingPE', 'N/A')
            market_cap = info.get('marketCap', 'N/A')
            high_52w = info.get('fiftyTwoWeekHigh', 'N/A')
            eps = info.get('trailingEps', 'N/A')
            
            # Format numbers
            if isinstance(pe_ratio, (int, float)):
                pe_ratio = f"{pe_ratio:.2f}"
            if isinstance(market_cap, (int, float)):
                market_cap = f"${market_cap:,.0f}"
            if isinstance(high_52w, (int, float)):
                high_52w = f"{high_52w:.2f}"
            
            return f"""**📈 Stock Profile: {name} ({normalized_symbol})**

| Metric | Value |
|--------|-------|
| Current Price | ${price_formatted} {currency} |
| P/E Ratio | {pe_ratio} |
| Market Cap | {market_cap} |
| EPS | {eps} |
| 52-Week High | ${high_52w} {currency} |"""
        
    except Exception as e:
        # FALLBACK / DUMMY MODE
        import random
        dummy_price = round(random.uniform(50, 500), 2)
        
        if is_crypto:
            return f"""Note: Live data unavailable. Showing demo data for {normalized_symbol}...

**Demo Crypto Data for {normalized_symbol}**:
- 💰 Current Price: ${dummy_price} USD
- ⚡ Volatility: High (crypto asset)
- 🧠 Sentiment: Check market conditions before investing

_(This is simulated data. Real implementation would show live data.)_"""
        else:
            dummy_pe = round(random.uniform(15, 45), 2)
            return f"""Note: Live data unavailable. Showing demo data for {symbol}...

**Demo Stock Data for {symbol}**:
- 💰 Current Price: ${dummy_price} USD
- 📊 P/E Ratio: {dummy_pe}

_(This is simulated data. Real implementation would show live data.)_"""

def plot_stock_history(symbol: Optional[str] = None) -> str:
    """
    Plots 1-year historical stock data using Plotly with interactive range selector.
    Stores chart in session_state for display.
    """
    if not symbol:
        return "⚠️ No symbol provided for chart generation."
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y")
        
        if hist.empty:
            return f"⚠️ No historical data available for {symbol}."
        
        # Create beautiful Plotly chart with range selector
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=hist.index, 
            y=hist['Close'], 
            mode='lines', 
            name='Close Price',
            line=dict(color='#10A37F', width=2),
            fill='tozeroy',
            fillcolor='rgba(16, 163, 127, 0.1)'
        ))
        
        fig.update_layout(
            title=f'{symbol.upper()} - Price History',
            xaxis_title='Date',
            yaxis_title='Price (USD)',
            template='plotly_dark',
            hovermode='x unified',
            margin=dict(l=20, r=20, t=60, b=20),
            height=400,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(family='Inter, sans-serif', color='#ECECEC'),
            xaxis=dict(
                gridcolor='#3F3F3F',
                rangeselector=dict(
                    buttons=list([
                        dict(count=1, label="1m", step="month", stepmode="backward"),
                        dict(count=6, label="6m", step="month", stepmode="backward"),
                        dict(count=1, label="YTD", step="year", stepmode="todate"),
                        dict(count=1, label="1y", step="year", stepmode="backward"),
                        dict(step="all", label="All")
                    ]),
                    bgcolor='rgba(16, 163, 127, 0.2)',
                    activecolor='#10A37F',
                    font=dict(color='#ECECEC')
                ),
                rangeslider=dict(visible=True, bgcolor='rgba(16, 163, 127, 0.1)'),
                type="date"
            ),
            yaxis=dict(gridcolor='#3F3F3F')
        )
        
        # Store in session state - will be attached to current message
        if 'pending_charts' not in st.session_state:
            st.session_state['pending_charts'] = []
        st.session_state['pending_charts'].append(fig)
        
        return f"📊 Interactive chart created successfully for {symbol.upper()}. Use the range selector buttons (1m, 6m, YTD, 1y, All) to adjust the time period."
        
    except Exception as e:
        return f"❌ Error creating chart for {symbol}: {str(e)}"

def get_fundamental_metrics(symbol: str) -> str:
    """
    Fetches key fundamental metrics (Market Cap, EPS, ROE, Dividend Yield) using yfinance.
    """
    normalized_symbol, is_crypto = normalize_ticker(symbol)
    
    if is_crypto:
        return f"ℹ️ Fundamental metrics (EPS, ROE, Dividends) are not applicable to cryptocurrencies like {normalized_symbol}. Please refer to the Market Data and Sentiment analysis."

    try:
        stock = yf.Ticker(normalized_symbol)
        info = stock.info
        
        market_cap = info.get('marketCap', 'N/A')
        eps = info.get('trailingEps', 'N/A')
        roe = info.get('returnOnEquity', 'N/A')
        div_yield = info.get('dividendYield', 'N/A')
        
        # Format
        if isinstance(market_cap, (int, float)):
            market_cap = f"${market_cap:,.0f}"
        if isinstance(div_yield, (int, float)):
            div_yield = f"{div_yield*100:.2f}%"
            
        return f"""**🏗️ Fundamental Metrics for {normalized_symbol}**:
- 🏢 Market Cap: {market_cap}
- 💵 EPS (Trailing): {eps}
- 📉 ROE: {roe}
- 🎁 Dividend Yield: {div_yield}"""
    except Exception as e:
        return f"❌ Error fetching fundamentals for {symbol}: {e}"

def get_analyst_recommendations(symbol: str) -> str:
    """
    Fetches analyst recommendations using yfinance.
    """
    try:
        stock = yf.Ticker(symbol)
        recs = stock.recommendations
        
        if recs is None or recs.empty:
            return f"⚠️ No analyst recommendations available for {symbol}."
            
        # Get latest recommendations (last 5 rows)
        latest = recs.tail(5)
        summary = f"**📢 Recent Analyst Recommendations for {symbol.upper()}**:\n"
        
        # Note: yfinance recommendations format varies, trying to be robust
        # Often has columns like 'period', 'strongBuy', 'buy', 'hold', 'sell', 'strongSell'
        # Or just a dataframe of individual ratings.
        
        # Simple summary if it's the aggregated format
        if 'strongBuy' in latest.columns:
            latest_row = latest.iloc[-1]
            summary += f"- Strong Buy: {latest_row.get('strongBuy', 0)}\n"
            summary += f"- Buy: {latest_row.get('buy', 0)}\n"
            summary += f"- Hold: {latest_row.get('hold', 0)}\n"
            summary += f"- Sell: {latest_row.get('sell', 0)}\n"
            summary += f"- Strong Sell: {latest_row.get('strongSell', 0)}\n"
        else:
            # Just show raw tail if format is different
            summary += "Detailed breakdown not available in standard format."
            
        target_mean = stock.info.get('targetMeanPrice', 'N/A')
        summary += f"\n🎯 **Average Price Target**: ${target_mean}"
        
        return summary
    except Exception as e:
        return f"❌ Error fetching recommendations for {symbol}: {e}"

def compare_stocks(symbol1: str, symbol2: str) -> str:
    """
    Compares two stocks side-by-side with key metrics.
    """
    try:
        data1 = get_market_data(symbol1)
        data2 = get_market_data(symbol2)
        
        # Extract metrics from the formatted strings (simple parsing)
        def extract_metrics(text):
            lines = text.split('\n')
            price = pe = high = 'N/A'
            for line in lines:
                if 'Current Price' in line:
                    price = line.split(':')[1].strip()
                elif 'P/E Ratio' in line:
                    pe = line.split(':')[1].strip()
                elif '52-Week High' in line:
                    high = line.split(':')[1].strip()
            return price, pe, high
        
        p1, pe1, h1 = extract_metrics(data1)
        p2, pe2, h2 = extract_metrics(data2)
        
        table = f"""
| Metric          | {symbol1.upper()} | {symbol2.upper()} |
|-----------------|-------------------|-------------------|
| Current Price  | {p1}             | {p2}             |
| P/E Ratio      | {pe1}            | {pe2}            |
| 52-Week High   | {h1}             | {h2}             |
"""
        
        return f"**📊 Side-by-Side Comparison: {symbol1.upper()} vs {symbol2.upper()}**\n\n{table}\n\n_Charts available for both stocks via plot_stock_history._"
    except Exception as e:
        return f"❌ Error comparing {symbol1} and {symbol2}: {str(e)}"

def get_watchlist_summary() -> str:
    """
    Gets a summary of all stocks in the watchlist with current prices.
    Use this when user asks about 'my watchlist' or 'my portfolio'.
    """
    try:
        from utils.db import get_watchlist
        watchlist = get_watchlist()
        
        if not watchlist:
            return "📋 Your watchlist is empty. Analyze some stocks to get started!"
        
        summary = "**📋 Watchlist Summary**\n\n"
        summary += "| Ticker | Name | Price | Change | Recommendation |\n"
        summary += "|--------|------|-------|--------|----------------|\n"
        
        for ticker, _ in watchlist:
            try:
                stock = yf.Ticker(ticker)
                info = stock.info
                name = info.get('shortName', ticker)[:15]
                price = info.get('currentPrice', info.get('regularMarketPrice', 0))
                prev_close = info.get('previousClose', price)
                
                if price and prev_close:
                    change = ((price - prev_close) / prev_close) * 100
                    change_str = f"{change:+.2f}%"
                else:
                    change_str = "N/A"
                
                # Simple recommendation
                low_52w = info.get('fiftyTwoWeekLow', price)
                high_52w = info.get('fiftyTwoWeekHigh', price)
                if price and low_52w and high_52w and high_52w != low_52w:
                    position = (price - low_52w) / (high_52w - low_52w)
                    rec = "HOLD" if 0.3 < position < 0.7 else ("SELL" if position > 0.7 else "BUY")
                else:
                    rec = "HOLD"
                
                summary += f"| {ticker} | {name} | ${price:.2f} | {change_str} | {rec} |\n"
            except Exception:
                summary += f"| {ticker} | {ticker} | Error | N/A | HOLD |\n"
        
        return summary
    except Exception as e:
        return f"❌ Error getting watchlist summary: {str(e)}"
