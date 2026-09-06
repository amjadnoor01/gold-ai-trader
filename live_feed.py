"""
live_feed.py — Free real-time Gold (XAUUSD/GC=F) data feed via yfinance.
No API key required. Polls every POLL_INTERVAL seconds and notifies callbacks.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Primary and fallback tickers (all free)
GOLD_TICKERS = ["GC=F", "XAUUSD=X", "GLD"]
MACRO_TICKERS = ["^TNX", "DX-Y.NYB", "^VIX", "CL=F", "^GSPC", "BTC-USD"]

_HISTORY_PERIOD  = "5d"
_HISTORY_INTERVAL = "1h"


class LiveGoldFeed:
    """
    Polls yfinance every `poll_interval` seconds for the latest gold price.
    Notifies all registered async callbacks with a tick dict.
    """

    def __init__(self, poll_interval: float = 10.0):
        self.poll_interval  = poll_interval
        self.last_price: float = 0.0
        self.last_tick: dict   = {}
        self._callbacks: List[Callable] = []
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def register_callback(self, cb: Callable):
        self._callbacks.append(cb)

    def get_current_price(self) -> float:
        """Synchronous price fetch — used at startup."""
        for ticker in GOLD_TICKERS:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = getattr(info, "last_price", None) or \
                        getattr(info, "regularMarketPrice", None)
                if price and price > 100:
                    logger.info(f"[Feed] Got price {price:.2f} from {ticker}")
                    return float(price)
            except Exception as e:
                logger.debug(f"[Feed] {ticker} failed: {e}")
        return 0.0

    def _fetch_tick(self) -> Optional[dict]:
        """Fetch latest price from yfinance (tries multiple tickers)."""
        for ticker in GOLD_TICKERS:
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = getattr(info, "last_price", None) or \
                        getattr(info, "regularMarketPrice", None)
                if price and price > 100:
                    tick = {
                        "symbol":    "XAUUSD",
                        "last":      float(price),
                        "timestamp": pd.Timestamp.utcnow().isoformat(),
                        "source":    ticker,
                    }
                    return tick
            except Exception as e:
                logger.debug(f"[Feed] {ticker} tick error: {e}")
        return None

    async def _poll_loop(self):
        logger.info("[Feed] Live gold feed started")
        while self._running:
            tick = self._fetch_tick()
            if tick:
                self.last_price = tick["last"]
                self.last_tick  = tick
                for cb in self._callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(tick)
                        else:
                            cb(tick)
                    except Exception as e:
                        logger.error(f"[Feed] Callback error: {e}")
            else:
                logger.warning("[Feed] Could not fetch price — retrying")
            await asyncio.sleep(self.poll_interval)

    async def start(self):
        self._running = True
        await self._poll_loop()

    def stop(self):
        self._running = False


def fetch_ohlcv(symbol: str = "GC=F", period: str = "6mo",
                interval: str = "1h") -> pd.DataFrame:
    """
    Download historical OHLCV data using yfinance.
    Returns a clean DataFrame with lowercase columns + 'time' index.
    """
    for ticker_sym in [symbol] + GOLD_TICKERS:
        try:
            df = yf.download(ticker_sym, period=period, interval=interval,
                             auto_adjust=True, progress=False)
            if df.empty:
                continue
            df = df.reset_index()
            # Normalise column names
            df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower()
                          for c in df.columns]
            if "datetime" in df.columns:
                df = df.rename(columns={"datetime": "time"})
            elif "date" in df.columns:
                df = df.rename(columns={"date": "time"})
            df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
            df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
            # Keep only what we need
            keep = [c for c in ["time", "open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[keep].dropna(subset=["open", "high", "low", "close"])
            logger.info(f"[Feed] Downloaded {len(df)} bars from {ticker_sym} ({period}/{interval})")
            return df
        except Exception as e:
            logger.debug(f"[Feed] Download failed {ticker_sym}: {e}")
    raise RuntimeError(f"Could not download data for {symbol}")


def fetch_macro() -> pd.DataFrame:
    """Download macro correlation data (DXY, VIX, Oil, SPX, BTC)."""
    frames = {}
    for sym in MACRO_TICKERS:
        try:
            df = yf.download(sym, period="6mo", interval="1d",
                             auto_adjust=True, progress=False)
            if not df.empty:
                df = df[["Close"]].rename(columns={"Close": sym})
                frames[sym] = df
        except Exception as e:
            logger.debug(f"[Feed] Macro {sym} failed: {e}")
    if not frames:
        return pd.DataFrame()
    macro = pd.concat(frames.values(), axis=1)
    macro.index = pd.to_datetime(macro.index, utc=True).tz_localize(None)
    return macro
