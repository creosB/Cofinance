from ddgs import DDGS  # Require modern package; install with `pip install ddgs`

def get_company_news(symbol: str) -> str:
    """
    Searches for the top 5 recent financial news articles using DuckDuckGo.
    """
    try:
        ddgs = DDGS()
        query = f"{symbol} stock market financial news"
        results = list(ddgs.text(query, max_results=5))
        
        if not results:
            return f"📰 No recent news found for {symbol}."
            
        news_summary = f"**📰 Recent Financial News for {symbol.upper()}:**\n\n"
        for i, r in enumerate(results, 1):
            title = r.get('title', 'No Title')
            href = r.get('href', '#')
            body = r.get('body', 'No summary available.')
            news_summary += f"{i}. **[{title}]({href})**\n   {body[:150]}...\n\n"
            
        return news_summary
    except Exception as e:
        return f"❌ Error fetching news for {symbol}: {str(e)}"

def get_watchlist_news() -> str:
    """
    Fetches news for all stocks in the user's watchlist.
    Use when user asks for 'news on my watchlist' or 'portfolio news'.
    """
    try:
        from utils.db import get_watchlist
        watchlist = get_watchlist()
        
        if not watchlist:
            return "📋 Your watchlist is empty. Add some stocks to track their news!"
        
        news_summary = "**📰 News Summary for Your Watchlist**\n\n"
        
        for ticker, _ in watchlist[:5]:  # Limit to first 5 to avoid too long response
            ticker_news = get_company_news(ticker)
            news_summary += f"### {ticker}\n{ticker_news}\n\n"
        
        if len(watchlist) > 5:
            news_summary += f"\n_Showing news for first 5 stocks. You have {len(watchlist)} total._"
        
        return news_summary
    except Exception as e:
        return f"❌ Error fetching watchlist news: {str(e)}"
