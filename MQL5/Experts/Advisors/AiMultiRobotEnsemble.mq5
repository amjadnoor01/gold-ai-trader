//+------------------------------------------------------------------+
//|                                        AiMultiRobotEnsemble.mq5 |
//|        Multi-Agent Multi-Robot Strategy Ensemble EA             |
//|        Fuses AI Brain, DOM Order Book, MACD, PSAR & RSI         |
//+------------------------------------------------------------------+
#property copyright "Antigravity AI Quantitative Systems"
#property link      "https://antigravity.google"
#property version   "3.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+
input group "=== Robot Ensemble Weights ==="
input double InpWeightAiBrain      = 0.35;   // AI Brain SGD Model (35%)
input double InpWeightDOM          = 0.20;   // Level-II DOM Volume Imbalance (20%)
input double InpWeightMACD         = 0.15;   // MACD Momentum (15%)
input double InpWeightPSAR         = 0.15;   // Parabolic SAR Breakout (15%)
input double InpWeightRSI          = 0.15;   // RSI Overbought/Oversold (15%)

input group "=== Aggressive Cluster Execution ==="
input ulong  InpBaseMagicNumber    = 999100;
input double InpTranche1Lots       = 0.02;   // Alpha Scalp
input int    InpTranche1TP_Pips    = 18;
input bool   InpTranche1Trail      = true;

input double InpTranche2Lots       = 0.02;   // Core Trend
input int    InpTranche2TP_Pips    = 32;

input double InpTranche3Lots       = 0.01;   // Impulse Runner
input int    InpTranche3TP_Pips    = 55;

input double InpEnsembleThreshold  = 35.0;   // Trigger Threshold (+-35%)
input int    InpPollIntervalSec    = 2;

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                 |
//+------------------------------------------------------------------+
CTrade        m_trade;
CPositionInfo m_position;

datetime m_last_poll_time = 0;
double   m_score_ai = 0.0;
double   m_score_dom = 0.0;
double   m_score_macd = 0.0;
double   m_score_psar = 0.0;
double   m_score_rsi = 0.0;
double   m_total_ensemble_score = 0.0;

int      m_ai_direction = 0;
double   m_ai_confidence = 0.0;
string   m_active_cluster_id = "NONE";

// Indicator Handles
int      m_h_rsi  = INVALID_HANDLE;
int      m_h_macd = INVALID_HANDLE;
int      m_h_psar = INVALID_HANDLE;
int      m_h_ema  = INVALID_HANDLE;
int      m_h_atr  = INVALID_HANDLE;
int      m_h_adx  = INVALID_HANDLE;

// Ticket Deduplication Tracking Array
ulong    m_processed_tickets[500];
int      m_processed_count = 0;

bool IsDealProcessed(ulong ticket)
{
   for(int i = 0; i < m_processed_count; i++)
   {
      if(m_processed_tickets[i] == ticket)
         return true;
   }
   return false;
}

void MarkDealProcessed(ulong ticket)
{
   if(m_processed_count >= 500)
   {
      for(int i = 0; i < 499; i++)
         m_processed_tickets[i] = m_processed_tickets[i+1];
      m_processed_count = 499;
   }
   m_processed_tickets[m_processed_count++] = ticket;
}

//+------------------------------------------------------------------+
//| Helper Encoding Utilities                                       |
//+------------------------------------------------------------------+
string ReadFileUTF(string filename)
{
   int handle = FileOpen(filename, FILE_READ|FILE_BIN);
   if(handle == INVALID_HANDLE) return "";
   
   ulong size = FileSize(handle);
   if(size <= 0) { FileClose(handle); return ""; }
   
   uchar buffer[];
   ArrayResize(buffer, (int)size);
   FileReadArray(handle, buffer, 0, (int)size);
   FileClose(handle);
   
   if(size >= 2 && buffer[0] == 0xFF && buffer[1] == 0xFE)
      return ShortArrayToString(buffer, 2, (int)(size-2)/2);
      
   return CharArrayToString(buffer, 0, (int)size);
}

