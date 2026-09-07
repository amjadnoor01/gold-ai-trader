//+------------------------------------------------------------------+
//|                                           GoldHedgerPro_v4.mq5   |
//|                                          Built for Micky @ PCG   |
//|    v4: Bug fixes (PipValue, fractal swing, SL, grid race) +      |
//|        BOS confirmation, trailing profit, capped lot scaling,    |
//|        smart weekend hedge, midnight-safe session times          |
//|    v4.01: News filter now blocks grid additions on an ALREADY    |
//|        active basket, not just new entries. Previously the grid  |
//|        kept averaging into news-driven spikes (e.g. FOMC) with   |
//|        zero awareness of the news window. Also added optional   |
//|        pre-news basket flatten (InpFlattenBeforeNews).           |
//|    v4.03: Dynamic ATR-based news-settle cooldown replaces fixed  |
//|        post-news window. Session windows (Asian/London/Overlap/  |
//|        NYClose) and trade start/end recalculated for confirmed   |
//|        GMT+0 broker server time (previously tuned for an         |
//|        assumed GMT+2/+3 offset and misaligned with real session  |
//|        hours).                                                   |
//+------------------------------------------------------------------+
#property copyright "PCG GoldHedger v4"
#property link      "peopleconnectglobal.com"
#property version   "4.03"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| ENUMS                                                             |
//+------------------------------------------------------------------+
enum ENUM_ENTRY_MODE {
   ENTRY_VOLUME_ADX_ONLY    = 0,
   ENTRY_SWEEP_ONLY         = 1,
   ENTRY_VOLUME_OR_SWEEP    = 2,
   ENTRY_VOLUME_AND_SWEEP   = 3
};

enum ENUM_SESSION {
   SESSION_NONE       = 0,
   SESSION_ASIAN      = 1,
   SESSION_LONDON     = 2,
   SESSION_OVERLAP    = 3,
   SESSION_NY_CLOSE   = 4
};

//+------------------------------------------------------------------+
//| INPUTS                                                            |
//+------------------------------------------------------------------+

input group "=== MONEY & LOT MANAGEMENT ==="
input double   InpInitialBalance     = 500.0;
input double   InpBaseLot            = 0.01;
input double   InpLotMultiplier      = 1.5;
input int      InpMaxPositions       = 4;
input bool     InpVolumeMultiplier   = false;
input double   InpVolMultiplierCap   = 5.0;      // v4: max scale factor (safety cap)

input group "=== BASKET PROFIT ==="
input double   InpBasketProfitUSD    = 4.00;
input double   InpSweepBasketTargetUSD = 3.00;
input bool     InpHideTP             = true;
input bool     InpUseTrailingProfit  = true;     // v4: trail basket close
input double   InpTrailRetreatPct    = 30.0;     // v4: close if profit retreats X% from peak

input group "=== ATR DYNAMIC GRID (Base) ==="
input int      InpATRPeriod          = 14;
input ENUM_TIMEFRAMES InpATRTimeframe = PERIOD_M20;
input double   InpATRMultiplier      = 1.3;
input double   InpMinDistancePips    = 80.0;
input double   InpMaxDistancePips    = 250.0;

input group "=== EMERGENCY STOP LOSS ==="
input bool     InpUseEmergencySL     = true;     // v4: hard SL on every position
input double   InpEmergencySLPips    = 500.0;    // v4: pips from open price

input group "=== ENTRY MODE SELECTOR ==="
input ENUM_ENTRY_MODE InpEntryMode   = ENTRY_VOLUME_OR_SWEEP;

input group "=== VOLUME + ADX ENTRY ==="
input int      InpADXPeriod          = 7;
input double   InpADXMinLevel        = 10.0;
input double   InpVolumeThreshold    = 0.5;
input int      InpVolumeAvgBars      = 20;
input ENUM_TIMEFRAMES InpEntryTF     = PERIOD_CURRENT;

input group "=== V4: LIQUIDITY SWEEP DETECTION ==="
input int      InpSweepLookback      = 30;
input int      InpSwingFractalPeriod = 5;        // v4: now actually used for fractal swing detection
input double   InpSweepMinWickPips   = 3.0;
input double   InpSweepMaxBodyPct    = 40.0;
input int      InpSweepValidityBars  = 3;
input bool     InpRequireBOSConfirm  = false;    // v4: require BOS before sweep entry

input group "=== V4: SESSION PROFILES ==="
input bool     InpUseSessionProfiles = false;

input group "--- Asian Session ---"
input bool     InpAsianEnabled       = true;
input string   InpAsianStart         = "00:00";  // server confirmed GMT+0; Tokyo session ~00:00-08:00 UTC
input string   InpAsianEnd           = "08:00";
input double   InpAsianADXMin        = 10.0;
input double   InpAsianVolThreshold  = 0.5;
input double   InpAsianATRMult       = 1.0;
input int      InpAsianMaxPositions  = 3;

input group "--- London Session ---"
input bool     InpLondonEnabled      = true;
input string   InpLondonStart        = "08:00";  // London open ~08:00 UTC
input string   InpLondonEnd          = "13:00";  // until NY/overlap begins
input double   InpLondonADXMin       = 10.0;
input double   InpLondonVolThreshold = 0.5;
input double   InpLondonATRMult      = 1.3;
input int      InpLondonMaxPositions = 4;

input group "--- London/NY Overlap ---"
input bool     InpOverlapEnabled     = true;
input string   InpOverlapStart       = "13:00";  // NY open / London-NY overlap, highest liquidity
input string   InpOverlapEnd         = "17:00";  // London close ~17:00 UTC
input double   InpOverlapADXMin      = 10.0;
input double   InpOverlapVolThreshold = 0.5;
input double   InpOverlapATRMult     = 1.5;
input int      InpOverlapMaxPositions = 5;

input group "--- NY Close ---"
input bool     InpNYCloseEnabled     = true;
input string   InpNYCloseStart       = "17:00";  // NY afternoon, tapering liquidity into close
input string   InpNYCloseEnd         = "21:00";  // FX/gold weekday close ~21:00-22:00 UTC
input double   InpNYCloseADXMin      = 10.0;
input double   InpNYCloseVolThreshold = 0.5;
input double   InpNYCloseATRMult     = 1.4;
input int      InpNYCloseMaxPositions = 3;

input group "=== TIER 1: MULTI-TF TREND FILTER ==="
input bool     InpUseTrendFilter     = false;
input ENUM_TIMEFRAMES InpTrendTF     = PERIOD_H1;
input int      InpTrendEMAPeriod     = 50;

input group "=== TIER 1: NEWS FILTER ==="
input bool     InpUseNewsFilter      = false;
input int      InpNewsMinutesBefore  = 30;       // Block starts this many minutes before the event
input int      InpNewsImportance     = 3;
input bool     InpFlattenBeforeNews  = false;    // v4.1: close active basket when news window opens (locks in current P&L)
input group "=== V4.2: NEWS SETTLE (dynamic post-news block) ==="
input int      InpNewsSettleMinMinutes = 15;     // Minimum minutes after event before checking if settled
input double   InpNewsSettleATRMult    = 1.3;    // Resume only once ATR <= baseline ATR * this multiplier
input int      InpNewsMaxBlockMinutes  = 120;    // Hard cap — force resume after this long regardless of volatility

input group "=== DRAWDOWN PROTECTION ==="
input double   InpMaxDrawdownPct     = 10.0;
input double   InpMaxSpreadPoints    = 400.0;

input group "=== TIER 2: RECOVERY MODE ==="
input bool     InpUseRecoveryMode    = true;
input int      InpRecoveryBaskets    = 5;
input double   InpRecoveryLotFactor  = 0.5;

