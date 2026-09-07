//+------------------------------------------------------------------+
//|                                                  AiBridgeEA.mq5 |
//|        High-Frequency Local AI Bridge & Aggressive Cluster EA    |
//|        Autonomous Feature Extractor & SGD Online Learning Loop   |
//+------------------------------------------------------------------+
#property copyright "Antigravity AI Quantitative Systems"
#property link      "https://antigravity.google"
#property version   "2.50"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== AI Microservice IPC ==="
input string InpServerUrl          = "http://127.0.0.1:8000";
input bool   InpUseHttpWebRequest  = false;
input int    InpPollIntervalSec    = 2;

input group "=== Aggressive Cluster Execution ==="
input ulong  InpBaseMagicNumber    = 888100;
input double InpTranche1Lots       = 0.02;   // Alpha Scalp
input int    InpTranche1TP_Pips    = 18;
input bool   InpTranche1Trail      = true;

input double InpTranche2Lots       = 0.02;   // Core Trend
input int    InpTranche2TP_Pips    = 32;

input double InpTranche3Lots       = 0.01;   // Impulse Runner
input int    InpTranche3TP_Pips    = 55;

input double InpTriggerThreshold   = 35.0;   // Institutional Strength >= 35%

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                 |
//+------------------------------------------------------------------+
CTrade        m_trade;
CPositionInfo m_position;

datetime m_last_poll_time = 0;
double   m_current_strength = 0.0;
int      m_current_direction = 0;   // 1=BUY, -1=SELL, 0=NEUTRAL
double   m_current_confidence = 0.0;
string   m_active_cluster_id = "NONE";
int      m_feedback_count = 0;
int      m_wins = 0;
int      m_losses = 0;
string   m_current_session = "LONDON/NY";

double   m_last_rsi = 50.0;
double   m_last_macd_diff = 0.0;
double   m_last_ema_slope = 0.0;
double   m_last_volatility = 0.002;
double   m_last_adx = 25.0;

// Technical Indicator Handles
int      m_h_rsi = INVALID_HANDLE;
int      m_h_macd = INVALID_HANDLE;
int      m_h_ema = INVALID_HANDLE;
int      m_h_atr = INVALID_HANDLE;
int      m_h_adx = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer(InpPollIntervalSec);
   m_trade.SetExpertMagicNumber(InpBaseMagicNumber);

   // Initialize Indicator Handles
   m_h_rsi  = iRSI(_Symbol, _Period, 14, PRICE_CLOSE);
   m_h_macd = iMACD(_Symbol, _Period, 12, 26, 9, PRICE_CLOSE);
   m_h_ema  = iMA(_Symbol, _Period, 20, 0, MODE_EMA, PRICE_CLOSE);
   m_h_atr  = iATR(_Symbol, _Period, 14);
   m_h_adx  = iADX(_Symbol, _Period, 14);

   CreateHUDCanvas();
   UpdateHUDDisplay();

   Print("[AiBridgeEA 2.60] Initialized with Multi-Symbol Regime Engine & Volatility Trailing Safeguards.");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   IndicatorRelease(m_h_rsi);
   IndicatorRelease(m_h_macd);
   IndicatorRelease(m_h_ema);
   IndicatorRelease(m_h_atr);
   IndicatorRelease(m_h_adx);
   DestroyHUDCanvas();
   Print("[AiBridgeEA] Deinitialized. Reason: ", reason);
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   ExtractAndExportFeatures();
   PollAISignal();
   CheckClusterCommands();
   CheckClosedDealsFeedback();
   ManageTrailingStops();
   UpdateHUDDisplay();
}

//+------------------------------------------------------------------+
//| Timer event function                                             |
//+------------------------------------------------------------------+
void OnTimer()
{
   ExtractAndExportFeatures();
   PollAISignal();
   CheckClusterCommands();
   CheckClosedDealsFeedback();
   UpdateHUDDisplay();
}