void WriteFileUTF(string filename, string content)
{
   int handle = FileOpen(filename, FILE_WRITE|FILE_BIN);
   if(handle != INVALID_HANDLE)
   {
      uchar bom[2] = {0xFF, 0xFE};
      FileWriteArray(handle, bom, 0, 2);
      ushort text_shorts[];
      StringToShortArray(content, text_shorts);
      int count = ArraySize(text_shorts) - 1;
      if(count > 0)
         FileWriteArray(handle, text_shorts, 0, count);
      FileClose(handle);
   }
}

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   EventSetTimer(InpPollIntervalSec);
   m_trade.SetExpertMagicNumber(InpBaseMagicNumber);

   // Subscribe to Level-II Order Book (DOM)
   MarketBookAdd(_Symbol);

   // Initialize Technical Indicators
   m_h_rsi  = iRSI(_Symbol, _Period, 14, PRICE_CLOSE);
   m_h_macd = iMACD(_Symbol, _Period, 12, 26, 9, PRICE_CLOSE);
   m_h_psar = iSAR(_Symbol, _Period, 0.02, 0.2);
   m_h_ema  = iMA(_Symbol, _Period, 50, 0, MODE_EMA, PRICE_CLOSE);
   m_h_atr  = iATR(_Symbol, _Period, 14);
   m_h_adx  = iADX(_Symbol, _Period, 14);

   if(m_h_rsi == INVALID_HANDLE || m_h_macd == INVALID_HANDLE || m_h_psar == INVALID_HANDLE)
   {
      Print("Error creating indicator handles in AiMultiRobotEnsemble");
      return INIT_FAILED;
   }

   Print("AiMultiRobotEnsemble v3.0 initialized successfully for ", _Symbol);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   MarketBookRelease(_Symbol);
   
   if(m_h_rsi  != INVALID_HANDLE) IndicatorRelease(m_h_rsi);
   if(m_h_macd != INVALID_HANDLE) IndicatorRelease(m_h_macd);
   if(m_h_psar != INVALID_HANDLE) IndicatorRelease(m_h_psar);
   if(m_h_ema  != INVALID_HANDLE) IndicatorRelease(m_h_ema);
   if(m_h_atr  != INVALID_HANDLE) IndicatorRelease(m_h_atr);
   if(m_h_adx  != INVALID_HANDLE) IndicatorRelease(m_h_adx);

   ObjectsDeleteAll(0, "ENSEMBLE_");
}

//+------------------------------------------------------------------+
//| Robot 1: AI Brain SGD Signal Reader                              |
//+------------------------------------------------------------------+
double CalculateRobotScore_AIBrain()
{
   string filename = "ai_signal_" + _Symbol + ".json";
   string text = ReadFileUTF(filename);
   if(text == "") text = ReadFileUTF("ai_signal.json");
   if(text == "") return 0.0;

   int str_pos = StringFind(text, "\"strength_score\":");
   int dir_pos = StringFind(text, "\"direction\":");
   
   if(str_pos >= 0 && dir_pos >= 0)
   {
      string str_sub = StringSubstr(text, str_pos + 17);
      int comma = StringFind(str_sub, ",");
      if(comma > 0) str_sub = StringSubstr(str_sub, 0, comma);
      m_score_ai = StringToDouble(str_sub);

      string dir_sub = StringSubstr(text, dir_pos + 12);
      int dir_comma = StringFind(dir_sub, ",");
      if(dir_comma > 0) dir_sub = StringSubstr(dir_sub, 0, dir_comma);
      m_ai_direction = (int)StringToInteger(dir_sub);
      
      return m_score_ai;
   }
   return 0.0;
}

//+------------------------------------------------------------------+
//| Robot 2: Level-II DOM Order Book Imbalance                      |
//+------------------------------------------------------------------+
double CalculateRobotScore_DOM()
{
   MqlBookInfo book[];
   if(MarketBookGet(_Symbol, book) && ArraySize(book) > 0)
   {
      double bid_vol = 0.0, ask_vol = 0.0;
      int size = ArraySize(book);
      for(int i = 0; i < size; i++)
      {
         if(book[i].type == BOOK_TYPE_BID || book[i].type == BOOK_TYPE_BUY)
            bid_vol += (double)book[i].volume;
         else if(book[i].type == BOOK_TYPE_ASK || book[i].type == BOOK_TYPE_SELL)
            ask_vol += (double)book[i].volume;
      }
      double total_vol = bid_vol + ask_vol;
      if(total_vol > 0.0)
      {
         m_score_dom = ((bid_vol - ask_vol) / total_vol) * 100.0;
         return m_score_dom;
      }
   }
   m_score_dom = 0.0;
   return 0.0;
}