input group "=== TIER 2: EQUITY PEAK ==="
input bool     InpUseEquityPeak      = true;
input double   InpEquityPeakDropPct  = 15.0;
input int      InpPeakCoolingHours   = 24;

input group "=== TIER 2: DAILY LOSS LIMIT ==="
input bool     InpUseDailyLossLimit  = true;
input double   InpMaxDailyLossPct    = 3.0;

input group "=== WEEKEND & TIME ==="
input bool     InpHedgeWeekend       = true;
input string   InpHedgeTime          = "20:45";  // ahead of weekly close, verify vs broker's exact Friday close time
input string   InpHedgeRelease       = "01:00";  // shortly after Monday reopen, lets spread normalize
input int      InpFridayCooldownMin  = 90;
input bool     InpBlockHolidays      = true;
input string   InpTradeStartTime     = "00:00";  // matches Asian session start (server = GMT+0)
input string   InpTradeEndTime       = "21:00";  // matches NY close session end

input group "=== EA IDENTITY ==="
input long     InpMagicNumber        = 11111;
input string   InpComment            = "GHPv4";

input group "=== AI PYTHON BRIDGE ==="
input bool     InpUseAIBridge        = true;     // Accept signals from Python Quant AI / Gemini
input string   InpBridgeCommandFile  = "ai_signals.json";
input string   InpBridgeStateFile    = "mt5_state.json";

//+------------------------------------------------------------------+
//| GLOBALS                                                           |
//+------------------------------------------------------------------+
CTrade         Trade;
CPositionInfo  PosInfo;

struct BasketInfo {
   int    buyCount;
   int    sellCount;
   double buyLots;
   double sellLots;
   double totalProfit;
   double highWaterProfit;   // v4: for trailing profit lock
   double lastBuyPrice;
   double lastSellPrice;
   double nextBuyLot;
   double nextSellLot;
   bool   active;
   bool   isSweepEntry;
   double targetUSD;
};

struct SessionParams {
   bool   enabled;
   double adxMin;
   double volThreshold;
   double atrMult;
   int    maxPositions;
};

BasketInfo     Basket;
double         PipValue;
double         PointSize;
int            Digits__;
bool           WeekendHedgeOpen      = false;
ulong          WeekendHedgeBuyTicket  = 0;
ulong          WeekendHedgeSellTicket = 0;
datetime       LastBarTime           = 0;
int            ADXHandle             = INVALID_HANDLE;
int            ATRHandle             = INVALID_HANDLE;
int            TrendEMAHandle        = INVALID_HANDLE;

double         EquityPeak            = 0;
datetime       CoolingOffUntil       = 0;
int            RecoveryBasketsLeft   = 0;
datetime       DailyResetTime        = 0;
double         DailyStartBalance     = 0;
bool           DailyLossLimitHit     = false;

// v4: sweep state + BOS confirmation
datetime       LastSweepTime         = 0;
int            LastSweepDirection    = 0;
double         SweepConfirmLevel     = 0;   // v4: BOS level price must cross before entry

// v4.2: dynamic news cooldown state
bool           NewsCooldownActive    = false;
datetime       ActiveNewsEventTime   = 0;   // the event currently being waited out
double         PreNewsATR            = 0;   // ATR snapshot captured when cooldown started
datetime       LastResolvedNewsEvent = 0;   // prevents re-triggering the same event every tick

ENUM_SESSION   CurrentSession        = SESSION_NONE;
SessionParams  ActiveParams;

int USDHolidays[] = { 101, 704, 1225, 1226 };

//+------------------------------------------------------------------+
//| HELPER: convert "HH:MM" string to integer minutes since midnight |
//| v4: replaces fragile string comparison — handles midnight wrap   |
//+------------------------------------------------------------------+
int TimeStrToMins(string t)
{
   return (int)StringToInteger(StringSubstr(t, 0, 2)) * 60
        + (int)StringToInteger(StringSubstr(t, 3, 2));
}

int CurrentTimeMins()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return dt.hour * 60 + dt.min;
}

bool InTimeRange(int nowMins, string startStr, string endStr)
{
   int s = TimeStrToMins(startStr);
   int e = TimeStrToMins(endStr);
   if(s <= e) return (nowMins >= s && nowMins < e);
   return (nowMins >= s || nowMins < e);  // midnight-spanning range
}

//+------------------------------------------------------------------+
//| OnInit                                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(StringFind(Symbol(), "XAUUSD") < 0 && StringFind(Symbol(), "GOLD") < 0)
      Print("WARNING: Optimized for XAUUSD/GOLD. Current: ", Symbol());

   PointSize = _Point;
   Digits__  = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

   // v4.3 FIX: correct PipValue across both classic and fractional quoting.
   // Classic gold (2 digits) / 4-digit forex: 1 pip = 1 point.
   // Fractional gold (3 digits, e.g. this Exness "m" symbol) / 5-digit forex:
   // brokers add one extra decimal, so 1 pip = 10 points.
   PipValue = (Digits__ == 3 || Digits__ == 5) ? PointSize * 10.0 : PointSize;

   ADXHandle      = iADX(Symbol(), InpEntryTF, InpADXPeriod);
   ATRHandle      = iATR(Symbol(), InpATRTimeframe, InpATRPeriod);
   TrendEMAHandle = iMA(Symbol(), InpTrendTF, InpTrendEMAPeriod, 0, MODE_EMA, PRICE_CLOSE);

   if(ADXHandle == INVALID_HANDLE || ATRHandle == INVALID_HANDLE ||
      TrendEMAHandle == INVALID_HANDLE) {
      Print("ERROR: Indicator init failed");
      return INIT_FAILED;
   }

   Trade.SetExpertMagicNumber(InpMagicNumber);
   Trade.SetDeviationInPoints(30);
   Trade.SetTypeFilling(ORDER_FILLING_IOC);
   Trade.LogLevel(LOG_LEVEL_ERRORS);

   EquityPeak = AccountInfoDouble(ACCOUNT_EQUITY);
   ResetDailyTracking();
   ResetBasket();
   ScanExistingPositions();
   UpdateActiveSession();

   // v4.2: log server-vs-GMT offset so session windows (defined in server
   // time) can be sanity-checked against real London/NY trading hours
   int gmtOffsetHours = (int)MathRound((TimeCurrent() - TimeGMT()) / 3600.0);
   Print("Server time: ", TimeToString(TimeCurrent(), TIME_DATE|TIME_MINUTES),
         " | GMT: ", TimeToString(TimeGMT(), TIME_DATE|TIME_MINUTES),
         " | Offset: GMT", (gmtOffsetHours >= 0 ? "+" : ""), gmtOffsetHours,
         " | Current session: ", SessionToString(CurrentSession),
         " (", ActiveParams.enabled ? "enabled" : "DISABLED", ")");

   Print("GoldHedgerPro v4.03 initialized | Magic: ", InpMagicNumber,
         " | Entry: ", EntryModeToString(InpEntryMode),
         " | PipValue: ", DoubleToString(PipValue, Digits__ + 1),
         " | EmergencySL: ", InpUseEmergencySL ? DoubleToString(InpEmergencySLPips, 0) + " pips" : "OFF",
         " | NewsBlocksGridAdds: true | FlattenBeforeNews: ", InpFlattenBeforeNews);

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(ADXHandle      != INVALID_HANDLE) IndicatorRelease(ADXHandle);
   if(ATRHandle      != INVALID_HANDLE) IndicatorRelease(ATRHandle);
   if(TrendEMAHandle != INVALID_HANDLE) IndicatorRelease(TrendEMAHandle);
   Comment("");
}

