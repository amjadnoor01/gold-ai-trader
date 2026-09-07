"""
google_native_engine.py — Google Native Quantitative Trading Engine for XAUUSD.
Uses Google Gemini API with ultra-low token consumption (compressed vector encoding).
Integrated with amjadnoor01@gmail.com Google AI Studio key.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# User's Google Gemini API Key from environment
DEFAULT_GEMINI_KEY = os.getenv(
    "GEMINI_API_KEY",
    os.getenv("GOOGLE_API_KEY", "AIzaSyBfhwwLeqDcF-GxnbE1i0LvChpIwDphac4")
)

# Active Gemini models in order of preference
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-flash-latest",
]


class GoogleNativeTradingEngine:
    """
    Ultra-token-efficient Google Gemini Trading Agent.
    Encodes technical, macro, and order-flow signals into a compressed string (<35 tokens),
    and requests a strictly-typed JSON decision from Gemini Flash (<35 tokens).
    Total round-trip is ~70-90 tokens per evaluation.
    Caches responses for `cache_ttl_sec` to avoid redundant calls and preserve free quotas.
    """

    def __init__(self, api_key: Optional[str] = None, cache_ttl_sec: int = 40):
        self.api_key = api_key or DEFAULT_GEMINI_KEY
        self.cache_ttl_sec = cache_ttl_sec
        self.last_eval_time: float = 0.0
        self.cached_decision: Dict[str, Any] = {
            "action": "HOLD",
            "signal": 0.0,
            "confidence": 0.5,
            "regime": "NEUTRAL",
            "rationale": "Google Gemini initializing...",
            "tokens_used": 0,
            "latency_ms": 0,
            "timestamp": time.time(),
        }
        self._lock = threading.Lock()
        self._is_evaluating = False
        self.total_tokens_consumed = 0
        self.call_count = 0

    def evaluate_market_async(self, features: Dict[str, Any]):
        """Trigger evaluation in a background worker thread to keep the trading loop zero-latency."""
        now = time.time()
        with self._lock:
            if self._is_evaluating or (now - self.last_eval_time < self.cache_ttl_sec):
                return
            self._is_evaluating = True

        threading.Thread(target=self._run_evaluation, args=(features,), daemon=True).start()

    def _run_evaluation(self, features: Dict[str, Any]):
        start_ts = time.time()
        try:
            # 1. Compress features into minimal token string (~30 tokens)
            price = features.get("price", 0.0)
            rsi = features.get("rsi", 50.0)
            adx = features.get("adx", 20.0)
            ema_status = features.get("ema_status", "FLAT")
            roc = features.get("roc", 0.0)
            zscore = features.get("zscore", 0.0)
            regime_lead = features.get("regime_lead", "QUANT")

            compressed_prompt = (
                f"XAUUSD|P:{price:.2f}|RSI:{rsi:.1f}|ADX:{adx:.1f}|EMA:{ema_status}|"
                f"ROC:{roc:.2f}%|Z:{zscore:.2f}|L:{regime_lead}\n"
                f"Act as Quantitative Hedge Fund Analyst. Decide gold position with factual macro grounding.\n"
                f"JSON only: {{\"action\":\"BUY\"|\"SELL\"|\"HOLD\",\"confidence\":0.5-0.95,"
                f"\"regime\":\"TREND\"|\"REVERSAL\"|\"BREAKOUT\"|\"CHOP\","
                f"\"rationale\":\"15 words max\","
                f"\"factual_driver\":\"DXY/US10Y/GoldReserve factual link\","
                f"\"sl_mult\":1.0,\"tp_mult\":1.5}}"
            )

            # 2. Call Gemini Flash with minimal payload
            payload = {
                "contents": [{"parts": [{"text": compressed_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.2,
                    "maxOutputTokens": 120,
                },
            }

            resp = None
            used_model = ""
            for model_name in GEMINI_MODELS:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={self.api_key}"
                try:
                    r = requests.post(url, json=payload, timeout=8)
                    if r.status_code == 200:
                        resp = r.json()
                        used_model = model_name
                        break
                    elif r.status_code == 429:
                        logger.warning(f"[GoogleAI] {model_name} rate limit 429, trying fallback")
                        continue
                except Exception as e:
                    logger.debug(f"[GoogleAI] Request to {model_name} failed: {e}")

            latency = int((time.time() - start_ts) * 1000)

            if resp and "candidates" in resp and resp["candidates"]:
                candidate = resp["candidates"][0]
                text_content = candidate.get("content", {}).get("parts", [{}])[0].get("text", "{}")
                parsed = json.loads(text_content)

                action = parsed.get("action", "HOLD").upper()
                conf = float(parsed.get("confidence", 0.6))
                regime = parsed.get("regime", "QUANT")
                rationale = parsed.get("rationale", f"Google AI {action} verdict")
                factual_driver = parsed.get("factual_driver", "Macro DXY/Yield Correlation")

                signal = 1.0 if action == "BUY" else -1.0 if action == "SELL" else 0.0
                usage = resp.get("usageMetadata", {})
                tokens = usage.get("totalTokenCount", 85)

                with self._lock:
                    self.total_tokens_consumed += tokens
                    self.call_count += 1
                    self.last_eval_time = time.time()
                    self.cached_decision = {
                        "action": action,
                        "signal": signal,
                        "confidence": conf,
                        "regime": f"GOOGLE_{regime}",
                        "rationale": f"[Gemini 3.6] {rationale}",
                        "factual_driver": factual_driver,
                        "sl_mult": float(parsed.get("sl_mult", 1.0)),
                        "tp_mult": float(parsed.get("tp_mult", 1.5)),
                        "tokens_used": tokens,
                        "total_tokens": self.total_tokens_consumed,
                        "model": used_model,
                        "latency_ms": latency,
                        "timestamp": time.time(),
                    }
                logger.info(f"[GoogleAI] Decision: {action} ({conf*100:.0f}%) | {rationale} | Tokens={tokens} | Latency={latency}ms")
            else:
                logger.warning(f"[GoogleAI] Gemini API returned no valid candidates. Status: {resp}")

        except Exception as err:
            logger.warning(f"[GoogleAI] Evaluation error: {err}")
        finally:
            with self._lock:
                self.last_eval_time = time.time()
                self._is_evaluating = False

    def get_latest_signal(self, features: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Returns the current cached decision and triggers background refresh if expired."""
        if features:
            self.evaluate_market_async(features)
        with self._lock:
            return dict(self.cached_decision)
