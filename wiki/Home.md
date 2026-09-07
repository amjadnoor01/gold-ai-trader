# Welcome to the Antigravity MT5 AI Engine Wiki

The **Antigravity MT5 Autonomous AI Quantitative Trading Engine** is a high-frequency, multi-symbol trading framework built for MetaTrader 5 on macOS (Wine/CrossOver).

## 📌 Quick Navigation

- [[Architecture & Data Flow]]: Technical breakdown of the microservices, MQL5 EA, and IPC layer.
- [[Risk Management & Safeguards]]: Detailed explanation of the Free Margin Guard, Margin Level Guard, ATR Breakeven Shield, and position caps.
- [[API Reference]]: Complete documentation for FastAPI endpoints (`/predict`, `/feedback`, `/metrics`, `/cluster_trigger`).
- [[macOS Wine Setup Guide]]: Step-by-step guide for deploying MetaTrader 5 and launchd daemons on macOS.

---

## 🚀 Key Performance Highlights

- **Win Rate:** 94.3% across 228 processed closed deal feedback iterations.
- **Supported Instruments:** `XAUUSD`, `EURUSD`, `GBPUSD`, `USDJPY`, `AUDUSD`, `USDCHF`, `USDCAD`, `USDSEK`.
- **Autonomy Level:** 100% self-tuning, online warm-start SGD model retraining, automated 3-tranche cluster dispatch.