//+------------------------------------------------------------------+
//| OnTick                                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   UpdateEquityPeak();
   CheckDailyReset();
   UpdateActiveSession();

   if(CheckMaxDrawdown()) return;

   if(CheckDailyLossLimit()) {
      UpdateInfoPanel();
      return;
   }

   // --- AI PYTHON BRIDGE & TELEMETRY ---
   CheckAIBridgeCommands();

   // v4.2: evaluated once per tick — starts blocking InpNewsMinutesBefore
   // ahead of a high-impact event, then keeps blocking dynamically until
   // ATR and spread settle back near pre-news levels (capped by
   // InpNewsMaxBlockMinutes so it can never block forever).
   bool newsBlocking = CheckNewsCooldown();

   if(Basket.active) {
      UpdateBasketProfit();
      if(CheckBasketExit()) return;

      // v4.1 FIX: grid additions were previously unconditional, meaning the
      // martingale grid kept averaging into news-driven moves with no
      // awareness of the news filter or spread blowout. Both must now be
      // clear before adding another position to an active basket.
      bool spreadBlocking = IsSpreadTooHigh();

      if(newsBlocking) {
         Print("Grid add PAUSED: high-impact news window active");
      } else if(spreadBlocking) {
         Print("Grid add PAUSED: spread too high (", SymbolInfoInteger(Symbol(), SYMBOL_SPREAD), " pts)");
      } else {
         CheckGridAddPosition();
      }

      // v4.1: optionally flatten the basket ahead of high-impact news rather
      // than just freezing the grid. Off by default — closing early locks in
      // whatever loss currently exists, which is a deliberate trade-off the
      // user must opt into, not a default behavior. No re-entry guard needed:
      // ResetBasket() sets active=false, so this block can't fire again until
      // a brand-new basket opens (and new entries are themselves news-gated).
      if(InpFlattenBeforeNews && newsBlocking) {
         CloseAllBasketPositions("Pre-news flatten: high-impact news window");
         ResetBasket();
         return;
      }
   }

   ManageWeekendHedge();

   ENUM_TIMEFRAMES evalTF = InpEntryTF;
   if(iBars(Symbol(), evalTF) < 20) evalTF = (ENUM_TIMEFRAMES)_Period;
   datetime currentBarTime = iTime(Symbol(), evalTF, 0);
   if(currentBarTime == LastBarTime) {
      UpdateInfoPanel();
      return;
   }
   LastBarTime = currentBarTime;

   if(!IsTradeTimeAllowed())                                  return;
   if(IsHolidayToday())                                       return;
   if(IsSpreadTooHigh())                                      return;
   if(IsFridayCooldown())                                     return;
   if(InpUseSessionProfiles && !ActiveParams.enabled)         return;
   if(IsInCoolingOff())                                       return;
   if(newsBlocking)                                           return;

   if(!Basket.active) {
      CheckEntrySignal();
   }

   UpdateInfoPanel();
}

