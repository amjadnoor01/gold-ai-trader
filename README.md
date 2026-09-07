# ⚡ MetaTrader 5 Autonomous AI Quantitative Trading Engine (v2.70)

[![MT5 Autonomous Engine CI/CD Pipeline](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/mt5_ci_cd.yml/badge.svg)](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/mt5_ci_cd.yml)
[![Autonomous AI Telemetry](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/ai_telemetry_report.yml/badge.svg)](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/ai_telemetry_report.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![MetaTrader 5: MQL5 Build 6182](https://img.shields.io/badge/MetaTrader_5-MQL5_6182-green.svg)](https://www.metatrader5.com/)

A high-frequency, fully autonomous multi-symbol quantitative AI trading system engineered specifically for **MetaTrader 5 (MT5) on macOS (Wine/CrossOver)**. Features an online warm-start Stochastic Gradient Descent (SGD) classifier microservice, multi-asset technical feature extraction, dynamic risk and free margin safeguards, ATR volatility breakeven shields, and on-chart visual trade setup graphics.

---

## 🏛️ System Architecture

```mermaid
graph TD
    subgraph MT5 Terminal Runtime (macOS / Wine)
        A[AiBridgeEA.mq5 v2.70] -->|Extract Features [RSI, MACD, EMA, Vol, ADX]| B[MQL5/Files/live_features_SYMBOL.json]
        E[MQL5/Files/cluster_command_SYMBOL.json] -->|Read & Filter Symbol| A
        A -->|Trade Execution + ATR Breakeven Shield| F[MT5 Broker Server]
        F -->|Deal Exit Feedback| G[MQL5/Files/ai_feedback.json]
    end

    subgraph macOS Launchd Daemons (24/7 Persistent)
        C[mt5_autonomy_loop.py Daemon] -->|Poll Features| B
        C -->|POST /predict| D[Local SGD AI Brain Microservice\n127.0.0.1:8000]
        D -->|Prediction & Strength Score| C
        C -->|Dispatch Targeted Clusters| E
        G -->|POST /feedback| D
        D -->|Warm-Start partial_fit| H[SQLite ~/trading_poc.db]
    end
```

---

## 🔑 Key Capabilities

1. **Multi-Symbol Intelligence & Signal Routing:**
   - Simultaneously scans and trades 8 major instruments: `XAUUSD`, `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCHF`, `USDCAD`, `USDSEK`.
   - Feature vectors $[RSI(14), MACD_{diff}, EMA_{slope}, Volatility, ADX(14)]$ extracted per symbol.

2. **Market Regime Awareness:**
   - **Trending Regime ($ADX \ge 25$):** Multiplies directional strength confidence by $1.15\times$, pursuing multi-tranche trend expansion.
   - **Ranging Regime ($ADX \le 18$):** Adapts target pips for mean-reversion scalping.

3. **Strict Free Margin & Risk Control Guard (`IsMarginSafe`):**
   - Free Margin required $\ge \$500$ AND $\text{Free Margin}/\text{Balance} \ge 15\%$.
   - Margin Level required $\ge 200\%$.
   - Maximum active cluster position cap: **9 positions** across account.

4. **ATR Volatility Breakeven Shield & Chandelier Trailing:**
   - Locks Stop Loss to $\text{Open Price} + \text{Spread}$ as soon as trade profit reaches $+1.0\times ATR(14)$.
   - Trails Stop Loss at $1.2\times ATR(14)$ behind price for Tranche 1 (Alpha Scalp) & Tranche 3 (Impulse Runner).

5. **Self-Evolving Dynamic Parameter Auto-Tuner:**
   - Evaluates rolling 20-trade win rate per symbol.
   - Win Rate $\ge 65\% \rightarrow$ Boosts lot size by $+25\%$ and lowers trigger threshold to $30\%$.
   - Win Rate $\le 45\% \rightarrow$ Reduces lot size by $-25\%$ and tightens trigger threshold to $42\%$.

6. **Visual On-Chart Trade Setup Graphics:**
   - Draws target Risk/Reward boxes (`OBJ_RECTANGLE`) and signal entry arrows (`OBJ_ARROW`) directly on the chart.

---

## ⚡ Quick Start & Deployment Guide

### 1. Start Local AI Brain Microservice & Autonomy Daemon
```bash
# Clone Repository
git clone https://github.com/amjadnoor01/gold-ai-trader.git
cd gold-ai-trader

# Create Virtual Environment & Install Dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Load macOS launchd 24/7 Daemons
launchctl load ~/Library/LaunchAgents/com.antigravity.mt5brain.plist
launchctl load ~/Library/LaunchAgents/com.antigravity.mt5autonomy.plist
```

### 2. Verify Microservice Metrics
```bash
curl -s http://127.0.0.1:8000/metrics
```
**Expected Output:**
```json
{
  "is_fitted": true,
  "feedback_count": 228,
  "wins": 215,
  "losses": 13,
  "win_rate": 0.943,
  "l2_alpha": 0.0001
}
```

### 3. Compile & Load MQL5 Expert Advisor in MT5
```bash
cd "/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5"
WINEPREFIX="/Users/amjadnoor/Library/Application Support/net.metaquotes.wine.metatrader5" "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine" "MetaEditor64.exe" /compile:"MQL5\Experts\AiBridgeEA.mq5" /log:"MQL5\Experts\compile_aibridge.log"
```

---

## 📡 API Endpoints Reference

| Endpoint | Method | Description |
|---|---|---|
| `/predict` | `POST` | Ingests 5-feature vector, returns directional strength score (-100 to +100), direction, regime, and confidence. |
| `/feedback` | `POST` | Ingests deal exit P&L and updates SGD model weights online via `partial_fit`. |
| `/metrics` | `GET` | Returns win rate, feedback count, L2 regularization state, and model coefficients. |
| `/cluster_trigger` | `POST` | Issues manual or automated multi-tranche cluster trigger command payload. |

---

## 📜 License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
