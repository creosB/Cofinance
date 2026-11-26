import types
import pytest

from tools import data_tools


class DummyTicker:
    def __init__(self, info):
        self.info = info


def test_get_market_data_stock(monkeypatch):
    # Patch yfinance to return a deterministic info object
    stock_info = {
        'currentPrice': 150.25,
        'currency': 'USD',
        'longName': 'Apple Inc.',
        'trailingPE': 28.23,
        'marketCap': 2500000000000,
        'trailingEps': 5.72,
        'fiftyTwoWeekHigh': 200.00
    }

    monkeypatch.setattr(data_tools, 'yf', types.SimpleNamespace(Ticker=lambda s: DummyTicker(stock_info)))

    res = data_tools.get_market_data('AAPL')
    assert 'Apple Inc.' in res
    assert 'P/E Ratio' in res or 'P/E' in res


def test_get_market_data_crypto(monkeypatch):
    # Patch yfinance and the sentiment API for crypto
    crypto_info = {
        'currentPrice': 42000,
        'currency': 'USD',
        'shortName': 'Bitcoin',
        'marketCap': 800000000000,
        'volume': 1000000000,
        'fiftyTwoWeekLow': 16000,
        'fiftyTwoWeekHigh': 69000
    }

    monkeypatch.setattr(data_tools, 'yf', types.SimpleNamespace(Ticker=lambda s: DummyTicker(crypto_info)))

    # Patch the API call for FNG
    monkeypatch.setattr(data_tools, 'get_crypto_sentiment', lambda: {'score': 40, 'label': 'Neutral', 'interpretation': 'Balanced'})

    res = data_tools.get_market_data('BTC')
    assert 'Crypto' in res
    assert 'Fear & Greed' in res or 'Fear & Greed' in res


def test_get_market_data_fallback(monkeypatch):
    # Simulate yfinance failing by raising in the constructor
    class BadTicker:
        def __init__(self, sym):
            raise RuntimeError('Network unreachable')

    monkeypatch.setattr(data_tools, 'yf', types.SimpleNamespace(Ticker=lambda s: BadTicker(s)))

    res = data_tools.get_market_data('AAPL')
    assert 'Demo' in res or 'Live data unavailable' in res