//+------------------------------------------------------------------+
//| v4: BASKET EXIT — fixed target OR trailing profit lock           |
//+------------------------------------------------------------------+
bool CheckBasketExit()
{
   // Update high-water mark
   if(Basket.totalProfit > Basket.highWaterProfit)
      Basket.highWaterProfit = Basket.totalProfit;

   bool shouldClose = false;
   string reason = "";

   // Fixed target
   if(Basket.totalProfit >= Basket.targetUSD) {
      shouldClose = true;
      reason = StringFormat("BasketTP: $%.2f (target $%.2f)", Basket.totalProfit, Basket.targetUSD);
   }

   // v4: trailing profit — only activates after target was reached once, then trails
   if(InpUseTrailingProfit && Basket.highWaterProfit >= Basket.targetUSD) {
      double retreatPct = (Basket.highWaterProfit > 0)
         ? (Basket.highWaterProfit - Basket.totalProfit) / Basket.highWaterProfit * 100.0
         : 0;
      if(retreatPct >= InpTrailRetreatPct) {
         shouldClose = true;
         reason = StringFormat("TrailingProfit: peak $%.2f, now $%.2f (%.1f%% retreat)",
                               Basket.highWaterProfit, Basket.totalProfit, retreatPct);
      }
   }

   if(shouldClose) {
      CloseAllBasketPositions(reason);
      ResetBasket();
      if(RecoveryBasketsLeft > 0) {
         RecoveryBasketsLeft--;
         Print("Recovery basket done | Remaining: ", RecoveryBasketsLeft);
      }
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| v4: SESSION DETECTION — integer-minute comparison, midnight-safe |
//+------------------------------------------------------------------+
void UpdateActiveSession()
{
   int nowMins = CurrentTimeMins();

   if(!InpUseSessionProfiles) {
      ActiveParams.enabled      = true;
      ActiveParams.adxMin       = InpADXMinLevel;
      ActiveParams.volThreshold = InpVolumeThreshold;
      ActiveParams.atrMult      = InpATRMultiplier;
      ActiveParams.maxPositions = InpMaxPositions;
      CurrentSession = SESSION_NONE;
      return;
   }

   if(InTimeRange(nowMins, InpAsianStart, InpAsianEnd)) {
      CurrentSession            = SESSION_ASIAN;
      ActiveParams.enabled      = InpAsianEnabled;
      ActiveParams.adxMin       = InpAsianADXMin;
      ActiveParams.volThreshold = InpAsianVolThreshold;
      ActiveParams.atrMult      = InpAsianATRMult;
      ActiveParams.maxPositions = InpAsianMaxPositions;
   }
   else if(InTimeRange(nowMins, InpLondonStart, InpLondonEnd)) {
      CurrentSession            = SESSION_LONDON;
      ActiveParams.enabled      = InpLondonEnabled;
      ActiveParams.adxMin       = InpLondonADXMin;
      ActiveParams.volThreshold = InpLondonVolThreshold;
      ActiveParams.atrMult      = InpLondonATRMult;
      ActiveParams.maxPositions = InpLondonMaxPositions;
   }
   else if(InTimeRange(nowMins, InpOverlapStart, InpOverlapEnd)) {
      CurrentSession            = SESSION_OVERLAP;
      ActiveParams.enabled      = InpOverlapEnabled;
      ActiveParams.adxMin       = InpOverlapADXMin;
      ActiveParams.volThreshold = InpOverlapVolThreshold;
      ActiveParams.atrMult      = InpOverlapATRMult;
      ActiveParams.maxPositions = InpOverlapMaxPositions;
   }
   else if(InTimeRange(nowMins, InpNYCloseStart, InpNYCloseEnd)) {
      CurrentSession            = SESSION_NY_CLOSE;
      ActiveParams.enabled      = InpNYCloseEnabled;
      ActiveParams.adxMin       = InpNYCloseADXMin;
      ActiveParams.volThreshold = InpNYCloseVolThreshold;
      ActiveParams.atrMult      = InpNYCloseATRMult;
      ActiveParams.maxPositions = InpNYCloseMaxPositions;
   }
   else {
      CurrentSession        = SESSION_NONE;
      ActiveParams.enabled  = false;
   }
}

//+------------------------------------------------------------------+
//| v4: LIQUIDITY SWEEP DETECTOR                                      |
//|   - Uses actual fractal swing highs/lows (InpSwingFractalPeriod) |
//|   - Sets SweepConfirmLevel for BOS gate in CheckEntrySignal      |
//| Returns: +1 bullish sweep, -1 bearish sweep, 0 none              |
//+------------------------------------------------------------------+
int DetectLiquiditySweep()
{
   int fp      = InpSwingFractalPeriod;
   int lookback = InpSweepLookback;
   int needed  = lookback + fp + 4;

   double highs[], lows[], opens[], closes[];
   ArraySetAsSeries(highs,  true);
   ArraySetAsSeries(lows,   true);
   ArraySetAsSeries(opens,  true);
   ArraySetAsSeries(closes, true);

   if(CopyHigh (Symbol(), InpEntryTF, 0, needed, highs)  < needed) return 0;
   if(CopyLow  (Symbol(), InpEntryTF, 0, needed, lows)   < needed) return 0;
   if(CopyOpen (Symbol(), InpEntryTF, 0, needed, opens)  < needed) return 0;
   if(CopyClose(Symbol(), InpEntryTF, 0, needed, closes) < needed) return 0;

   // bar 1 = last fully closed bar
   double bar1High  = highs[1];
   double bar1Low   = lows[1];
   double bar1Open  = opens[1];
   double bar1Close = closes[1];

   double bar1Range = bar1High - bar1Low;
   if(bar1Range <= 0) return 0;

   double bodyPct = MathAbs(bar1Close - bar1Open) / bar1Range * 100.0;
   if(bodyPct > InpSweepMaxBodyPct) return 0;

   // v4 FIX: find fractal swing highs/lows (N bars each side must be lower/higher)
   double swingHigh = 0;
   double swingLow  = DBL_MAX;
   for(int i = fp + 2; i < lookback + fp + 2; i++) {
      bool isSwingHigh = true;
      bool isSwingLow  = true;
      for(int j = 1; j <= fp; j++) {
         if(i - j < 0 || i + j >= needed) { isSwingHigh = false; isSwingLow = false; break; }
         if(highs[i] <= highs[i - j] || highs[i] <= highs[i + j]) isSwingHigh = false;
         if(lows[i]  >= lows[i - j]  || lows[i]  >= lows[i + j])  isSwingLow  = false;
      }
      if(isSwingHigh && highs[i] > swingHigh) swingHigh = highs[i];
      if(isSwingLow  && lows[i]  < swingLow)  swingLow  = lows[i];
   }

   if(swingHigh == 0 || swingLow == DBL_MAX) return 0;

   double minWick = InpSweepMinWickPips * PipValue;

   // BEARISH SWEEP: wick above fractal swing high, close back below
   if(bar1High > swingHigh && bar1Close < swingHigh) {
      double wickAbove = bar1High - MathMax(bar1Open, bar1Close);
      if(wickAbove >= minWick) {
         LastSweepTime      = iTime(Symbol(), InpEntryTF, 1);
         LastSweepDirection = -1;
         // v4: BOS level — price must drop below recent internal low to confirm
         SweepConfirmLevel  = MathMin(bar1Open, bar1Close);
         Print("BEARISH SWEEP | SwingHigh: ", DoubleToString(swingHigh, Digits__),
               " | Wick: ", DoubleToString(wickAbove / PipValue, 1), " pips",
               " | BOS level: ", DoubleToString(SweepConfirmLevel, Digits__));
         return -1;
      }
   }

   // BULLISH SWEEP: wick below fractal swing low, close back above
   if(bar1Low < swingLow && bar1Close > swingLow) {
      double wickBelow = MathMin(bar1Open, bar1Close) - bar1Low;
      if(wickBelow >= minWick) {
         LastSweepTime      = iTime(Symbol(), InpEntryTF, 1);
         LastSweepDirection = +1;
         // v4: BOS level — price must push above the pre-sweep close to confirm
         SweepConfirmLevel  = MathMax(bar1Open, bar1Close);
         Print("BULLISH SWEEP | SwingLow: ", DoubleToString(swingLow, Digits__),
               " | Wick: ", DoubleToString(wickBelow / PipValue, 1), " pips",
               " | BOS level: ", DoubleToString(SweepConfirmLevel, Digits__));
         return +1;
      }
   }

   // Still within validity window of a prior sweep
   if(LastSweepTime > 0) {
      int barsSince = iBarShift(Symbol(), InpEntryTF, LastSweepTime, false);
      if(barsSince > 0 && barsSince <= InpSweepValidityBars)
         return LastSweepDirection;
      LastSweepDirection = 0;
   }

   return 0;
}

//+------------------------------------------------------------------+
//| ENTRY SIGNAL                                                      |
//+------------------------------------------------------------------+
void CheckEntrySignal()
{
   // --- Volume + ADX signal ---
   int volumeSignal = 0;

   double adxMain[3], diPlus[3], diMinus[3];
   if(CopyBuffer(ADXHandle, 0, 0, 3, adxMain) >= 3 &&
      CopyBuffer(ADXHandle, 1, 0, 3, diPlus)  >= 3 &&
      CopyBuffer(ADXHandle, 2, 0, 3, diMinus) >= 3) {

      if(adxMain[1] >= ActiveParams.adxMin) {
         long volBars[];
         ArraySetAsSeries(volBars, true);
         if(CopyTickVolume(Symbol(), InpEntryTF, 1, InpVolumeAvgBars + 1, volBars) >= InpVolumeAvgBars + 1) {
            double avgVol = 0;
            for(int i = 1; i <= InpVolumeAvgBars; i++) avgVol += volBars[i];
            avgVol /= InpVolumeAvgBars;
            if(volBars[0] >= avgVol * ActiveParams.volThreshold)
               volumeSignal = (diPlus[1] > diMinus[1]) ? +1 : -1;
            else
               Print("Vol+ADX: ADX ", DoubleToString(adxMain[1], 1), " OK but volume ",
                     volBars[0], " < ", DoubleToString(avgVol * ActiveParams.volThreshold, 0), " threshold");
         } else {
            // v4.03: previously silent — on a fresh chart/symbol this can fail for many
            // bars in a row if tick-volume history hasn't backfilled yet, blocking every
            // entry with zero trace in the log.
            Print("Vol+ADX signal skipped: insufficient tick-volume history (need ",
                  InpVolumeAvgBars + 1, " bars on entry TF)");
         }
      }
   } else {
      // v4.03: previously silent — same issue as above but for the ADX buffer itself.
      Print("Vol+ADX signal skipped: insufficient ADX history on entry TF (handle ", ADXHandle, ")");
   }

   // --- Sweep signal ---
   int sweepSignal = 0;
   if(InpEntryMode != ENTRY_VOLUME_ADX_ONLY)
      sweepSignal = DetectLiquiditySweep();

   // --- Combine by mode ---
   int  finalSignal  = 0;
   bool isSweepEntry = false;

   switch(InpEntryMode) {
      case ENTRY_VOLUME_ADX_ONLY:
         finalSignal = volumeSignal;
         break;
      case ENTRY_SWEEP_ONLY:
         finalSignal  = sweepSignal;
         isSweepEntry = (sweepSignal != 0);
         break;
      case ENTRY_VOLUME_OR_SWEEP:
         if(sweepSignal != 0)      { finalSignal = sweepSignal;  isSweepEntry = true; }
         else if(volumeSignal != 0) { finalSignal = volumeSignal; }
         break;
      case ENTRY_VOLUME_AND_SWEEP:
         if(volumeSignal != 0 && sweepSignal != 0 && volumeSignal == sweepSignal)
            { finalSignal = volumeSignal; isSweepEntry = true; }
         break;
   }

   if(finalSignal == 0) return;

   // --- v4: BOS gate for sweep entries ---
   if(isSweepEntry && InpRequireBOSConfirm && SweepConfirmLevel > 0) {
      double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
      if(finalSignal == +1 && price < SweepConfirmLevel) {
         Print("Sweep BUY waiting for BOS above ", DoubleToString(SweepConfirmLevel, Digits__));
         return;
      }
      if(finalSignal == -1 && price > SweepConfirmLevel) {
         Print("Sweep SELL waiting for BOS below ", DoubleToString(SweepConfirmLevel, Digits__));
         return;
      }
   }

   // --- Trend filter — applied to ALL entry types (sweeps ARE trend-aligned entries) ---
   if(InpUseTrendFilter) {
      double emaH1[2];
      if(CopyBuffer(TrendEMAHandle, 0, 0, 2, emaH1) < 2) {
         // v4.03: previously silent — blocks every entry with zero trace in the log
         // if the H1 EMA history hasn't backfilled yet (e.g. fresh chart/symbol).
         Print("Entry skipped: insufficient H1 EMA history for trend filter (handle ", TrendEMAHandle, ")");
         return;
      }
      double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
      int trendDir = (price > emaH1[1]) ? +1 : -1;
      if(finalSignal != trendDir) {
         Print("Signal ", finalSignal, " rejected by trend filter (trend: ", trendDir, ")");
         return;
      }
   }

   // --- Spread quality gate: reject if spread > 15% of grid distance ---
   double atr = GetATRValue();
   if(atr > 0) {
      double gridDist  = MathMax(InpMinDistancePips * PipValue,
                         MathMin(InpMaxDistancePips * PipValue, atr * ActiveParams.atrMult));
      double spreadPips = SymbolInfoInteger(Symbol(), SYMBOL_SPREAD) * PointSize / PipValue;
      double gridPips   = gridDist / PipValue;
      if(spreadPips > gridPips * 0.60) {
         Print("Entry skipped: spread ", DoubleToString(spreadPips, 1),
               " pips > 60% of grid ", DoubleToString(gridPips, 1), " pips");
         return;
      }
   }

   // --- Execute ---
   double firstLot     = CalculateLotForLevel(0);
   double basketTarget = isSweepEntry ? InpSweepBasketTargetUSD : InpBasketProfitUSD;
   string label        = isSweepEntry ? (finalSignal == +1 ? "SweepBUY" : "SweepSELL")
                                      : (finalSignal == +1 ? "VolBUY"   : "VolSELL");

   ENUM_ORDER_TYPE orderType = (finalSignal == +1) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(OpenBasketTrade(orderType, firstLot, label, isSweepEntry, basketTarget)) {
      Print("Entry ", EnumToString(orderType),
            " | Mode: ", EntryModeToString(InpEntryMode),
            " | Sweep: ", isSweepEntry,
            " | Session: ", SessionToString(CurrentSession),
            " | Target: $", DoubleToString(basketTarget, 2));
   }
}

//+------------------------------------------------------------------+
//| v4: GRID — mutual exclusion: only one side adds per check        |
//+------------------------------------------------------------------+
void CheckGridAddPosition()
{
   int totalPos = Basket.buyCount + Basket.sellCount;
   if(totalPos >= ActiveParams.maxPositions) return;

   double atr = GetATRValue();
   if(atr <= 0) return;

   double gridDistance = MathMax(InpMinDistancePips * PipValue,
                         MathMin(InpMaxDistancePips * PipValue, atr * ActiveParams.atrMult));

   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);

   // v4 FIX: return after first add — prevents both sides adding on same tick
   if(Basket.buyCount > 0 && Basket.buyCount < ActiveParams.maxPositions) {
      if(bid < Basket.lastBuyPrice - gridDistance) {
         OpenBasketTrade(ORDER_TYPE_BUY,
                         CalculateLotForLevel(Basket.buyCount),
                         "Grid-BUY-" + IntegerToString(Basket.buyCount + 1),
                         Basket.isSweepEntry, Basket.targetUSD);
         return;  // don't check sell side this tick
      }
   }

   if(Basket.sellCount > 0 && Basket.sellCount < ActiveParams.maxPositions) {
      if(ask > Basket.lastSellPrice + gridDistance) {
         OpenBasketTrade(ORDER_TYPE_SELL,
                         CalculateLotForLevel(Basket.sellCount),
                         "Grid-SELL-" + IntegerToString(Basket.sellCount + 1),
                         Basket.isSweepEntry, Basket.targetUSD);
      }
   }
}

//+------------------------------------------------------------------+
//| OPEN TRADE — v4: adds per-position emergency SL                  |
//+------------------------------------------------------------------+
bool OpenBasketTrade(ENUM_ORDER_TYPE type, double lot, string label,
                     bool isSweep = false, double basketTarget = 0)
{
   lot = NormalizeLot(lot);
   if(lot <= 0) return false;

   string comment = InpComment + "-" + label;
   double ask     = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double bid     = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double slDist  = InpEmergencySLPips * PipValue;
   bool   result  = false;

   if(InpUseEmergencySL) {
      // v4: hard SL on every position — protects account if EA is offline
      if(type == ORDER_TYPE_BUY)
         result = Trade.Buy(lot,  Symbol(), 0, bid - slDist, 0, comment);
      else
         result = Trade.Sell(lot, Symbol(), 0, ask + slDist, 0, comment);
   } else {
      if(type == ORDER_TYPE_BUY)
         result = Trade.Buy(lot,  Symbol(), 0, 0, 0, comment);
      else
         result = Trade.Sell(lot, Symbol(), 0, 0, 0, comment);
   }

   if(result) {
      if(type == ORDER_TYPE_BUY) {
         Basket.buyCount++;
         Basket.buyLots     += lot;
         Basket.lastBuyPrice = ask;
         Basket.nextBuyLot   = CalculateLotForLevel(Basket.buyCount);
      } else {
         Basket.sellCount++;
         Basket.sellLots      += lot;
         Basket.lastSellPrice  = bid;
         Basket.nextSellLot    = CalculateLotForLevel(Basket.sellCount);
      }
      if(!Basket.active) {
         Basket.isSweepEntry  = isSweep;
         Basket.targetUSD     = (basketTarget > 0) ? basketTarget : InpBasketProfitUSD;
         Basket.highWaterProfit = 0;
      }
      Basket.active = true;
   } else {
      Print("ERROR opening ", EnumToString(type), ": ", Trade.ResultRetcodeDescription());
   }

   return result;
}

//+------------------------------------------------------------------+
//| CLOSE / UPDATE / SCAN                                             |
//+------------------------------------------------------------------+
void CloseAllBasketPositions(string reason)
{
   Print("Closing basket | Reason: ", reason);
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--) {
      if(PosInfo.SelectByIndex(i)) {
         if(PosInfo.Magic() == InpMagicNumber && PosInfo.Symbol() == Symbol()) {
            if(PosInfo.Ticket() == WeekendHedgeBuyTicket ||
               PosInfo.Ticket() == WeekendHedgeSellTicket) continue;
            Trade.PositionClose(PosInfo.Ticket(), 30);
         }
      }
   }
}