//+------------------------------------------------------------------+
//| Robot 3: MACD Momentum                                          |
//+------------------------------------------------------------------+
double CalculateRobotScore_MACD()
{
   double macd_main[2], macd_signal[2];
   if(CopyBuffer(m_h_macd, MAIN_LINE, 0, 2, macd_main) <= 0 ||
      CopyBuffer(m_h_macd, SIGNAL_LINE, 0, 2, macd_signal) <= 0)
      return 0.0;

   double diff_curr = macd_main[0] - macd_signal[0];
   double diff_prev = macd_main[1] - macd_signal[1];

   if(diff_curr > 0 && diff_curr > diff_prev)
      m_score_macd = 100.0;   // Strong Bullish Momentum
   else if(diff_curr > 0)
      m_score_macd = 50.0;    // Moderate Bullish Momentum
   else if(diff_curr < 0 && diff_curr < diff_prev)
      m_score_macd = -100.0;  // Strong Bearish Momentum
   else if(diff_curr < 0)
      m_score_macd = -50.0;   // Moderate Bearish Momentum
   else
      m_score_macd = 0.0;

   return m_score_macd;
}

//+------------------------------------------------------------------+
//| Robot 4: Parabolic SAR Breakout                                  |
//+------------------------------------------------------------------+
double CalculateRobotScore_PSAR()
{
   double psar[1], close[1];
   if(CopyBuffer(m_h_psar, 0, 0, 1, psar) <= 0 ||
      CopyClose(_Symbol, _Period, 0, 1, close) <= 0)
      return 0.0;

   if(close[0] > psar[0])
      m_score_psar = 100.0;   // Uptrend
   else
      m_score_psar = -100.0;  // Downtrend

   return m_score_psar;
}

//+------------------------------------------------------------------+
//| Robot 5: RSI Overbought/Oversold Divergence                     |
//+------------------------------------------------------------------+
double CalculateRobotScore_RSI()
{
   double rsi[1];
   if(CopyBuffer(m_h_rsi, 0, 0, 1, rsi) <= 0)
      return 0.0;

   if(rsi[0] < 30.0)
      m_score_rsi = 100.0;    // Oversold -> Strong Buy
   else if(rsi[0] < 45.0)
      m_score_rsi = 40.0;
   else if(rsi[0] > 70.0)
      m_score_rsi = -100.0;   // Overbought -> Strong Sell
   else if(rsi[0] > 55.0)
      m_score_rsi = -40.0;
   else
      m_score_rsi = 0.0;

   return m_score_rsi;
}

//+------------------------------------------------------------------+
//| Export Feature State & Check External Command Triggers           |
//+------------------------------------------------------------------+
void ExportLiveFeatures()
{
   double rsi[1], macd_main[1], macd_signal[1], ema[2], atr[1], adx[1];

   if(CopyBuffer(m_h_rsi, 0, 0, 1, rsi) <= 0) return;
   if(CopyBuffer(m_h_macd, MAIN_LINE, 0, 1, macd_main) <= 0) return;
   if(CopyBuffer(m_h_macd, SIGNAL_LINE, 0, 1, macd_signal) <= 0) return;
   if(CopyBuffer(m_h_ema, 0, 0, 2, ema) <= 0) return;
   if(CopyBuffer(m_h_atr, 0, 0, 1, atr) <= 0) return;
   if(CopyBuffer(m_h_adx, 0, 0, 1, adx) <= 0) return;

   double macd_diff = macd_main[0] - macd_signal[0];
   double ema_slope = ema[0] - ema[1];
   double volatility = (atr[0] > 0) ? (atr[0] / SymbolInfoDouble(_Symbol, SYMBOL_BID)) : 0.002;
   double adx_val = adx[0];

   string json = StringFormat(
      "{\"symbol\":\"%s\",\"rsi\":%.2f,\"macd_diff\":%.6f,\"ema_slope\":%.6f,\"volatility\":%.6f,\"adx\":%.2f,\"ensemble_score\":%.2f}",
      _Symbol, rsi[0], macd_diff, ema_slope, volatility, adx_val, m_total_ensemble_score
   );

   WriteFileUTF("live_features_" + _Symbol + ".json", json);
   if(_Symbol == "XAUUSD")
      WriteFileUTF("live_features.json", json);
}