//+------------------------------------------------------------------+
//| Chart Event function (1-Click Hotkeys)                           |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_KEYDOWN)
   {
      // Key 'C' -> Dispatch Aggressive Cluster
      if(lparam == 67 || lparam == 99)
      {
         Print("[Hotkey] Key 'C' pressed — Dispatching Aggressive Cluster!");
         ExecuteTradeCluster(m_current_direction != 0 ? m_current_direction : 1, "MANUAL_HOTKEY_C");
      }
      // Key 'X' -> Emergency Close All Clusters
      else if(lparam == 88 || lparam == 120)
      {
         Print("[Hotkey] Key 'X' pressed — Emergency Close All Clusters!");
         EmergencyCloseAll();
      }
   }
}

//+------------------------------------------------------------------+
//| Extract Live Features & Export to IPC live_features_<SYMBOL>.json|
//+------------------------------------------------------------------+
void ExtractAndExportFeatures()
{
   double rsi_buf[1], macd_main[1], macd_sig[1], ema_buf[3], atr_buf[1];

   if(CopyBuffer(m_h_rsi, 0, 0, 1, rsi_buf) <= 0) return;
   if(CopyBuffer(m_h_macd, 0, 0, 1, macd_main) <= 0) return;
   if(CopyBuffer(m_h_macd, 1, 0, 1, macd_sig) <= 0) return;
   if(CopyBuffer(m_h_ema, 0, 0, 3, ema_buf) <= 0) return;
   if(CopyBuffer(m_h_atr, 0, 0, 1, atr_buf) <= 0) return;

   double rsi       = rsi_buf[0];
   double macd_diff = macd_main[0] - macd_sig[0];
   double ema_slope = (ema_buf[2] - ema_buf[0]) / (ema_buf[0] + 1e-9);
   double close_p   = SymbolInfoDouble(_Symbol, SYMBOL_LAST);
   double vol       = close_p > 0 ? (atr_buf[0] / close_p) : 0.002;

   m_last_rsi       = rsi;
   m_last_macd_diff = macd_diff;
   m_last_ema_slope = ema_slope;
   m_last_volatility = vol;

   string json = StringFormat("{\"symbol\":\"%s\",\"rsi\":%.2f,\"macd_diff\":%.4f,\"ema_slope\":%.4f,\"volatility\":%.4f}",
                              _Symbol, rsi, macd_diff, ema_slope, vol);

   // Export to symbol-specific file
   string file_sym = StringFormat("live_features_%s.json", _Symbol);
   int handle = FileOpen(file_sym, FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(file_sym, FILE_WRITE|FILE_TXT);

   if(handle != INVALID_HANDLE)
   {
      FileWriteString(handle, json);
      FileClose(handle);
   }

   // Also update main live_features.json
   int handle_main = FileOpen("live_features.json", FILE_WRITE|FILE_TXT|FILE_COMMON);
   if(handle_main == INVALID_HANDLE)
      handle_main = FileOpen("live_features.json", FILE_WRITE|FILE_TXT);
   if(handle_main != INVALID_HANDLE)
   {
      FileWriteString(handle_main, json);
      FileClose(handle_main);
   }
}

//+------------------------------------------------------------------+
//| Poll AI Signal File                                              |
//+------------------------------------------------------------------+
void PollAISignal()
{
   string file_sym = StringFormat("ai_signal_%s.json", _Symbol);
   int handle = FileOpen(file_sym, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(file_sym, FILE_READ|FILE_TXT);
   if(handle == INVALID_HANDLE)
   {
      handle = FileOpen("ai_signal.json", FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
      if(handle == INVALID_HANDLE)
         handle = FileOpen("ai_signal.json", FILE_READ|FILE_TXT);
   }

   if(handle != INVALID_HANDLE)
   {
      string json_text = "";
      while(!FileIsEnding(handle))
         json_text += FileReadString(handle);
      FileClose(handle);

      if(StringLen(json_text) > 10)
         ParseSignalJson(json_text);
   }

   // Update session text
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(dt.hour >= 8 && dt.hour < 16)
      m_current_session = "LONDON ACTIVE";
   else if(dt.hour >= 13 && dt.hour < 21)
      m_current_session = "NY ACTIVE";
   else if(dt.hour >= 0 && dt.hour < 8)
      m_current_session = "ASIAN ACTIVE";
   else
      m_current_session = "OVERLAP / QUIET";
}

//+------------------------------------------------------------------+
//| Check Cluster Command Trigger                                    |
//+------------------------------------------------------------------+
void CheckClusterCommands()
{
   string file_sym = StringFormat("cluster_command_%s.json", _Symbol);
   int handle = FileOpen(file_sym, FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
   if(handle == INVALID_HANDLE)
      handle = FileOpen(file_sym, FILE_READ|FILE_TXT);
   string target_file = file_sym;

   if(handle == INVALID_HANDLE)
   {
      handle = FileOpen("cluster_command.json", FILE_READ|FILE_TXT|FILE_COMMON|FILE_SHARE_READ);
      if(handle == INVALID_HANDLE)
         handle = FileOpen("cluster_command.json", FILE_READ|FILE_TXT);
      target_file = "cluster_command.json";
   }

   if(handle != INVALID_HANDLE)
   {
      string json_text = "";
      while(!FileIsEnding(handle))
         json_text += FileReadString(handle);
      FileClose(handle);

      FileDelete(target_file);

      if(StringLen(json_text) > 10)
      {
         // Verify target symbol if specified
         int sym_idx = StringFind(json_text, "\"symbol\":");
         if(sym_idx >= 0)
         {
            if(StringFind(json_text, _Symbol) < 0 && StringFind(json_text, "ALL") < 0)
               return; // Command meant for another symbol
         }

         Print(StringFormat("[Cluster Command] Found trigger for %s: %s", _Symbol, json_text));
         int dir = (StringFind(json_text, "BUY") >= 0) ? 1 : -1;
         ExecuteTradeCluster(dir, "AUTONOMOUS_TRIGGER");
      }
   }
}

//+------------------------------------------------------------------+
//| Check Closed Deals & Send SGD Feedback Payload                   |
//+------------------------------------------------------------------+
void CheckClosedDealsFeedback()
{
   datetime from = TimeCurrent() - 60; // Check last 60 seconds
   if(HistorySelect(from, TimeCurrent()))
   {
      int deals = HistoryDealsTotal();
      for(int i = deals - 1; i >= 0; i--)
      {
         ulong deal_ticket = HistoryDealGetTicket(i);
         ulong magic       = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
         double profit     = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
         long entry_type   = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);

         // If closed deal from cluster EAs
         if(entry_type == DEAL_ENTRY_OUT && magic >= InpBaseMagicNumber && magic <= InpBaseMagicNumber + 10)
         {
            m_feedback_count++;
            if(profit > 0) m_wins++; else m_losses++;

            string fb_json = StringFormat("{\"trade_id\":\"MT5_%d\",\"direction\":1,\"realized_pnl\":%.2f,\"rsi\":%.2f,\"macd_diff\":%.4f,\"ema_slope\":%.4f,\"volatility\":%.4f}",
                                          deal_ticket, profit, m_last_rsi, m_last_macd_diff, m_last_ema_slope, m_last_volatility);

            int handle = FileOpen("ai_feedback.json", FILE_WRITE|FILE_TXT|FILE_COMMON);
            if(handle == INVALID_HANDLE)
               handle = FileOpen("ai_feedback.json", FILE_WRITE|FILE_TXT);

            if(handle != INVALID_HANDLE)
            {
               FileWriteString(handle, fb_json);
               FileClose(handle);
               Print(StringFormat("[Feedback Export] Closed deal #%d exported | Profit=$%.2f | Total Feedback=%d",
                     deal_ticket, profit, m_feedback_count));
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Parse Signal JSON                                                |
//+------------------------------------------------------------------+
void ParseSignalJson(string json)
{
   int score_idx = StringFind(json, "\"strength_score\":");
   if(score_idx >= 0)
   {
      string sub = StringSubstr(json, score_idx + 17, 10);
      m_current_strength = StringToDouble(sub);
   }

   int dir_idx = StringFind(json, "\"direction\":");
   if(dir_idx >= 0)
   {
      string sub = StringSubstr(json, dir_idx + 12, 5);
      m_current_direction = (int)StringToInteger(sub);
   }

   int conf_idx = StringFind(json, "\"confidence\":");
   if(conf_idx >= 0)
   {
      string sub = StringSubstr(json, conf_idx + 13, 6);
      m_current_confidence = StringToDouble(sub);
   }
}

//+------------------------------------------------------------------+
//| Margin & Risk Management Safeguard Check                         |
//+------------------------------------------------------------------+
bool IsMarginSafe()
{
   double free_margin  = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double balance      = AccountInfoDouble(ACCOUNT_BALANCE);
   double margin_level = AccountInfoDouble(ACCOUNT_MARGIN_LEVEL);

   // 1. Min Free Margin Guard ($500 minimum or >= 15% of account balance)
   if(free_margin < 500.0 || (balance > 0 && (free_margin / balance) < 0.15))
   {
      Print(StringFormat("[Margin Safeguard] Trade rejected! Low Free Margin: $%.2f (Min required: 15%% of $%.2f)", free_margin, balance));
      return false;
   }

   // 2. Margin Level Guard (Min 200% margin level if margin in use > 0)
   if(margin_level > 0 && margin_level < 200.0)
   {
      Print(StringFormat("[Margin Safeguard] Trade rejected! Low Margin Level: %.1f%% (Min required: 200%%)", margin_level));
      return false;
   }

   // 3. Max Active Cluster Position Cap (Max 9 cluster positions across account)
   if(GetActiveClusterPositionCount() >= 9)
   {
      Print("[Margin Safeguard] Trade rejected! Max active cluster position cap (9) reached.");
      return false;
   }

   return true;
}

//+------------------------------------------------------------------+
//| Render Visual On-Chart Trade Setup Boxes & Entry Markers         |
//+------------------------------------------------------------------+
void DrawTradeSetupVisuals(int direction, double entry_p, double tp_p, double sl_p)
{
   string box_name   = StringFormat("SETUP_BOX_%s_%d", _Symbol, (int)TimeCurrent());
   string arrow_name = StringFormat("SETUP_ARROW_%s_%d", _Symbol, (int)TimeCurrent());

   datetime t1 = TimeCurrent();
   datetime t2 = t1 + (PeriodSeconds() * 25);

   // 1. Render Target Risk/Reward Box
   ObjectCreate(0, box_name, OBJ_RECTANGLE, 0, t1, entry_p, t2, tp_p);
   color bg_clr = (direction == 1) ? C'34,197,94' : C'239,68,68';
   ObjectSetInteger(0, box_name, OBJPROP_COLOR, bg_clr);
   ObjectSetInteger(0, box_name, OBJPROP_BGCOLOR, bg_clr);
   ObjectSetInteger(0, box_name, OBJPROP_FILL, true);
   ObjectSetInteger(0, box_name, OBJPROP_BACK, true);

   // 2. Render Entry Signal Arrow
   int arrow_code = (direction == 1) ? 233 : 234;
   ObjectCreate(0, arrow_name, OBJ_ARROW, 0, t1, entry_p);
   ObjectSetInteger(0, arrow_name, OBJPROP_ARROWCODE, arrow_code);
   ObjectSetInteger(0, arrow_name, OBJPROP_COLOR, bg_clr);
   ObjectSetInteger(0, arrow_name, OBJPROP_WIDTH, 3);

   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Execute 3-Tranche Aggressive Trade Cluster                       |
//+------------------------------------------------------------------+
void ExecuteTradeCluster(int direction, string source_tag)
{
   if(direction == 0) direction = 1;

   // Enforce strict margin management guard
   if(!IsMarginSafe())
   {
      Print("[Cluster Execution] Aborted trade cluster due to Margin/Risk Safeguard.");
      return;
   }

   // Dynamic Risk-Scaled Lot Sizing
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double lot_scale = MathMax(0.5, MathMin(3.0, equity / 10000.0));
   double t1_lots   = NormalizeDouble(InpTranche1Lots * lot_scale, 2);
   double t2_lots   = NormalizeDouble(InpTranche2Lots * lot_scale, 2);
   double t3_lots   = NormalizeDouble(InpTranche3Lots * lot_scale, 2);

   m_active_cluster_id = StringFormat("CL-%d", TimeCurrent() % 10000);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   Print(StringFormat("[Cluster Execution] Initiating 3-Tranche Cluster %s (%s) on %s | Dir=%s | Strength=%.1f | Lots=%.2f/%.2f/%.2f",
         m_active_cluster_id, source_tag, _Symbol, (direction == 1 ? "BUY" : "SELL"), m_current_strength, t1_lots, t2_lots, t3_lots));

   double entry_p = (direction == 1) ? ask : bid;
   double tp_p    = (direction == 1) ? (ask + InpTranche2TP_Pips * 10 * point) : (bid - InpTranche2TP_Pips * 10 * point);
   double sl_p    = (direction == 1) ? (ask - InpTranche1TP_Pips * 10 * point) : (bid + InpTranche1TP_Pips * 10 * point);

   // Tranche 1: Alpha Scalp
   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 1);
   if(direction == 1)
      m_trade.Buy(t1_lots, _Symbol, ask, 0, ask + InpTranche1TP_Pips * 10 * point, m_active_cluster_id + "-T1");
   else
      m_trade.Sell(t1_lots, _Symbol, bid, 0, bid - InpTranche1TP_Pips * 10 * point, m_active_cluster_id + "-T1");

   // Tranche 2: Core Trend
   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 2);
   if(direction == 1)
      m_trade.Buy(t2_lots, _Symbol, ask, 0, ask + InpTranche2TP_Pips * 10 * point, m_active_cluster_id + "-T2");
   else
      m_trade.Sell(t2_lots, _Symbol, bid, 0, bid - InpTranche2TP_Pips * 10 * point, m_active_cluster_id + "-T2");

   // Tranche 3: Impulse Runner
   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 3);
   if(direction == 1)
      m_trade.Buy(t3_lots, _Symbol, ask, 0, ask + InpTranche3TP_Pips * 10 * point, m_active_cluster_id + "-T3");
   else
      m_trade.Sell(t3_lots, _Symbol, bid, 0, bid - InpTranche3TP_Pips * 10 * point, m_active_cluster_id + "-T3");

   // Draw visually informative trade setup graphics on chart
   DrawTradeSetupVisuals(direction, entry_p, tp_p, sl_p);
}

//+------------------------------------------------------------------+
//| Emergency Close All Active Cluster Positions                     |
//+------------------------------------------------------------------+
void EmergencyCloseAll()
{
   int closed_cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         ulong magic = m_position.Magic();
         if(magic >= InpBaseMagicNumber && magic <= InpBaseMagicNumber + 10)
         {
            m_trade.PositionClose(m_position.Ticket());
            closed_cnt++;
         }
      }
   }
   m_active_cluster_id = "FLATTENED";
   Print(StringFormat("[Emergency Close] Closed %d active cluster positions.", closed_cnt));
}

//+------------------------------------------------------------------+
//| Manage ATR Trailing Stop & Breakeven Safeguard                   |
//+------------------------------------------------------------------+
void ManageTrailingStops()
{
   double atr_buf[1];
   if(CopyBuffer(m_h_atr, 0, 0, 1, atr_buf) <= 0) return;
   double atr = atr_buf[0];
   if(atr <= 0) return;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   long spread  = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         if(m_position.Symbol() != _Symbol) continue;
         ulong magic = m_position.Magic();
         if(magic < InpBaseMagicNumber || magic > InpBaseMagicNumber + 10) continue;

         double open_price = m_position.PriceOpen();
         double current_sl = m_position.StopLoss();

         // 1. Breakeven Shield (+1.0x ATR profit reached)
         if(m_position.PositionType() == POSITION_TYPE_BUY)
         {
            if(bid - open_price >= (1.0 * atr))
            {
               double be_sl = NormalizeDouble(open_price + (spread * point), _Digits);
               if(be_sl > current_sl || current_sl == 0)
               {
                  m_trade.PositionModify(m_position.Ticket(), be_sl, m_position.TakeProfit());
                  Print(StringFormat("[Breakeven Shield] BUY position #%d locked at breakeven + spread", m_position.Ticket()));
                  continue;
               }
            }

            // 2. ATR Chandelier Trailing Stop (Tranche 1 & Tranche 3)
            if(magic == InpBaseMagicNumber + 1 || magic == InpBaseMagicNumber + 3)
            {
               double new_sl = NormalizeDouble(bid - (1.2 * atr), _Digits);
               if(bid - open_price > (1.2 * atr) && new_sl > current_sl)
                  m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
            }
         }
         else if(m_position.PositionType() == POSITION_TYPE_SELL)
         {
            if(open_price - ask >= (1.0 * atr))
            {
               double be_sl = NormalizeDouble(open_price - (spread * point), _Digits);
               if(be_sl < current_sl || current_sl == 0)
               {
                  m_trade.PositionModify(m_position.Ticket(), be_sl, m_position.TakeProfit());
                  Print(StringFormat("[Breakeven Shield] SELL position #%d locked at breakeven - spread", m_position.Ticket()));
                  continue;
               }
            }

            // 2. ATR Chandelier Trailing Stop (Tranche 1 & Tranche 3)
            if(magic == InpBaseMagicNumber + 1 || magic == InpBaseMagicNumber + 3)
            {
               double new_sl = NormalizeDouble(ask + (1.2 * atr), _Digits);
               if(open_price - ask > (1.2 * atr) && (new_sl < current_sl || current_sl == 0))
                  m_trade.PositionModify(m_position.Ticket(), new_sl, m_position.TakeProfit());
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Helpers & HUD Functions                                          |
//+------------------------------------------------------------------+
int GetActiveClusterPositionCount()
{
   int cnt = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         ulong magic = m_position.Magic();
         if(magic >= InpBaseMagicNumber && magic <= InpBaseMagicNumber + 10)
            cnt++;
      }
   }
   return cnt;
}

double GetFloatingClusterPnL()
{
   double pnl = 0.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(m_position.SelectByIndex(i))
      {
         ulong magic = m_position.Magic();
         if(magic >= InpBaseMagicNumber && magic <= InpBaseMagicNumber + 10)
            pnl += m_position.Profit() + m_position.Swap();
      }
   }
   return pnl;
}

void CreateHUDCanvas()
{
   DestroyHUDCanvas();

   ObjectCreate(0, "HUD_BG", OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_XDISTANCE, 15);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_YDISTANCE, 25);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_XSIZE, 325);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_YSIZE, 220);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_BGCOLOR, C'15,20,28');
   ObjectSetInteger(0, "HUD_BG", OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, "HUD_BG", OBJPROP_COLOR, C'38,47,61');

   CreateHUDLabel("HUD_TITLE", "⚡ ANTIGRAVITY CONTINUOUS AI BRAIN 2.6", 25, 33, C'245,176,39', 9, true);

   CreateHUDLabel("HUD_SESSION_L", "Market Session:", 25, 55, C'100,116,139', 9, false);
   CreateHUDLabel("HUD_SESSION_V", "LONDON / NY", 145, 55, C'248,250,252', 9, true);

   CreateHUDLabel("HUD_STRENGTH_L", "AI Strength Score:", 25, 75, C'100,116,139', 9, false);
   CreateHUDLabel("HUD_STRENGTH_V", "+0.0 (NEUTRAL)", 145, 75, C'245,176,39', 9, true);

   CreateHUDLabel("HUD_CLUSTER_L", "Active Cluster:", 25, 95, C'100,116,139', 9, false);
   CreateHUDLabel("HUD_CLUSTER_V", "NONE (0 positions)", 145, 95, C'248,250,252', 9, true);

   CreateHUDLabel("HUD_PNL_L", "Cluster Floating PnL:", 25, 115, C'100,116,139', 9, false);
   CreateHUDLabel("HUD_PNL_V", "$0.00", 145, 115, C'34,197,94', 9, true);

   CreateHUDLabel("HUD_SGD_L", "SGD Online Feedback:", 25, 135, C'100,116,139', 9, false);
   CreateHUDLabel("HUD_SGD_V", "0 updates (Warm-Start)", 145, 135, C'59,130,246', 9, true);

   CreateHUDLabel("HUD_KEY_C", "[C] 1-Click Cluster (3-Tranche)", 25, 160, C'34,197,94', 8, false);
   CreateHUDLabel("HUD_KEY_X", "[X] Emergency Close All", 25, 178, C'239,68,68', 8, false);

   ChartRedraw(0);
}