void UpdateBasketProfit()
{
   double prevHWM      = Basket.highWaterProfit;
   Basket.totalProfit  = 0;
   Basket.buyCount     = 0;
   Basket.sellCount    = 0;
   Basket.buyLots      = 0;
   Basket.sellLots     = 0;

   bool stillActive = false;
   for(int i = 0; i < PositionsTotal(); i++) {
      if(PosInfo.SelectByIndex(i)) {
         if(PosInfo.Magic() == InpMagicNumber && PosInfo.Symbol() == Symbol()) {
            if(PosInfo.Ticket() == WeekendHedgeBuyTicket ||
               PosInfo.Ticket() == WeekendHedgeSellTicket) continue;

            Basket.totalProfit += PosInfo.Profit() + PosInfo.Swap() + PosInfo.Commission();
            if(PosInfo.PositionType() == POSITION_TYPE_BUY) {
               Basket.buyCount++;
               Basket.buyLots += PosInfo.Volume();
               Basket.lastBuyPrice = PosInfo.PriceOpen();
            } else {
               Basket.sellCount++;
               Basket.sellLots += PosInfo.Volume();
               Basket.lastSellPrice = PosInfo.PriceOpen();
            }
            stillActive = true;
         }
      }
   }
   Basket.active          = stillActive;
   Basket.highWaterProfit = prevHWM;  // preserve across recalc
}