//+------------------------------------------------------------------+
//| Export Account & Position State                                  |
//+------------------------------------------------------------------+
void ExportAccountState()
{
   double balance     = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity      = AccountInfoDouble(ACCOUNT_EQUITY);
   double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   double margin      = AccountInfoDouble(ACCOUNT_MARGIN);
   double margin_lvl  = (margin > 0) ? (equity / margin * 100.0) : 10000.0;

   string pos_json = "[";
   int total = PositionsTotal();
   int count = 0;

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0 && PositionGetString(POSITION_SYMBOL) == _Symbol)
      {
         if(count > 0) pos_json += ",";
         pos_json += StringFormat(
            "{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"volume\":%.2f,\"price_open\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f}",
            ticket,
            PositionGetString(POSITION_SYMBOL),
            (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "BUY" : "SELL",
            PositionGetDouble(POSITION_VOLUME),
            PositionGetDouble(POSITION_PRICE_OPEN),
            PositionGetDouble(POSITION_SL),
            PositionGetDouble(POSITION_TP),
            PositionGetDouble(POSITION_PROFIT)
         );
         count++;
      }
   }
   pos_json += "]";

   string state_json = StringFormat(
      "{\"balance\":%.2f,\"equity\":%.2f,\"free_margin\":%.2f,\"margin_level\":%.2f,\"position_count\":%d,\"positions\":%s}",
      balance, equity, free_margin, margin_lvl, count, pos_json
   );

   WriteFileUTF("mt5_state_" + _Symbol + ".json", state_json);
   if(_Symbol == "XAUUSD")
      WriteFileUTF("mt5_state.json", state_json);
}

//+------------------------------------------------------------------+
//| Deduplicated History Deal Feedback Exporter                      |
//+------------------------------------------------------------------+
void ExportTradeFeedback()
{
   if(!HistorySelect(TimeCurrent() - 86400, TimeCurrent())) return;

   int deals = HistoryDealsTotal();
   for(int i = 0; i < deals; i++)
   {
      ulong deal_ticket = HistoryDealGetTicket(i);
      if(deal_ticket <= 0) continue;

      long deal_entry = HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
      if(deal_entry != DEAL_ENTRY_OUT) continue;

      if(IsDealProcessed(deal_ticket)) continue;

      string deal_symbol = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
      if(deal_symbol != _Symbol) continue;

      double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
      double swap   = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
      double comm   = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
      double net_pnl = profit + swap + comm;

      long type = HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
      int direction = (type == DEAL_TYPE_SELL) ? 1 : -1;

      string feedback_json = StringFormat(
         "{\"trade_id\":\"MT5_%I64u\",\"symbol\":\"%s\",\"direction\":%d,\"realized_pnl\":%.2f,\"rsi\":50.0,\"macd_diff\":0.0,\"ema_slope\":0.0,\"volatility\":0.002,\"adx\":25.0}",
         deal_ticket, deal_symbol, direction, net_pnl
      );

      WriteFileUTF("ai_feedback_" + _Symbol + ".json", feedback_json);
      WriteFileUTF("ai_feedback.json", feedback_json);
      MarkDealProcessed(deal_ticket);
      Print("Exported trade feedback for ticket MT5_", deal_ticket, " | PnL=", net_pnl);
      break;
   }
}

//+------------------------------------------------------------------+
//| Execute 3-Tranche Targeted Cluster                              |
//+------------------------------------------------------------------+
void ExecuteCluster(string direction_str, string cluster_id)
{
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double ask   = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid   = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   ENUM_ORDER_TYPE order_type = (direction_str == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double price               = (direction_str == "BUY") ? ask : bid;

   // Tranche 1: Alpha Scalp
   double tp1 = 0.0;
   if(InpTranche1TP_Pips > 0)
      tp1 = (direction_str == "BUY") ? price + (InpTranche1TP_Pips * 10 * point) : price - (InpTranche1TP_Pips * 10 * point);

   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 1);
   if(m_trade.PositionOpen(_Symbol, order_type, InpTranche1Lots, price, 0, NormalizeDouble(tp1, digits), "T1 Scalp " + cluster_id))
      Print("Tranche 1 executed | ", direction_str, " | Lot=", InpTranche1Lots);

   // Tranche 2: Core Trend
   double tp2 = 0.0;
   if(InpTranche2TP_Pips > 0)
      tp2 = (direction_str == "BUY") ? price + (InpTranche2TP_Pips * 10 * point) : price - (InpTranche2TP_Pips * 10 * point);

   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 2);
   if(m_trade.PositionOpen(_Symbol, order_type, InpTranche2Lots, price, 0, NormalizeDouble(tp2, digits), "T2 Trend " + cluster_id))
      Print("Tranche 2 executed | ", direction_str, " | Lot=", InpTranche2Lots);

   // Tranche 3: Impulse Runner
   double tp3 = 0.0;
   if(InpTranche3TP_Pips > 0)
      tp3 = (direction_str == "BUY") ? price + (InpTranche3TP_Pips * 10 * point) : price - (InpTranche3TP_Pips * 10 * point);

   m_trade.SetExpertMagicNumber(InpBaseMagicNumber + 3);
   if(m_trade.PositionOpen(_Symbol, order_type, InpTranche3Lots, price, 0, NormalizeDouble(tp3, digits), "T3 Runner " + cluster_id))
      Print("Tranche 3 executed | ", direction_str, " | Lot=", InpTranche3Lots);

   m_active_cluster_id = cluster_id;
}

