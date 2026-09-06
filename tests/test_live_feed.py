"""
tests/test_live_feed.py — Unit tests for live_feed module (offline/mock)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock


class TestFetchOHLCV:
    @patch("live_feed.yf.download")
    def test_returns_dataframe(self, mock_dl):
        from live_feed import fetch_ohlcv
        # yfinance returns flat columns after auto_adjust=True with a single ticker
        idx = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        fake = pd.DataFrame({
            "Open":   [2000.0]*5,
            "High":   [2010.0]*5,
            "Low":    [1995.0]*5,
            "Close":  [2005.0]*5,
            "Volume": [100.0]*5,
        }, index=idx)
        fake.index.name = "Datetime"
        mock_dl.return_value = fake
        df = fetch_ohlcv("GC=F", period="5d", interval="1h")
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns
        assert len(df) == 5

    @patch("live_feed.yf.download")
    def test_empty_returns_raises(self, mock_dl):
        from live_feed import fetch_ohlcv
        mock_dl.return_value = pd.DataFrame()
        with pytest.raises(RuntimeError):
            fetch_ohlcv("FAKE", period="5d", interval="1h")


class TestLiveGoldFeed:
    def test_register_callback(self):
        from live_feed import LiveGoldFeed
        feed = LiveGoldFeed(poll_interval=5)
        cb = lambda tick: None
        feed.register_callback(cb)
        assert cb in feed._callbacks

    @patch("live_feed.yf.Ticker")
    def test_get_current_price(self, mock_ticker):
        from live_feed import LiveGoldFeed
        mock_info = MagicMock()
        mock_info.last_price = 2345.67
        mock_ticker.return_value.fast_info = mock_info
        feed = LiveGoldFeed()
        price = feed.get_current_price()
        assert price == pytest.approx(2345.67)

    @patch("live_feed.yf.Ticker")
    def test_fetch_tick_returns_dict(self, mock_ticker):
        from live_feed import LiveGoldFeed
        mock_info = MagicMock()
        mock_info.last_price = 2450.0
        mock_info.regularMarketPrice = 2450.0
        mock_ticker.return_value.fast_info = mock_info
        feed = LiveGoldFeed()
        tick = feed._fetch_tick()
        assert tick is not None
        assert tick["last"] == pytest.approx(2450.0)
        assert "symbol" in tick
        assert "timestamp" in tick
