# Risk Management & Safeguards

To prevent drawdown, margin calls, or over-leveraging, the engine enforces 4 strict layers of risk management:

## 1. Free Margin Guard (`IsMarginSafe`)
* **Min Free Margin:** Must be $\ge \$500$ AND $\ge 15\%$ of Account Balance.
* If free margin is below this threshold, incoming trade triggers are rejected instantly.

## 2. Margin Level Guard
* **Min Margin Level:** Must be $\ge 200\%$.
* If account margin level drops below $200\%$, new trade entries are blocked.

## 3. Position Cap
* **Max Active Cluster Positions:** Hard limit of **9 positions** (3 full 3-tranche clusters) open simultaneously across the account.

## 4. ATR Volatility Breakeven Shield
* As soon as trade profit reaches $+1.0 \times ATR(14)$, the EA automatically moves Stop Loss to $\text{Open Price} + \text{Spread}$.
* Once locked, the trade cannot lose money regardless of market volatility.

## 5. Chandelier ATR Trailing Stop
* For Tranches 1 & 3, trails Stop Loss at $1.2 \times ATR(14)$ behind price as profits expand.