//+------------------------------------------------------------------+
//| Check IPC Commands from Autonomy Engine                         |
//+------------------------------------------------------------------+
void CheckIPCCommands()
{
   string command_file = "cluster_command_" + _Symbol + ".json";
   string text = ReadFileUTF(command_file);
   if(text == "") text = ReadFileUTF("cluster_command.json");
   if(text == "") return;

   int action_pos = StringFind(text, "\"action\":");
   int dir_pos    = StringFind(text, "\"direction\":");
   int id_pos     = StringFind(text, "\"cluster_id\":");

   if(action_pos >= 0 && dir_pos >= 0)
   {
      string dir_sub = StringSubstr(text, dir_pos + 13);
      int quote1 = StringFind(dir_sub, "\"");
      if(quote1 >= 0)
      {
         dir_sub = StringSubstr(dir_sub, quote1 + 1);
         int quote2 = StringFind(dir_sub, "\"");
         if(quote2 > 0) dir_sub = StringSubstr(dir_sub, 0, quote2);
      }

      string cid_sub = "CL-IPC";
      if(id_pos >= 0)
      {
         string id_tmp = StringSubstr(text, id_pos + 14);
         int q1 = StringFind(id_tmp, "\"");
         if(q1 >= 0)
         {
            id_tmp = StringSubstr(id_tmp, q1 + 1);
            int q2 = StringFind(id_tmp, "\"");
            if(q2 > 0) cid_sub = StringSubstr(id_tmp, 0, q2);
         }
      }

      if(cid_sub != m_active_cluster_id)
      {
         Print("🔥 [IPC COMMAND RECEIVED] Executing cluster: ", cid_sub, " | Direction=", dir_sub);
         ExecuteCluster(dir_sub, cid_sub);
      }
   }
}