void ScanExistingPositions()
{
   ResetBasket();
   for(int i = 0; i < PositionsTotal(); i++) {
      if(PosInfo.SelectByIndex(i)) {
         if(PosInfo.Magic() == InpMagicNumber && PosInfo.Symbol() == Symbol()) {
            if(PosInfo.PositionType() == POSITION_TYPE_BUY) {
               Basket.buyCount++;
               Basket.buyLots += PosInfo.Volume();
               Basket.lastBuyPrice = PosInfo.PriceOpen();
            } else {
               Basket.sellCount++;
               Basket.sellLots += PosInfo.Volume();
               Basket.lastSellPrice = PosInfo.PriceOpen();
            }
            Basket.active    = true;
            Basket.targetUSD = InpBasketProfitUSD;
         }
      }
   }
   if(Basket.active) {
      Basket.nextBuyLot  = CalculateLotForLevel(Basket.buyCount);
      Basket.nextSellLot = CalculateLotForLevel(Basket.sellCount);
   }
}

//+------------------------------------------------------------------+
//| DRAWDOWN / RECOVERY                                               |
//+------------------------------------------------------------------+
bool CheckMaxDrawdown()
{
   double equity     = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double drawdownPct = (balance - equity) / balance * 100.0;

   if(drawdownPct >= InpMaxDrawdownPct) {
      Print("MAX DRAWDOWN HIT: ", DoubleToString(drawdownPct, 2), "% | Closing all");
      CloseAllPositionsEmergency();
      if(InpUseRecoveryMode) {
         RecoveryBasketsLeft = InpRecoveryBaskets;
         Print("RECOVERY MODE | Next ", InpRecoveryBaskets, " baskets at ",
               InpRecoveryLotFactor * 100, "% lot");
      }
      return true;
   }
   return false;
}

void CloseAllPositionsEmergency()
{
   int total = PositionsTotal();
   for(int i = total - 1; i >= 0; i--) {
      if(PosInfo.SelectByIndex(i)) {
         if(PosInfo.Symbol() == Symbol() && PosInfo.Magic() == InpMagicNumber)
            Trade.PositionClose(PosInfo.Ticket(), 50);
      }
   }
   ResetBasket();
   WeekendHedgeOpen       = false;
   WeekendHedgeBuyTicket  = 0;
   WeekendHedgeSellTicket = 0;
}

//+------------------------------------------------------------------+
//| EQUITY PEAK / DAILY TRACKING                                      |
//+------------------------------------------------------------------+
void UpdateEquityPeak()
{
   if(!InpUseEquityPeak) return;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   if(equity > EquityPeak) EquityPeak = equity;

   double dropPct = (EquityPeak > 0) ? (EquityPeak - equity) / EquityPeak * 100.0 : 0;
   if(dropPct >= InpEquityPeakDropPct && CoolingOffUntil < TimeCurrent()) {
      CoolingOffUntil = TimeCurrent() + InpPeakCoolingHours * 3600;
      Print("EQUITY PEAK DROP ", DoubleToString(dropPct, 2),
            "% | Cooling-off until ", TimeToString(CoolingOffUntil));
      if(Basket.active) {
         CloseAllBasketPositions("Equity peak protection");
         ResetBasket();
      }
   }
}

bool IsInCoolingOff() { return (CoolingOffUntil > TimeCurrent()); }

void CheckDailyReset()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   datetime todayStart = StringToTime(StringFormat("%04d.%02d.%02d 00:00", dt.year, dt.mon, dt.day));
   if(DailyResetTime < todayStart) ResetDailyTracking();
}

void ResetDailyTracking()
{
   DailyResetTime    = TimeCurrent();
   DailyStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   DailyLossLimitHit = false;
}

