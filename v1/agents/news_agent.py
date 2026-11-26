from phi.agent import Agent
from tools.search_tools import get_company_news, get_watchlist_news

def get_news_agent(model_config):
    return Agent(
        name="News Researcher",
        role="Market News Analyst",
        model=model_config,
        tools=[get_company_news, get_watchlist_news],
        instructions=[
            "You are a market news researcher.",
            "Find and summarize recent financial news.",
            "For single stock: use get_company_news(symbol='<TICKER>').",
            "For watchlist/portfolio news: use get_watchlist_news().",
            "Ignore irrelevant content like driver downloads or support pages.",
            "Focus on market-moving news, earnings, and analyst opinions.",
            "CRITICAL: Summarize news into 3-5 key bullet points. Do not return full articles.",
            "Start your response with '📰 [News Researcher]' to show you're working."
        ],
        show_tool_calls=True,
        markdown=True,
    )
