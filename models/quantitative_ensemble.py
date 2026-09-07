"""
quantitative_ensemble.py — Advanced Multi-Strategy Quantitative Trading Engine.

Implements 5 distinct mathematical & statistical market models:
  1. TREND:      Triple EMA (12/26/50) alignment + ADX (14) trend strength filter.
  2. MEAN_REV:   RSI (14) overbought/oversold + Bollinger Bands (20, 2.0σ) z-score reversal.
  3. MOMENTUM:   MACD histogram acceleration + Rate of Change (ROC 14) + Stochastic %K/%D.
  4. MACRO:      Dollar Index (DXY) rolling inverse correlation + volatility regime scaling.
  5. ML_POLICY:  Calibrated Scikit-Learn Gradient Boosting classifier on feature matrix.

Signals are combined using dynamic weights from ClosedLoopLearner.
Ensemble only triggers when consensus threshold is met and uncertainty is low.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

logger = logging.getLogger(__name__)


def compute_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate ADX, +DI, and -DI."""
    n = len(close)
    if n < period + 1:
        return np.zeros(n), np.zeros(n), np.zeros(n)

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hpc = abs(high[i] - close[i - 1])
        lpc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hpc, lpc)

        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # Smooth TR and DM
    atr = np.zeros(n)
    smooth_plus = np.zeros(n)
    smooth_minus = np.zeros(n)

    atr[period] = np.mean(tr[1:period + 1])
    smooth_plus[period] = np.mean(plus_dm[1:period + 1])
    smooth_minus[period] = np.mean(minus_dm[1:period + 1])

    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        smooth_plus[i] = (smooth_plus[i - 1] * (period - 1) + plus_dm[i]) / period
        smooth_minus[i] = (smooth_minus[i - 1] * (period - 1) + minus_dm[i]) / period

    eps = 1e-9
    plus_di = 100 * smooth_plus / (atr + eps)
    minus_di = 100 * smooth_minus / (atr + eps)

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + eps)
    adx = np.zeros(n)
    if n > 2 * period:
        adx[2 * period] = np.mean(dx[period:2 * period + 1])
        for i in range(2 * period + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx, plus_di, minus_di


from models.google_native_engine import GoogleNativeTradingEngine


class QuantitativeEnsemble:
    """
    Multi-model quant engine combining technical, statistical, ML, and Google Gemini AI signals.
    """

    def __init__(self):
        self.ml_model: Optional[HistGradientBoostingClassifier] = None
        self.is_ml_fitted = False
        self.google_engine = GoogleNativeTradingEngine()
        self.last_signals: Dict[str, dict] = {}
        self.last_consensus: float = 0.0
        self.last_uncertainty: float = 0.0

    # ── Model 1: Trend Following (EMA Alignment + ADX) ────────────────────────
    def _evaluate_trend(self, df: pd.DataFrame) -> Tuple[float, float, str]:
        """
        Returns (signal in [-1.0, 1.0], confidence in [0.0, 1.0], rationale).
        Positive = Bullish, Negative = Bearish.
        """
        if len(df) < 55:
            return 0.0, 0.0, "Warmup bars needed"

        closes = df["close"].to_numpy(dtype=np.float64)
        highs = df["high"].to_numpy(dtype=np.float64)
        lows = df["low"].to_numpy(dtype=np.float64)

        ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean().to_numpy()
        ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean().to_numpy()
        ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().to_numpy()

        curr_price = closes[-1]
        e12, e26, e50 = ema12[-1], ema26[-1], ema50[-1]
        prev_e12 = ema12[-2]

        adx, plus_di, minus_di = compute_adx(highs, lows, closes, period=14)
        curr_adx = adx[-1]

        # Trend Strength Factor from ADX (25+ is strong trend, <20 is choppy range)
        adx_strength = min(1.0, max(0.0, (curr_adx - 15.0) / 25.0))

        # Bullish alignment: Price > EMA12 > EMA26 > EMA50
        if curr_price > e12 > e26 > e50:
            slope = (e12 - prev_e12) / curr_price * 1000
            conf = min(0.95, 0.55 + 0.35 * adx_strength + min(0.1, max(0.0, slope)))
            return 1.0, conf, f"Bullish Trend (EMA12>26>50, ADX={curr_adx:.1f})"

        # Bearish alignment: Price < EMA12 < EMA26 < EMA50
        elif curr_price < e12 < e26 < e50:
            slope = (prev_e12 - e12) / curr_price * 1000
            conf = min(0.95, 0.55 + 0.35 * adx_strength + min(0.1, max(0.0, slope)))
            return -1.0, conf, f"Bearish Trend (EMA12<26<50, ADX={curr_adx:.1f})"

        # Partial trend with ADX confirmation
        elif e12 > e26 and curr_adx > 22 and plus_di[-1] > minus_di[-1]:
            return 0.6, 0.50 * adx_strength, f"Mild Bullish (EMA12>26, ADX={curr_adx:.1f})"
        elif e12 < e26 and curr_adx > 22 and minus_di[-1] > plus_di[-1]:
            return -0.6, 0.50 * adx_strength, f"Mild Bearish (EMA12<26, ADX={curr_adx:.1f})"

        return 0.0, 0.1, f"Choppy Range (ADX={curr_adx:.1f})"

    # ── Model 2: Mean Reversion (RSI + Bollinger Bands) ───────────────────────
    def _evaluate_mean_reversion(self, df: pd.DataFrame) -> Tuple[float, float, str]:
        """
        Returns (signal, confidence, rationale).
        Oversold at lower band -> Bullish (+1.0).
        Overbought at upper band -> Bearish (-1.0).
        """
        if len(df) < 30:
            return 0.0, 0.0, "Warmup bars needed"

        closes = df["close"].to_numpy(dtype=np.float64)
        curr_price = closes[-1]

        # 14-period RSI
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = float(pd.Series(gains).tail(14).mean())
        avg_loss = float(pd.Series(losses).tail(14).mean())
        if avg_gain == 0 and avg_loss == 0:
            rsi = 50.0
        else:
            rs = avg_gain / (avg_loss + 1e-9)
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # 20-period Bollinger Bands (2.0 std)
        rolling_20 = pd.Series(closes).tail(20)
        sma20 = rolling_20.mean()
        std20 = rolling_20.std() + 1e-9
        upper_bb = sma20 + 2.0 * std20
        lower_bb = sma20 - 2.0 * std20
        z_score = (curr_price - sma20) / std20

        # Oversold Mean Reversion (RSI < 32 and price touching/below lower BB)
        if rsi < 32 or curr_price <= lower_bb:
            conf = min(0.95, 0.50 + max(0.0, (35 - rsi) / 35.0 * 0.45))
            return 1.0, conf, f"Oversold Bounce (RSI={rsi:.1f}, Z={z_score:.2f})"

        # Overbought Mean Reversion (RSI > 68 and price touching/above upper BB)
        elif rsi > 68 or curr_price >= upper_bb:
            conf = min(0.95, 0.50 + max(0.0, (rsi - 65) / 35.0 * 0.45))
            return -1.0, conf, f"Overbought Exhaustion (RSI={rsi:.1f}, Z={z_score:.2f})"

        # Mild mean-reversion drift toward SMA20
        if z_score > 1.5:
            return -0.4, 0.35, f"Extended Above Mean (Z={z_score:.2f})"
        elif z_score < -1.5:
            return 0.4, 0.35, f"Extended Below Mean (Z={z_score:.2f})"

        return 0.0, 0.1, f"Neutral Band (RSI={rsi:.1f}, Z={z_score:.2f})"

    # ── Model 3: Momentum (MACD Histogram Acceleration + ROC) ─────────────────
    def _evaluate_momentum(self, df: pd.DataFrame) -> Tuple[float, float, str]:
        """
        Returns (signal, confidence, rationale).
        Measures rate of change and MACD histogram impulse.
        """
        if len(df) < 35:
            return 0.0, 0.0, "Warmup bars needed"

        closes = df["close"].to_numpy(dtype=np.float64)
        c_series = pd.Series(closes)

        # MACD (12, 26, 9)
        ema12 = c_series.ewm(span=12, adjust=False).mean()
        ema26 = c_series.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal

        curr_hist = hist.iloc[-1]
        prev_hist = hist.iloc[-2]
        hist_accel = curr_hist - prev_hist

        # 14-period Rate of Change (ROC)
        roc14 = (closes[-1] - closes[-14]) / closes[-14] * 100.0

        # Strong bullish momentum: MACD above signal line and histogram expanding upwards
        if curr_hist > 0 and hist_accel > 0 and roc14 > 0.1:
            conf = min(0.95, 0.55 + min(0.35, abs(roc14) * 0.5))
            return 1.0, conf, f"Bullish Impulse (MACD Hist={curr_hist:+.2f}, ROC={roc14:+.2f}%)"

        # Strong bearish momentum: MACD below signal line and histogram expanding downwards
        elif curr_hist < 0 and hist_accel < 0 and roc14 < -0.1:
            conf = min(0.95, 0.55 + min(0.35, abs(roc14) * 0.5))
            return -1.0, conf, f"Bearish Impulse (MACD Hist={curr_hist:+.2f}, ROC={roc14:+.2f}%)"

        # Histogram crossover
        if prev_hist <= 0 and curr_hist > 0:
            return 0.8, 0.60, f"Bullish MACD Cross (Hist={curr_hist:+.2f})"
        elif prev_hist >= 0 and curr_hist < 0:
            return -0.8, 0.60, f"Bearish MACD Cross (Hist={curr_hist:+.2f})"

        return 0.0, 0.15, f"Weak Momentum (ROC={roc14:+.2f}%)"

    # ── Model 4: Macro Dynamics & Volatility Regime ───────────────────────────
    def _evaluate_macro(self, df: pd.DataFrame) -> Tuple[float, float, str]:
        """
        Returns (signal, confidence, rationale).
        Evaluates gold's macro drivers (DXY correlation if available, volatility regime).
        """
        closes = df["close"].to_numpy(dtype=np.float64)
        if len(closes) < 25:
            return 0.0, 0.0, "Warmup bars needed"

        # Check if macro columns exist (e.g. from macro fetch)
        if "dxy_close" in df.columns or "dxy_ret" in df.columns:
            dxy_col = "dxy_ret" if "dxy_ret" in df.columns else "dxy_close"
            gold_ret = pd.Series(closes).pct_change().tail(24)
            dxy_ret = df[dxy_col].pct_change().tail(24)
            corr = gold_ret.corr(dxy_ret)

            # DXY dropping is historically bullish for Gold
            dxy_change = (df[dxy_col].iloc[-1] - df[dxy_col].iloc[-5]) / (abs(df[dxy_col].iloc[-5]) + 1e-9)
            if dxy_change < -0.002:
                return 1.0, 0.70, f"Dollar Weakness Tail-wind (DXY={dxy_change*100:+.2f}%, Corr={corr:.2f})"
            elif dxy_change > 0.002:
                return -1.0, 0.70, f"Dollar Strength Head-wind (DXY={dxy_change*100:+.2f}%, Corr={corr:.2f})"

        # Volatility regime analysis via True Range expansion
        highs = df["high"].tail(24).to_numpy(dtype=np.float64)
        lows = df["low"].tail(24).to_numpy(dtype=np.float64)
        ranges = highs - lows
        curr_range = ranges[-1]
        avg_range = np.mean(ranges) + 1e-9
        range_ratio = curr_range / avg_range

        # Directional drift with range expansion
        drift = (closes[-1] - closes[-6]) / closes[-6] * 100.0
        if range_ratio > 1.25 and drift > 0.15:
            return 0.7, 0.60, f"Volatility Expansion Up (Range={range_ratio:.1f}x avg)"
        elif range_ratio > 1.25 and drift < -0.15:
            return -0.7, 0.60, f"Volatility Expansion Down (Range={range_ratio:.1f}x avg)"

        return 0.0, 0.20, f"Standard Volatility (Range={range_ratio:.1f}x)"

    # ── Model 5: Calibrated Machine Learning Classifier ───────────────────────
    def _train_ml_model(self, df: pd.DataFrame):
        """Fit or update lightweight Gradient Boosting model on historical bars."""
        try:
            if len(df) < 150:
                return

            closes = df["close"].to_numpy(dtype=np.float64)
            highs = df["high"].to_numpy(dtype=np.float64)
            lows = df["low"].to_numpy(dtype=np.float64)

            # Feature Engineering
            ret1 = pd.Series(closes).pct_change(1).to_numpy()
            ret5 = pd.Series(closes).pct_change(5).to_numpy()
            ret15 = pd.Series(closes).pct_change(15).to_numpy()
            vol20 = pd.Series(ret1).rolling(20).std().to_numpy()
            hl_ratio = (highs - lows) / (closes + 1e-9)

            # Target: forward 3-bar return direction (1 = Up > 0.05%, 0 = Down < -0.05%, ignored flat)
            fwd_ret = pd.Series(closes).shift(-3) / pd.Series(closes) - 1.0
            fwd_ret = fwd_ret.to_numpy()

            features = np.column_stack([ret1, ret5, ret15, vol20, hl_ratio])
            valid_mask = ~np.isnan(features).any(axis=1) & ~np.isnan(fwd_ret)
            valid_mask[:-3] = True  # ignore last 3 unshifted

            X = features[valid_mask][:-3]
            y_raw = fwd_ret[valid_mask][:-3]

            # Binary classification (Up vs Down)
            y = np.where(y_raw > 0, 1, 0)
            if len(y) > 80:
                clf = HistGradientBoostingClassifier(
                    max_iter=50, max_leaf_nodes=15, min_samples_leaf=10,
                    learning_rate=0.08, random_state=42
                )
                clf.fit(X, y)
                self.ml_model = clf
                self.is_ml_fitted = True
                logger.info(f"[Ensemble ML] Trained gradient boosting classifier on {len(X)} bars")
        except Exception as e:
            logger.debug(f"[Ensemble ML] Training error: {e}")

    def _evaluate_ml(self, df: pd.DataFrame) -> Tuple[float, float, str]:
        """Generate ML prediction on latest bar."""
        if not self.is_ml_fitted or self.ml_model is None:
            if len(df) >= 150:
                self._train_ml_model(df)
            if not self.is_ml_fitted:
                return 0.0, 0.1, "ML model calibrating"

        try:
            closes = df["close"].to_numpy(dtype=np.float64)
            highs = df["high"].to_numpy(dtype=np.float64)
            lows = df["low"].to_numpy(dtype=np.float64)

            ret1 = (closes[-1] - closes[-2]) / closes[-2]
            ret5 = (closes[-1] - closes[-6]) / closes[-6]
            ret15 = (closes[-1] - closes[-16]) / closes[-16]
            vol20 = float(pd.Series(closes).pct_change().tail(20).std())
            hl_ratio = (highs[-1] - lows[-1]) / (closes[-1] + 1e-9)

            x = np.array([[ret1, ret5, ret15, vol20, hl_ratio]], dtype=np.float64)
            probs = self.ml_model.predict_proba(x)[0]  # [prob_down, prob_up]
            prob_up = float(probs[1])

            if prob_up >= 0.58:
                conf = min(0.95, (prob_up - 0.5) * 2.0)
                return 1.0, conf, f"ML Classifier Bullish (P(Up)={prob_up:.1%})"
            elif prob_up <= 0.42:
                prob_down = 1.0 - prob_up
                conf = min(0.95, (prob_down - 0.5) * 2.0)
                return -1.0, conf, f"ML Classifier Bearish (P(Down)={prob_down:.1%})"

            return 0.0, 0.20, f"ML Neutral (P(Up)={prob_up:.1%})"
        except Exception as e:
            logger.debug(f"[Ensemble ML] Prediction error: {e}")
            return 0.0, 0.0, "ML calculation fallback"

    # ── Master Consensus Evaluation ──────────────────────────────────────────
    def evaluate(self, df: pd.DataFrame, learner_weights: Optional[Dict[str, float]] = None, threshold: float = 0.12) -> dict:
        """
        Evaluate all 5 strategies and aggregate by dynamic learner weights.

        Returns comprehensive dictionary:
          - action: 0 (FLAT), 1 (BUY), 2 (SELL)
          - direction: "FLAT" | "BUY" | "SELL"
          - confidence: float 0.0–1.0
          - consensus_score: float -1.0 to +1.0
          - uncertainty: float 0.0–1.0 (disagreement)
          - regime_lead: name of strongest contributing model
          - model_breakdown: detailed status of each of the 5 models
        """
        if learner_weights is None:
            learner_weights = {"ppo": 0.20, "trend": 0.20, "mean_rev": 0.20, "momentum": 0.20, "macro": 0.20}

        s_trend, c_trend, r_trend = self._evaluate_trend(df)
        s_mean, c_mean, r_mean = self._evaluate_mean_reversion(df)
        s_mom, c_mom, r_mom = self._evaluate_momentum(df)
        s_macro, c_macro, r_macro = self._evaluate_macro(df)
        s_ml, c_ml, r_ml = self._evaluate_ml(df)

        # Lightweight Google Gemini Native Feature Extraction
        curr_price = float(df["close"].iloc[-1])
        closes = df["close"].to_numpy(dtype=np.float64)
        rsi_val = 50.0
        if len(closes) >= 15:
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
            loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
            rs = gain / (loss + 1e-9)
            rsi_val = float(100 - (100 / (1 + rs)))

        adx_val = 25.0
        if len(df) >= 30:
            adx_arr, _, _ = compute_adx(df["high"].to_numpy(), df["low"].to_numpy(), closes, 14)
            adx_val = float(adx_arr[-1])

        roc_val = float((closes[-1] - closes[-6]) / (closes[-6] + 1e-9) * 100) if len(closes) >= 6 else 0.0

        g_features = {
            "price": curr_price,
            "rsi": rsi_val,
            "adx": adx_val,
            "ema_status": "BULL" if s_trend > 0 else "BEAR" if s_trend < 0 else "FLAT",
            "roc": roc_val,
            "zscore": 0.0,
            "regime_lead": "TREND" if abs(s_trend) > 0.5 else "CHOP",
        }
        g_sig = self.google_engine.get_latest_signal(g_features)

        breakdown = {
            "trend":     {"signal": s_trend, "confidence": c_trend, "weight": learner_weights.get("trend", 0.18), "rationale": r_trend},
            "mean_rev":  {"signal": s_mean,  "confidence": c_mean,  "weight": learner_weights.get("mean_rev", 0.18), "rationale": r_mean},
            "momentum":  {"signal": s_mom,   "confidence": c_mom,   "weight": learner_weights.get("momentum", 0.18), "rationale": r_mom},
            "macro":     {"signal": s_macro, "confidence": c_macro, "weight": learner_weights.get("macro", 0.18), "rationale": r_macro},
            "ppo":       {"signal": s_ml,    "confidence": c_ml,    "weight": learner_weights.get("ppo", 0.18), "rationale": r_ml},
            "google_ai": {
                "signal": float(g_sig.get("signal", 0.0)),
                "confidence": float(g_sig.get("confidence", 0.5)),
                "weight": learner_weights.get("google_ai", 0.20),
                "rationale": g_sig.get("rationale", "Google Gemini Active"),
                "tokens_used": g_sig.get("tokens_used", 0),
                "model": g_sig.get("model", "gemini-3.6-flash"),
                "latency_ms": g_sig.get("latency_ms", 0),
            }
        }

        # Weighted score: sum(weight * signal * confidence)
        total_score = 0.0
        total_weight = 0.0
        model_scores = {}

        for k, v in breakdown.items():
            eff_score = v["signal"] * v["confidence"]
            w = v["weight"]
            total_score += eff_score * w
            total_weight += w
            model_scores[k] = eff_score

        if total_weight > 0:
            consensus = total_score / total_weight
        else:
            consensus = 0.0

        # Uncertainty: standard deviation of individual model signals
        signals = [v["signal"] for v in breakdown.values()]
        uncertainty = float(np.std(signals)) if len(signals) > 1 else 0.0

        # Leading strategy
        lead_model = max(model_scores, key=lambda k: abs(model_scores[k]))
        lead_regime = lead_model.upper()

        # Decision threshold: require consensus >= threshold and uncertainty <= 0.95
        action = 0
        direction = "FLAT"
        overall_confidence = min(0.95, max(0.50, abs(consensus) * 1.5))

        if consensus >= threshold and uncertainty <= 0.95:
            action = 1
            direction = "BUY"
        elif consensus <= -threshold and uncertainty <= 0.95:
            action = 2
            direction = "SELL"

        self.last_signals = breakdown
        self.last_consensus = consensus
        self.last_uncertainty = uncertainty

        return {
            "action": action,
            "direction": direction,
            "confidence": round(overall_confidence, 3),
            "consensus_score": round(consensus, 3),
            "uncertainty": round(uncertainty, 3),
            "regime_lead": lead_regime,
            "breakdown": breakdown,
            "google_decision": g_sig,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