bool CheckDailyLossLimit()
{
   if(!InpUseDailyLossLimit) return false;
   if(DailyLossLimitHit)     return true;

   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double lossPct = (DailyStartBalance - equity) / DailyStartBalance * 100.0;

   if(lossPct >= InpMaxDailyLossPct) {
      DailyLossLimitHit = true;
      Print("DAILY LOSS LIMIT HIT: ", DoubleToString(lossPct, 2), "%");
      if(Basket.active) {
         CloseAllBasketPositions("Daily loss limit");
         ResetBasket();
      }
      return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| v4.2: NEWS FILTER — finds the nearest qualifying high-impact      |
//|   event (USD/EUR) within [now - maxBlockMinutes, now + before]   |
//|   so a just-started cooldown is also detected after EA restart.  |
//+------------------------------------------------------------------+
datetime FindNearestHighImpactEvent(datetime now)
{
   datetime from = now - InpNewsMaxBlockMinutes * 60;
   datetime to   = now + InpNewsMinutesBefore   * 60;
   datetime best = 0;

   string currencies[] = {"USD", "EUR"};
   for(int c = 0; c < ArraySize(currencies); c++) {
      MqlCalendarValue values[];
      int count = CalendarValueHistory(values, from, to, NULL, currencies[c]);
      for(int i = 0; i < count; i++) {
         MqlCalendarEvent ev;
         if(!CalendarEventById(values[i].event_id, ev)) continue;
         if((int)ev.importance < InpNewsImportance) continue;
         if(best == 0 || MathAbs((long)(values[i].time - now)) < MathAbs((long)(best - now)))
            best = values[i].time;
      }
   }
   return best;
}

//+------------------------------------------------------------------+
//| v4.2: NEWS COOLDOWN — starts InpNewsMinutesBefore ahead of an     |
//|   event, then instead of resuming after a fixed time, waits for  |
//|   ATR to fall back near its pre-news baseline AND spread to      |
//|   normalize. InpNewsMaxBlockMinutes hard-caps the wait so the EA |
//|   can never get stuck blocked forever.                           |
//| Returns true while trading should stay blocked.                  |
//+------------------------------------------------------------------+
bool CheckNewsCooldown()
{
   if(!InpUseNewsFilter) return false;

   datetime now = TimeCurrent();

   if(!NewsCooldownActive) {
      datetime eventTime = FindNearestHighImpactEvent(now);
      if(eventTime == 0) return false;

      // Already fully resolved this exact event — don't re-trigger every tick
      if(eventTime == LastResolvedNewsEvent) return false;

      // Event already fully expired beyond our max block window — ignore it
      if(now > eventTime && (now - eventTime) >= InpNewsMaxBlockMinutes * 60) {
         LastResolvedNewsEvent = eventTime;
         return false;
      }

      NewsCooldownActive  = true;
      ActiveNewsEventTime = eventTime;
      PreNewsATR          = GetATRValue();
      Print("News cooldown STARTED | event: ", TimeToString(eventTime),
            " | baseline ATR: ", DoubleToString(PreNewsATR, Digits__ + 1));
   }

   // Still in the pre-news lead-in window
   if(now < ActiveNewsEventTime) return true;

   int minutesSinceEvent = (int)((now - ActiveNewsEventTime) / 60);

   // Hard cap — force resume even if volatility hasn't normalized
   if(minutesSinceEvent >= InpNewsMaxBlockMinutes) {
      Print("News cooldown ENDED (max wait ", InpNewsMaxBlockMinutes, " min reached)");
      NewsCooldownActive    = false;
      LastResolvedNewsEvent = ActiveNewsEventTime;
      return false;
   }

   // Minimum settle time must pass before we even check volatility
   if(minutesSinceEvent < InpNewsSettleMinMinutes) return true;

   double currentATR  = GetATRValue();
   bool   atrSettled   = (PreNewsATR <= 0) || (currentATR <= PreNewsATR * InpNewsSettleATRMult);
   bool   spreadSettled = !IsSpreadTooHigh();

   if(atrSettled && spreadSettled) {
      Print("News cooldown ENDED (settled after ", minutesSinceEvent, " min) | ATR ",
            DoubleToString(currentATR, Digits__ + 1), " vs baseline ", DoubleToString(PreNewsATR, Digits__ + 1));
      NewsCooldownActive    = false;
      LastResolvedNewsEvent = ActiveNewsEventTime;
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| v4: WEEKEND HEDGE — directional (hedges net exposure, not both)  |
//+------------------------------------------------------------------+
void ManageWeekendHedge()
{
   if(!InpHedgeWeekend) return;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int nowMins = dt.hour * 60 + dt.min;

   // Friday: open directional hedge against net basket exposure
   if(dt.day_of_week == 5 &&
      nowMins == TimeStrToMins(InpHedgeTime) && !WeekendHedgeOpen) {

      double netLots = Basket.buyLots - Basket.sellLots;

      if(netLots > 0.0) {
         // Net long basket — hedge with a sell at 50% of net exposure
         double hedgeLot = NormalizeLot(netLots * 0.5);
         if(hedgeLot > 0 && Trade.Sell(hedgeLot, Symbol(), 0, 0, 0, InpComment + "-WHedge-SELL"))
            WeekendHedgeSellTicket = Trade.ResultOrder();
         WeekendHedgeOpen = true;
         Print("Weekend hedge SELL ", DoubleToString(hedgeLot, 2), " lots (net long exposure)");
      } else if(netLots < 0.0) {
         // Net short basket — hedge with a buy
         double hedgeLot = NormalizeLot(MathAbs(netLots) * 0.5);
         if(hedgeLot > 0 && Trade.Buy(hedgeLot, Symbol(), 0, 0, 0, InpComment + "-WHedge-BUY"))
            WeekendHedgeBuyTicket = Trade.ResultOrder();
         WeekendHedgeOpen = true;
         Print("Weekend hedge BUY ", DoubleToString(hedgeLot, 2), " lots (net short exposure)");
      }
      // No open basket = no hedge needed
   }

   // Monday: release hedge
   if(dt.day_of_week == 1 &&
      nowMins == TimeStrToMins(InpHedgeRelease) && WeekendHedgeOpen) {

      if(WeekendHedgeBuyTicket  > 0) Trade.PositionClose(WeekendHedgeBuyTicket,  30);
      if(WeekendHedgeSellTicket > 0) Trade.PositionClose(WeekendHedgeSellTicket, 30);
      WeekendHedgeOpen       = false;
      WeekendHedgeBuyTicket  = 0;
      WeekendHedgeSellTicket = 0;
   }
}

//+------------------------------------------------------------------+
//| TIME / SPREAD / HOLIDAY                                           |
//+------------------------------------------------------------------+
bool IsTradeTimeAllowed()
{
   int nowMins = CurrentTimeMins();
   return InTimeRange(nowMins, InpTradeStartTime, InpTradeEndTime);
}

bool IsFridayCooldown()
{
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.day_of_week != 5) return false;
   datetime hedgeDT = StringToTime(TimeToString(TimeCurrent(), TIME_DATE) + " " + InpHedgeTime);
   return (TimeCurrent() >= hedgeDT - InpFridayCooldownMin * 60);
}

bool IsHolidayToday()
{
   if(!InpBlockHolidays) return false;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   int today = dt.mon * 100 + dt.day;
   for(int i = 0; i < ArraySize(USDHolidays); i++)
      if(USDHolidays[i] == today) return true;
   return false;
}

bool IsSpreadTooHigh()
{
   return (SymbolInfoInteger(Symbol(), SYMBOL_SPREAD) > InpMaxSpreadPoints);
}

double GetATRValue()
{
   double atr[];
   ArraySetAsSeries(atr, true);
   if(CopyBuffer(ATRHandle, 0, 1, 1, atr) < 1) return 0;
   return atr[0];
}

//+------------------------------------------------------------------+
//| v4: LOT SIZING — scale factor capped at InpVolMultiplierCap      |
//+------------------------------------------------------------------+
double CalculateLotForLevel(int level)
{
   double baseLot = InpBaseLot;
   if(RecoveryBasketsLeft > 0) baseLot *= InpRecoveryLotFactor;
   if(InpVolumeMultiplier) {
      double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
      double scaleFactor = MathMin(balance / InpInitialBalance, InpVolMultiplierCap);
      baseLot *= scaleFactor;
   }
   double lot = baseLot;
   for(int i = 0; i < level; i++) lot *= InpLotMultiplier;
   return NormalizeLot(lot);
}

double NormalizeLot(double lot)
{
   double minLot  = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
   lot = MathMax(minLot, MathMin(maxLot, lot));
   lot = MathRound(lot / lotStep) * lotStep;
   return NormalizeDouble(lot, 2);
}

void ResetBasket()
{
   Basket.buyCount        = 0;
   Basket.sellCount       = 0;
   Basket.buyLots         = 0;
   Basket.sellLots        = 0;
   Basket.totalProfit     = 0;
   Basket.highWaterProfit = 0;
   Basket.lastBuyPrice    = 0;
   Basket.lastSellPrice   = 0;
   Basket.nextBuyLot      = CalculateLotForLevel(0);
   Basket.nextSellLot     = CalculateLotForLevel(0);
   Basket.active        = false;
   Basket.isSweepEntry  = false;
   Basket.targetUSD     = InpBasketProfitUSD;
}

//+------------------------------------------------------------------+
//| STRING HELPERS                                                    |
//+------------------------------------------------------------------+
string EntryModeToString(ENUM_ENTRY_MODE m)
{
   switch(m) {
      case ENTRY_VOLUME_ADX_ONLY:  return "Vol+ADX";
      case ENTRY_SWEEP_ONLY:       return "Sweep";
      case ENTRY_VOLUME_OR_SWEEP:  return "Vol OR Sweep";
      case ENTRY_VOLUME_AND_SWEEP: return "Vol AND Sweep";
   }
   return "Unknown";
}

string SessionToString(ENUM_SESSION s)
{
   switch(s) {
      case SESSION_ASIAN:    return "Asian";
      case SESSION_LONDON:   return "London";
      case SESSION_OVERLAP:  return "Overlap";
      case SESSION_NY_CLOSE: return "NY Close";
   }
   return "None";
}

//+------------------------------------------------------------------+
//| INFO PANEL                                                        |
//+------------------------------------------------------------------+
void UpdateInfoPanel()
{
   double equity    = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double drawdown  = (balance > 0) ? (balance - equity) / balance * 100.0 : 0;
   double peakDrop  = (EquityPeak > 0) ? (EquityPeak - equity) / EquityPeak * 100.0 : 0;
   double dailyLoss = (DailyStartBalance > 0) ? (DailyStartBalance - equity) / DailyStartBalance * 100.0 : 0;
   double atr       = GetATRValue();
   double gridPips  = (atr > 0)
      ? MathMax(InpMinDistancePips, MathMin(InpMaxDistancePips, atr * ActiveParams.atrMult / PipValue))
      : 0;

   string sweepStat = (LastSweepDirection == 0) ? "none" :
                      (LastSweepDirection == +1 ? "BULL" : "BEAR");
   string bosStat   = (SweepConfirmLevel > 0)
      ? DoubleToString(SweepConfirmLevel, Digits__) : "n/a";
   string newsStat  = !InpUseNewsFilter ? "OFF" :
                       !NewsCooldownActive ? "clear" :
                       (TimeCurrent() < ActiveNewsEventTime)
                          ? "PRE-NEWS (" + TimeToString(ActiveNewsEventTime, TIME_MINUTES) + ")"
                          : "SETTLING (" + IntegerToString((int)((TimeCurrent() - ActiveNewsEventTime) / 60)) + "m)";
   string coolStat  = IsInCoolingOff() ? TimeToString(CoolingOffUntil, TIME_MINUTES) : "OFF";
   string recovStat = (RecoveryBasketsLeft > 0) ? IntegerToString(RecoveryBasketsLeft) + " left" : "OFF";
   string dailyStat = DailyLossLimitHit ? "LIMIT HIT"
                      : StringFormat("%.2f%% / %.0f%%", dailyLoss, InpMaxDailyLossPct);

   string panel = StringFormat(
      "==== GoldHedger Pro v4.03 ====\n"
      "Balance:   $%.2f\n"
      "Equity:    $%.2f\n"
      "Peak:      $%.2f (drop %.2f%%)\n"
      "Drawdown:  %.2f%% / Max %.0f%%\n"
      "Daily P&L: %s\n"
      "-------------------------\n"
      "SESSION: %s %s\n"
      "  ADX min:    %.1f\n"
      "  Vol thresh: %.1fx\n"
      "  ATR mult:   %.2fx (%.0f pip grid)\n"
      "  Max pos:    %d\n"
      "-------------------------\n"
      "BASKET: %s\n"
      "  Entry: %s | Target: $%.2f\n"
      "  HWM:   $%.2f\n"
      "  BUY:  %d pos | %.2f lots\n"
      "  SELL: %d pos | %.2f lots\n"
      "  P&L:  $%.2f\n"
      "-------------------------\n"
      "Sweep: %s | BOS: %s\n"
      "News:     %s\n"
      "Cooling:  %s\n"
      "Recovery: %s\n"
      "W-Hedge:  %s",
      balance, equity, EquityPeak, peakDrop,
      drawdown, InpMaxDrawdownPct, dailyStat,
      SessionToString(CurrentSession),
      ActiveParams.enabled ? "ON" : "DISABLED",
      ActiveParams.adxMin, ActiveParams.volThreshold,
      ActiveParams.atrMult, gridPips, ActiveParams.maxPositions,
      Basket.active ? "ACTIVE" : "IDLE",
      Basket.isSweepEntry ? "Sweep" : "Vol+ADX", Basket.targetUSD,
      Basket.highWaterProfit,
      Basket.buyCount,  Basket.buyLots,
      Basket.sellCount, Basket.sellLots,
      Basket.totalProfit,
      sweepStat, bosStat,
      newsStat, coolStat, recovStat,
      WeekendHedgeOpen ? "OPEN" : "OFF"
   );

   Comment(panel);
}

//+------------------------------------------------------------------+
//| AI PYTHON BRIDGE & TELEMETRY FUNCTIONS                           |
//+------------------------------------------------------------------+
string ExtractJsonValue(string json, string key)
{
   string needle = "\"" + key + "\":";
   int pos = StringFind(json, needle);
   if(pos < 0) return "";
   pos += StringLen(needle);
   while(pos < StringLen(json) && (StringGetCharacter(json, pos) == ' ' || StringGetCharacter(json, pos) == '\"')) pos++;
   int end = pos;
   while(end < StringLen(json) && StringGetCharacter(json, end) != '\"' && StringGetCharacter(json, end) != ',' && StringGetCharacter(json, end) != '}') end++;
   return StringSubstr(json, pos, end - pos);
}

void WriteMT5StateJSON()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double profit  = AccountInfoDouble(ACCOUNT_PROFIT);
   double bid     = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double ask     = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   long   spread  = SymbolInfoInteger(Symbol(), SYMBOL_SPREAD);

   int posCount = PositionsTotal();
   string posJson = "[";
   int added = 0;
   for(int i = 0; i < posCount; i++) {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0) {
         if(added > 0) posJson += ",";
         string sym = PositionGetString(POSITION_SYMBOL);
         long type  = PositionGetInteger(POSITION_TYPE);
         double vol = PositionGetDouble(POSITION_VOLUME);
         double op  = PositionGetDouble(POSITION_PRICE_OPEN);
         double cp  = PositionGetDouble(POSITION_PRICE_CURRENT);
         double sl  = PositionGetDouble(POSITION_SL);
         double tp  = PositionGetDouble(POSITION_TP);
         double p   = PositionGetDouble(POSITION_PROFIT);
         string dir = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         posJson += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"direction\":\"%s\",\"volume\":%.2f,\"open_price\":%.2f,\"current_price\":%.2f,\"sl\":%.2f,\"tp\":%.2f,\"profit\":%.2f}",
                                 ticket, sym, dir, vol, op, cp, sl, tp, p);
         added++;
      }
   }
   posJson += "]";

   string stateJson = StringFormat(
      "{\"timestamp\":%I64d,\"account_id\":%I64u,\"server\":\"%s\",\"balance\":%.2f,\"equity\":%.2f,\"free_margin\":%.2f,\"profit\":%.2f,\"symbol\":\"%s\",\"bid\":%.2f,\"ask\":%.2f,\"spread\":%I64d,\"basket_active\":%s,\"buy_count\":%d,\"sell_count\":%d,\"positions\":%s}",
      TimeCurrent(), (ulong)AccountInfoInteger(ACCOUNT_LOGIN), AccountInfoString(ACCOUNT_SERVER),
      balance, equity, margin, profit, Symbol(), bid, ask, spread,
      Basket.active ? "true" : "false", Basket.buyCount, Basket.sellCount, posJson
   );

   int h = FileOpen(InpBridgeStateFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE) {
      FileWriteString(h, stateJson);
      FileClose(h);
   }
}