//+------------------------------------------------------------------+
//| ATR Dynamic Trailing Breakeven Shield                           |
//+------------------------------------------------------------------+
void ManageTrailingBreakeven()
{
   double atr[1];
   if(CopyBuffer(m_h_atr, 0, 0, 1, atr) <= 0 || atr[0] <= 0) return;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits   = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   int total    = PositionsTotal();

   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;

      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      ulong magic = PositionGetInteger(POSITION_MAGIC);
      if(magic < InpBaseMagicNumber || magic > InpBaseMagicNumber + 10) continue;

      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      double current_sl = PositionGetDouble(POSITION_SL);
      ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

      double lock_dist = atr[0] * 1.0; // 1.0x ATR profit lock

      if(pos_type == POSITION_TYPE_BUY)
      {
         if((bid - open_price) >= lock_dist)
         {
            double new_sl = NormalizeDouble(open_price + (2 * 10 * point), digits);
            if(current_sl < new_sl)
            {
               m_trade.PositionModify(ticket, new_sl, PositionGetDouble(POSITION_TP));
               Print("🛡️ [ATR SHIELD] Locked Breakeven for BUY Ticket MT5_", ticket);
            }
         }
      }
      else if(pos_type == POSITION_TYPE_SELL)
      {
         if((open_price - ask) >= lock_dist)
         {
            double new_sl = NormalizeDouble(open_price - (2 * 10 * point), digits);
            if(current_sl == 0 || current_sl > new_sl)
            {
               m_trade.PositionModify(ticket, new_sl, PositionGetDouble(POSITION_TP));
               Print("🛡️ [ATR SHIELD] Locked Breakeven for SELL Ticket MT5_", ticket);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| On-Chart Interactive High-Tech HUD Overlay                       |
//+------------------------------------------------------------------+
void RenderOnChartHUD()
{
   string font_name = "Trebuchet MS";
   color header_color = clrGold;

   string lines[8];
   lines[0] = "🤖 MULTI-ROBOT ENSEMBLE SYSTEM v3.0 (" + _Symbol + ")";
   lines[1] = StringFormat("• Robot 1 (AI Brain):    %+.1f%%", m_score_ai);
   lines[2] = StringFormat("• Robot 2 (DOM Book):    %+.1f%%", m_score_dom);
   lines[3] = StringFormat("• Robot 3 (MACD Imp):    %+.1f%%", m_score_macd);
   lines[4] = StringFormat("• Robot 4 (PSAR Trail):  %+.1f%%", m_score_psar);
   lines[5] = StringFormat("• Robot 5 (RSI Extreme): %+.1f%%", m_score_rsi);
   lines[6] = StringFormat("⚡ ENSEMBLE SCORE:      %+.1f%%", m_total_ensemble_score);
   lines[7] = StringFormat("🛡️ Account Free Margin:  $%.2f", AccountInfoDouble(ACCOUNT_MARGIN_FREE));

   for(int i = 0; i < 8; i++)
   {
      string obj_name = "ENSEMBLE_HUD_L" + IntegerToString(i);
      if(ObjectFind(0, obj_name) < 0)
      {
         ObjectCreate(0, obj_name, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, obj_name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, obj_name, OBJPROP_XDISTANCE, 20);
         ObjectSetInteger(0, obj_name, OBJPROP_YDISTANCE, 25 + (i * 18));
         ObjectSetString(0, obj_name, OBJPROP_FONT, font_name);
         ObjectSetInteger(0, obj_name, OBJPROP_FONTSIZE, 9);
      }
      
      color text_col = (i == 0 || i == 6) ? header_color : clrWhite;
      if(i == 6)
      {
         if(m_total_ensemble_score >= InpEnsembleThreshold) text_col = clrLime;
         else if(m_total_ensemble_score <= -InpEnsembleThreshold) text_col = clrRed;
      }

      ObjectSetString(0, obj_name, OBJPROP_TEXT, lines[i]);
      ObjectSetInteger(0, obj_name, OBJPROP_COLOR, text_col);
   }
}

//+------------------------------------------------------------------+
//| Timer Event Handler                                              |
//+------------------------------------------------------------------+
void OnTimer()
{
   // Calculate individual Robot Scores
   CalculateRobotScore_AIBrain();
   CalculateRobotScore_DOM();
   CalculateRobotScore_MACD();
   CalculateRobotScore_PSAR();
   CalculateRobotScore_RSI();

   // Compute Weighted Ensemble Score
   m_total_ensemble_score = (m_score_ai   * InpWeightAiBrain) +
                            (m_score_dom  * InpWeightDOM)     +
                            (m_score_macd * InpWeightMACD)    +
                            (m_score_psar * InpWeightPSAR)    +
                            (m_score_rsi  * InpWeightRSI);

   // Export Features & Account State
   ExportLiveFeatures();
   ExportAccountState();
   ExportTradeFeedback();

   // Check IPC Commands & Manage Positions
   CheckIPCCommands();
   ManageTrailingBreakeven();

   // Render On-Chart Overlay
   RenderOnChartHUD();

   // Autonomous Direct Ensemble Execution if Threshold Breached
   if(MathAbs(m_total_ensemble_score) >= InpEnsembleThreshold)
   {
      datetime now = TimeCurrent();
      if(now - m_last_poll_time >= 30) // 30s per-symbol trigger cooldown
      {
         m_last_poll_time = now;
         string dir_str = (m_total_ensemble_score > 0) ? "BUY" : "SELL";
         string cluster_id = "CL-ENS-" + IntegerToString((int)now);
         Print("🔥 [ENSEMBLE CONSENSUS TRIGGER] Score=", m_total_ensemble_score, " | Executing ", dir_str);
         ExecuteCluster(dir_str, cluster_id);
      }
   }
}

//+------------------------------------------------------------------+
//| Tick Event Handler                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   ManageTrailingBreakeven();
}
