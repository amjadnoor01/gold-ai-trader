# Gold AI Trading Bot 🤖📈

> Deep Reinforcement Learning system for autonomous XAUUSD (Gold) paper trading.
> **100% free / open-source** — no MetaTrader5, no paid API tokens required.

[![CI](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/ci.yml/badge.svg)](https://github.com/amjadnoor01/gold-ai-trader/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 🎯 What Is This?

An AI-powered Gold (XAUUSD) trading bot that:
- Uses **PPO (Proximal Policy Optimization)** deep RL for decision making
- Fetches **live prices via yfinance** — completely free, no API key
- Runs **paper trading** (virtual $10,000) to safely test strategies
- **Self-improves** after every closed trade via closed-loop regret-matching
- Trains with 140+ market features: multi-timeframe, macro, calendar events

---

## 🏗️ Architecture

```
Live Gold Price (yfinance, free)
        │
        ▼
  LiveGoldFeed (live_feed.py)
        │  tick every 10s
        ▼
 PaperTradingBot (live_paper_trade.py)
  ├── RL Model (PPO, stable-baselines3)
  │     └─ predicts: Flat | Long
  ├── PaperBroker (paper_broker.py)
  │     └─ SL/TP, PnL, virtual orders
  └── ClosedLoopLearner (learner.py)
        └─ updates ensemble weights from every trade outcome
```

---

## ⚡ Quick Start

```bash
# 1. Clone
git clone https://github.com/amjadnoor01/gold-ai-trader.git
cd gold-ai-trader

# 2. Create venv
python3 -m venv venv
source venv/bin/activate

# 3. Install
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env if needed (defaults work out of the box)

# 5. Run paper trading
python live_paper_trade.py
```

State is saved every tick to `logs/paper_state.json`.

---

## 🧪 Tests

```bash
pytest tests/ -v
```

| Test File | Coverage |
|---|---|
| `tests/test_paper_broker.py` | SL/TP, PnL, equity, commission |
| `tests/test_learner.py` | Weight diversity, regret, reset |
| `tests/test_live_feed.py` | Price fetch, callbacks (mocked) |

---

## 🤖 Training Your Own Model

```bash
# Fetch data (yfinance, free)
python scripts/fetch_all_data.py

# Train PPO model
python train/train_ppo.py

# Evaluate
python evaluate_model.py
```

Or use [Google Colab](colab_train_ultimate_150.ipynb) for free GPU training.

---

## 📊 Strategy

| Parameter | Value |
|---|---|
| Initial Balance | $10,000 |
| Risk per Trade | 1.5% of equity |
| Max Daily Drawdown | 3% |
| SL Distance | 1.5× ATR |
| TP Distance | 3× ATR (2:1 R:R) |
| Position Sizing | Kelly-fraction |

---

## 📁 Project Structure

```
├── live_paper_trade.py      # Main paper trading runner ← START HERE
├── live_feed.py             # yfinance live price feed
├── paper_broker.py          # Virtual broker (SL/TP, PnL)
├── learner.py               # Closed-loop trade feedback learner
├── env/                     # Gymnasium RL environments
├── features/                # 140+ feature engineering
├── models/                  # PPO, Dreamer, Ensemble
├── train/                   # Training scripts
├── backtest/                # Backtesting engine
├── tests/                   # pytest test suite
├── .github/workflows/ci.yml # GitHub Actions CI
└── requirements.txt         # Pure open-source deps
```

---

## ⚠️ Disclaimer

This is for **educational and research purposes only**.
Paper trading only — not financial advice. Trading real capital is risky.

---

## 📜 License

MIT © amjadnoor01 — forked and extended from [zero-was-here/tradingbot](https://github.com/zero-was-here/tradingbot)