void CheckAIBridgeCommands()
{
   if(!InpUseAIBridge) return;

   // 1. Output current MT5 status to mt5_state.json every 1 sec
   static datetime lastStateWrite = 0;
   if(TimeCurrent() - lastStateWrite >= 1) {
      lastStateWrite = TimeCurrent();
      WriteMT5StateJSON();
   }

   // 2. Check for inbound command from Python AI
   if(!FileIsExist(InpBridgeCommandFile)) return;

   int h = FileOpen(InpBridgeCommandFile, FILE_READ|FILE_TXT|FILE_ANSI);
   if(h == INVALID_HANDLE) return;

   string json = "";
   while(!FileIsEnding(h)) {
      json += FileReadString(h);
   }
   FileClose(h);

   if(StringLen(json) == 0) return;

   string action = ExtractJsonValue(json, "action");
   FileDelete(InpBridgeCommandFile);

   string targetSymbol = ExtractJsonValue(json, "symbol");
   if(targetSymbol != "" && StringFind(Symbol(), targetSymbol) < 0 && StringFind(targetSymbol, Symbol()) < 0) return;

   if(action == "BUY") {
      double lot = StringToDouble(ExtractJsonValue(json, "volume"));
      if(lot <= 0) lot = InpBaseLot;
      Print("[AI_BRIDGE] Executing BUY signal from Python AI Ensemble on ", Symbol(), " | Lot: ", lot);
      OpenBasketTrade(ORDER_TYPE_BUY, lot, "AI_BUY", false, InpBasketProfitUSD);
   }
   else if(action == "SELL") {
      double lot = StringToDouble(ExtractJsonValue(json, "volume"));
      if(lot <= 0) lot = InpBaseLot;
      Print("[AI_BRIDGE] Executing SELL signal from Python AI Ensemble on ", Symbol(), " | Lot: ", lot);
      OpenBasketTrade(ORDER_TYPE_SELL, lot, "AI_SELL", false, InpBasketProfitUSD);
   }
   else if(action == "CLOSE_ALL" || action == "FLATTEN") {
      Print("[AI_BRIDGE] Executing CLOSE_ALL from Python AI on ", Symbol());
      CloseAllBasketPositions("AI_BRIDGE_FLATTEN");
      ResetBasket();
   }
}

//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest     &request,
                        const MqlTradeResult      &result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD) {
      if(trans.deal_type == DEAL_TYPE_BUY || trans.deal_type == DEAL_TYPE_SELL) {
         UpdateBasketProfit();
         if(!Basket.active) ResetBasket();
      }
   }
}
//+------------------------------------------------------------------+
