# Architecture & Data Flow

The system consists of three main decoupled layers operating asynchronously:

## 1. MQL5 Expert Advisor (`AiBridgeEA.mq5` v2.70)
* Runs on each MT5 chart window.
* Extracts 5 technical features every second:
  - $RSI(14)$
  - $MACD_{diff} = MACD(12,26,9) - Signal$
  - $EMA_{slope} = (EMA(20)_t - EMA(20)_{t-2}) / EMA(20)_{t-2}$
  - $Volatility = ATR(14) / Price_{close}$
  - $ADX(14)$
* Exports symbol-specific feature files: `live_features_<SYMBOL>.json`.

## 2. Multi-Symbol Autonomy Loop (`mt5_autonomy_loop.py`)
* Runs continuously as a macOS `launchd` daemon (`com.antigravity.mt5autonomy`).
* Scans all `live_features_*.json` files.
* Queries local SGD AI Brain microservice via HTTP `POST http://127.0.0.1:8000/predict`.
* Computes self-evolving trigger thresholds ($\ge 30\%$ to $\ge 42\%$) and lot size multipliers based on rolling win rate.
* Dispatches cluster triggers to `cluster_command_<SYMBOL>.json`.

## 3. High-Frequency Local SGD AI Brain Microservice (`mt5_brain_service.py`)
* Runs on `127.0.0.1:8000` via `com.antigravity.mt5brain`.
* Uses `StandardScaler` + `SGDClassifier(loss="log_loss", warm_start=True)` for online learning.
* Receives realized trade outcomes via `POST /feedback` and immediately retrains model weights online via `partial_fit`.
* Persists predictions, cluster triggers, and feedback logs into SQLite (`~/trading_poc.db`).
