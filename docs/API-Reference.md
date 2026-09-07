# API Reference

The local FastAPI microservice runs on `http://127.0.0.1:8000`.

## Endpoints

### 1. `POST /predict`
Evaluates a 5-feature technical indicator vector and returns prediction metrics.

**Request Payload:**
```json
{
  "rsi": 55.4,
  "macd_diff": 0.12,
  "ema_slope": 0.04,
  "volatility": 0.0025,
  "adx": 28.5
}
```

**Response Payload:**
```json
{
  "timestamp": "2026-09-07T21:55:00.000000+00:00",
  "regime": "TRENDING_EXPANSION",
  "strength_score": 98.8,
  "direction": 1,
  "confidence": 0.994,
  "dynamic_threshold": 35.0,
  "cluster_eligible": true
}
```

---

### 2. `POST /feedback`
Ingests deal exit P&L and updates SGD model weights online via `partial_fit`.

**Request Payload:**
```json
{
  "trade_id": "MT5_58337208857",
  "direction": 1,
  "realized_pnl": 14.20,
  "rsi": 58.1,
  "macd_diff": 0.15,
  "ema_slope": 0.05,
  "volatility": 0.0024,
  "adx": 29.0
}
```

---

### 3. `GET /metrics`
Returns telemetry, win rate, and online model coefficients.

**Response Payload:**
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