void CreateHUDLabel(string name, string text, int x, int y, color clr, int font_size, bool bold)
{
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetString(0, name, OBJPROP_FONT, bold ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
}

void UpdateHUDDisplay()
{
   ObjectSetString(0, "HUD_SESSION_V", OBJPROP_TEXT, m_current_session);

   string str_text = StringFormat("%+.1f (%s)", m_current_strength,
      m_current_strength >= 35.0 ? "STRONG BULL" : (m_current_strength <= -35.0 ? "STRONG BEAR" : "NEUTRAL"));
   color str_clr = m_current_strength >= 35.0 ? C'34,197,94' : (m_current_strength <= -35.0 ? C'239,68,68' : C'245,176,39');

   ObjectSetString(0, "HUD_STRENGTH_V", OBJPROP_TEXT, str_text);
   ObjectSetInteger(0, "HUD_STRENGTH_V", OBJPROP_COLOR, str_clr);

   int open_cnt = GetActiveClusterPositionCount();
   string cluster_text = StringFormat("%s (%d tranches)", m_active_cluster_id, open_cnt);
   ObjectSetString(0, "HUD_CLUSTER_V", OBJPROP_TEXT, cluster_text);

   double float_pnl = GetFloatingClusterPnL();
   string pnl_text = StringFormat("%s$%.2f", float_pnl >= 0 ? "+" : "", float_pnl);
   ObjectSetString(0, "HUD_PNL_V", OBJPROP_TEXT, pnl_text);
   ObjectSetInteger(0, "HUD_PNL_V", OBJPROP_COLOR, float_pnl >= 0 ? C'34,197,94' : C'239,68,68');

   int total_deals = m_wins + m_losses;
   double wr = total_deals > 0 ? ((double)m_wins / total_deals) * 100.0 : 0.0;
   string sgd_text = StringFormat("%d updates (WR: %.0f%%)", m_feedback_count, wr);
   ObjectSetString(0, "HUD_SGD_V", OBJPROP_TEXT, sgd_text);

   ChartRedraw(0);
}

void DestroyHUDCanvas()
{
   string labels[] = {"HUD_BG", "HUD_TITLE", "HUD_SESSION_L", "HUD_SESSION_V", "HUD_STRENGTH_L", "HUD_STRENGTH_V",
                      "HUD_CLUSTER_L", "HUD_CLUSTER_V", "HUD_PNL_L", "HUD_PNL_V", "HUD_SGD_L", "HUD_SGD_V", "HUD_KEY_C", "HUD_KEY_X"};
   for(int i = 0; i < ArraySize(labels); i++)
      ObjectDelete(0, labels[i]);
}
