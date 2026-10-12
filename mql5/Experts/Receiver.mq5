//+------------------------------------------------------------------+
//| Receiver                                                         |
//| Reports account, schedule, and position facts to the guard       |
//| server, then executes signals the server has already released.  |
//| Daily loss, profit, loss-count, spike-count, cooldown, and      |
//| break-even gates are server settings. This EA does not apply    |
//| them again.                                                      |
//+------------------------------------------------------------------+
#property strict
#include <Trade/Trade.mqh>
#include "../Include/ReceiverProtocol.mqh"
#include "../Include/ReceiverDecisions.mqh"

CTrade Trade;

enum ENUM_INSTR_TYPE {
   INSTR_TYPE_FOREX,
   INSTR_TYPE_INDEX,
   INSTR_TYPE_COMMODITY,
   INSTR_TYPE_OTHER
};

input string AUTH_SHARED = ""; // Must match AUTH_SHARED on the server. No default: set it in the EA inputs.
input string BaseURL = "http://127.0.0.1:5001"; // Same base URL as the sender, including the port
input int TimeoutMs = 8000;
input int PollMs = 300;
input int ReceiverStatePublishMs = 1000;
input long Magic = 20258008;
input int Slippage = 10;

input int SignalMaxAgeSeconds = 5;
input double SpikeThresholdPct = 1.0; // |closed net| >= this % of day-start balance counts as one spike

input bool EnforceSchedule = false; // false reports the schedule as open
input ENUM_DAY_OF_WEEK Slot1Day = MONDAY;
input string Slot1TimeHHMM = "16:30";
input string Slot1EndHHMM = "17:30";
input ENUM_DAY_OF_WEEK Slot2Day = WEDNESDAY;
input string Slot2TimeHHMM = "12:33";
input string Slot2EndHHMM = "13:33";

input bool AutoBE_3R_Enable = true;
input double AutoBE_Multiplier = 3.0;

input double RiskPerTradePct = 1.0;
input double TakeProfitReward = 4.0; // 4 closes at four times the stop distance
input double MaxLotsCap = 100.0;
input bool EnableMinStopDistance = true;
input double MinStopDistancePoints = 10.0;
// Fixed safety constants, not inputs, so this build adds no new EA settings.
const double MinStopSpreadMultiple = 3.0; // Silent stop is never closer than this many current spreads
const double AtrScaleMinRatio = 0.5; // ATR can shrink the stop to at most half the configured distance
const double AtrScaleMaxRatio = 2.0; // ATR can widen the stop to at most twice the configured distance
const double BackstopStopMultiple = 3.0; // Broker-side backstop sits this many silent-stop distances from entry
// Positions and orders without this Magic are always closed (no setting).

input bool EmergencyStop = false;
input bool EnableSelfHealing = true;
input bool EnableDetailedLogging = true;
input bool EnablePush = true;
input bool EnableMail = true;

input string MarketWatchSymbols = "EURUSD,GBPUSD,XAUUSD,US30,NAS100,DAX40";
input bool AutoAddSignalSymbols = true;

input group "Volatility scale"
input ENUM_TIMEFRAMES AtrTimeframe = PERIOD_M1; // ATR timeframe for 1-minute scalps
input int AtrPeriod = 14; // Live ATR bars
input int AtrBaselinePeriod = 1440; // One day of 1-minute bars

input group "Forex stop distance"
input double ForexSpreadPoints = 2.0;
input double ForexSpreadMultiplier = 25.0;

input group "Index stop distance"
input double FixedSLPoints_Indices = 100.0;

input group "Commodity stop distance"
input double FixedSLPoints_Commodities = 80.0;

input group "Gold stop distance"
input bool UseGoldSpecificSettings = true;
input double FixedSLPoints_Gold = 150.0;

input group "Other stop distance"
input double FixedSLPoints_Other = 60.0;

string URL_NEXT(){ return BaseURL + "/next"; }
string URL_ACK(){ return BaseURL + "/ack"; }
string URL_RECEIVER_STATE(){ return BaseURL + "/receiver-state"; }
string URL_TRADE_RISK(){ return BaseURL + "/trade-risk"; }

datetime g_dayAnchor = 0;
double g_equityAtDayStart = 0.0;
double g_dailyClosedNet = 0.0;
int g_lossesToday = 0;
int g_spikesToday = 0;
datetime g_lastLossClose = 0;
bool g_publishStateNow = false;
bool g_dayStartPending = false; // Day-start not derived yet: no state is published and no signal is taken
datetime g_dayStartPendingSince = 0;
string g_lastAckedSignalID = "";
ulong g_processedDeals[];
ulong g_recentClosedTickets[];
double g_effRiskPerTradePct = 1.0;

ulong gTickets[];
double gBoundarySL[];
long gTypeOfTicket[];
string gAtrSymbols[];
int gAtrHandles[];

datetime g_lastNoticeTime = 0;
string g_lastNoticeText = "";
int g_logFileHandle = INVALID_HANDLE;
string g_logFileDate = "";

bool IsFiniteNumber(const double value)
{
   if(value != value) return false;
   if(value > DBL_MAX || value < -DBL_MAX) return false;
   return true;
}

datetime DayAnchor(datetime when)
{
   MqlDateTime parts;
   TimeToStruct(when, parts);
   parts.hour = 0;
   parts.min = 0;
   parts.sec = 0;
   return StructToTime(parts);
}

bool ParseHHMM(const string hhmm, int &hour, int &minute)
{
   int separator = StringFind(hhmm, ":");
   if(separator < 0) return false;
   hour = (int)StringToInteger(StringSubstr(hhmm, 0, separator));
   minute = (int)StringToInteger(StringSubstr(hhmm, separator + 1));
   return hour >= 0 && hour <= 23 && minute >= 0 && minute <= 59;
}

void LogToFile(const string message)
{
   if(!EnableDetailedLogging) return;
   string today = TimeToString(TimeCurrent(), TIME_DATE);
   if(g_logFileHandle != INVALID_HANDLE && today != g_logFileDate)
   {
      FileClose(g_logFileHandle);
      g_logFileHandle = INVALID_HANDLE;
   }
   if(g_logFileHandle == INVALID_HANDLE)
   {
      g_logFileDate = today;
      string filename = "SignalReceiverEA_" + IntegerToString(Magic) + "_" + today + ".log";
      g_logFileHandle = FileOpen(filename, FILE_WRITE | FILE_READ | FILE_TXT | FILE_SHARE_READ);
   }
   if(g_logFileHandle == INVALID_HANDLE) return;
   FileSeek(g_logFileHandle, 0, SEEK_END);
   FileWrite(g_logFileHandle, TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), message);
   FileFlush(g_logFileHandle);
}

void SendNoticeOnce(const string text, const int minGapSeconds = 10)
{
   datetime now = TimeTradeServer();
   if(text == g_lastNoticeText && (now - g_lastNoticeTime) < minGapSeconds) return;
   g_lastNoticeText = text;
   g_lastNoticeTime = now;
   if(EnablePush) SendNotification(text);
   if(EnableMail && !SendMail("SignalReceiver", text))
      Print("[RX] SendMail failed");
}

double GetSymbolPoint(const string symbol)
{
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   return point > 0.0 ? point : 0.00001;
}

string ClassificationSymbol(const string symbol)
{
   string upper = symbol;
   StringToUpper(upper);
   int dot = StringFind(upper, ".");
   if(dot > 0) upper = StringSubstr(upper, 0, dot);
   return upper;
}

bool IsGoldSymbol(const string symbol)
{
   string upper = ClassificationSymbol(symbol);
   return StringFind(upper, "XAU") >= 0 || StringFind(upper, "GOLD") >= 0;
}

bool ListedCurrency(const string code)
{
   string currencies[8] = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"};
   for(int index = 0; index < 8; index++)
      if(code == currencies[index]) return true;
   return false;
}

ENUM_INSTR_TYPE GetInstrumentType(const string symbol)
{
   string upper = ClassificationSymbol(symbol);
   if(StringFind(upper, "GOLD") >= 0 || StringFind(upper, "XAU") >= 0 ||
      StringFind(upper, "SILVER") >= 0 || StringFind(upper, "XAG") >= 0 ||
      StringFind(upper, "OIL") >= 0 || StringFind(upper, "BRENT") >= 0 ||
      StringFind(upper, "COPPER") >= 0 || StringFind(upper, "PLATINUM") >= 0 ||
      StringFind(upper, "PALLADIUM") >= 0 || StringFind(upper, "NATURALGAS") >= 0 ||
      StringFind(upper, "NGAS") >= 0)
      return INSTR_TYPE_COMMODITY;

   if(StringFind(upper, "30") >= 0 || StringFind(upper, "40") >= 0 ||
      StringFind(upper, "100") >= 0 || StringFind(upper, "500") >= 0 ||
      StringFind(upper, "DAX") >= 0 || StringFind(upper, "DOW") >= 0 ||
      StringFind(upper, "NAS") >= 0 || StringFind(upper, "SP") >= 0 ||
      StringFind(upper, "FTSE") >= 0 || StringFind(upper, "NIKKEI") >= 0 ||
      StringFind(upper, "CAC") >= 0)
      return INSTR_TYPE_INDEX;

   if(StringLen(upper) == 6)
   {
      string base = StringSubstr(upper, 0, 3);
      string quote = StringSubstr(upper, 3, 3);
      string commodityBases[9] = {"XAU", "XAG", "OIL", "BRT", "COP", "PLT", "PAL", "NAT", "NG"};
      bool commodityBase = false;
      for(int index = 0; index < 9; index++)
         if(base == commodityBases[index]) commodityBase = true;
      if(!commodityBase && (ListedCurrency(base) || ListedCurrency(quote)))
         return INSTR_TYPE_FOREX;
   }

   return INSTR_TYPE_OTHER;
}

double GetSafeStopPoints(const string symbol)
{
   ENUM_INSTR_TYPE type = GetInstrumentType(symbol);
   bool isGold = IsGoldSymbol(symbol);
   double fallback = 60.0;
   if(type == INSTR_TYPE_FOREX) fallback = 50.0;
   else if(type == INSTR_TYPE_INDEX) fallback = 100.0;
   else if(type == INSTR_TYPE_COMMODITY) fallback = isGold ? 150.0 : 80.0;

   return ReceiverSelectStopPoints(
      type == INSTR_TYPE_FOREX,
      type == INSTR_TYPE_COMMODITY,
      isGold,
      type == INSTR_TYPE_INDEX,
      UseGoldSpecificSettings,
      ForexSpreadPoints,
      ForexSpreadMultiplier,
      FixedSLPoints_Gold,
      FixedSLPoints_Commodities,
      FixedSLPoints_Indices,
      FixedSLPoints_Other,
      fallback);
}

string AtrHandleKey(const string symbol, const int period)
{
   return symbol + "|" + IntegerToString(period);
}

int EnsureAtrHandle(const string symbol, const int period)
{
   if(period < 1 || symbol == "")
      return INVALID_HANDLE;
   string key = AtrHandleKey(symbol, period);
   for(int index = 0; index < ArraySize(gAtrSymbols); index++)
   {
      if(gAtrSymbols[index] == key)
         return gAtrHandles[index];
   }

   int handle = iATR(symbol, AtrTimeframe, period);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat("[RX] ATR handle failed for %s period %d (%d)", symbol, period, GetLastError());
      return INVALID_HANDLE;
   }

   int count = ArraySize(gAtrSymbols);
   ArrayResize(gAtrSymbols, count + 1);
   ArrayResize(gAtrHandles, count + 1);
   gAtrSymbols[count] = key;
   gAtrHandles[count] = handle;
   return handle;
}

void ReleaseAtrHandles()
{
   for(int index = 0; index < ArraySize(gAtrHandles); index++)
   {
      if(gAtrHandles[index] != INVALID_HANDLE)
         IndicatorRelease(gAtrHandles[index]);
   }
   ArrayResize(gAtrSymbols, 0);
   ArrayResize(gAtrHandles, 0);
}

double ReadAtr(const string symbol, const int period, const bool wait_for_value)
{
   int handle = EnsureAtrHandle(symbol, period);
   if(handle == INVALID_HANDLE)
      return 0.0;

   int attempts = wait_for_value ? 5 : 1;
   for(int attempt = 0; attempt < attempts; attempt++)
   {
      double values[];
      ArraySetAsSeries(values, true);
      if(CopyBuffer(handle, 0, 0, 1, values) == 1 && values[0] > 0.0)
         return values[0];
      if(wait_for_value && attempt + 1 < attempts)
         Sleep(40);
   }
   return 0.0;
}

double ResolveStopPoints(const string symbol, const bool wait_for_atr)
{
   double normal = GetSafeStopPoints(symbol);
   double current = ReadAtr(symbol, AtrPeriod, wait_for_atr);
   double baseline = ReadAtr(symbol, AtrBaselinePeriod, wait_for_atr);
   double minimumPoints = MinStopDistancePoints;
   double spreadPoints = (double)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
   if(MinStopSpreadMultiple > 0.0 && spreadPoints * MinStopSpreadMultiple > minimumPoints)
      minimumPoints = spreadPoints * MinStopSpreadMultiple;
   double stopsLevel = (double)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(stopsLevel > minimumPoints)
      minimumPoints = stopsLevel;
   double scaled = ReceiverApplyMinimumStopPoints(
      ReceiverScaleStopPoints(normal, current, baseline, AtrScaleMinRatio, AtrScaleMaxRatio),
      EnableMinStopDistance,
      minimumPoints);

   if(wait_for_atr)
   {
      if(current > 0.0 && baseline > 0.0)
         PrintFormat("[RX] Volatility scale %s: normal=%.1f ATR%d=%.5f ATR%d=%.5f scaled=%.1f",
                     symbol, normal, AtrPeriod, current, AtrBaselinePeriod, baseline, scaled);
      else
         PrintFormat("[RX] Volatility scale %s: ATR not ready, using configured %.1f points",
                     symbol, scaled);
   }
   return scaled;
}

void WarmAtrHandles()
{
   string symbols[];
   int count = StringSplit(MarketWatchSymbols, ',', symbols);
   for(int index = 0; index < count; index++)
   {
      string symbol = symbols[index];
      StringTrimLeft(symbol);
      StringTrimRight(symbol);
      if(symbol == "")
         continue;
      ReadAtr(symbol, AtrPeriod, false);
      ReadAtr(symbol, AtrBaselinePeriod, false);
   }
}

void InitializeMarketWatch()
{
   string symbols[];
   int count = StringSplit(MarketWatchSymbols, ',', symbols);
   int added = 0;
   for(int index = 0; index < count; index++)
   {
      string symbol = symbols[index];
      StringTrimLeft(symbol);
      StringTrimRight(symbol);
      if(symbol == "" || SymbolInfoInteger(symbol, SYMBOL_SELECT)) continue;
      if(SymbolSelect(symbol, true)) added++;
      else PrintFormat("[RX] Market Watch could not add %s", symbol);
   }
   if(added > 0)
      PrintFormat("[RX] Added %d symbols to Market Watch", added);
}

bool EnsureSymbolInMarketWatch(const string symbol)
{
   if(SymbolInfoInteger(symbol, SYMBOL_SELECT)) return true;
   if(!AutoAddSignalSymbols)
   {
      PrintFormat("[RX] %s is not in Market Watch", symbol);
      return false;
   }
   if(!SymbolSelect(symbol, true))
   {
      PrintFormat("[RX] Could not add %s to Market Watch", symbol);
      return false;
   }
   for(int attempt = 0; attempt < 10; attempt++)
   {
      MqlTick tick;
      if(SymbolInfoTick(symbol, tick) && tick.time > 0) return true;
      Sleep(50);
   }
   return true;
}

double CalculateValuePerPoint(const string symbol)
{
   EnsureSymbolInMarketWatch(symbol);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double fromTick = ReceiverValuePerPoint(tickValue, tickSize, point);
   if(fromTick > 0.0)
      return fromTick;

   double contractSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
   if(point <= 0.0 || contractSize <= 0.0)
      return 0.0;

   string profitCurrency = SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT);
   string accountCurrency = AccountInfoString(ACCOUNT_CURRENCY);
   double valueInProfitCurrency = contractSize * point;
   if(profitCurrency == accountCurrency)
      return valueInProfitCurrency;

   string conversionSymbol = "";
   if(SymbolSelect(profitCurrency + accountCurrency, true))
      conversionSymbol = profitCurrency + accountCurrency;
   else if(SymbolSelect(accountCurrency + profitCurrency, true))
      conversionSymbol = accountCurrency + profitCurrency;
   else
      return 0.0;

   double conversionRate = SymbolInfoDouble(conversionSymbol, SYMBOL_BID);
   if(conversionRate <= 0.0)
      return 0.0;

   string conversionBase = SymbolInfoString(conversionSymbol, SYMBOL_CURRENCY_BASE);
   if(conversionBase == accountCurrency)
      return valueInProfitCurrency / conversionRate;
   return valueInProfitCurrency * conversionRate;
}

bool SlotIsOpen(const ENUM_DAY_OF_WEEK day, const string startText, const string endText, const MqlDateTime &now)
{
   if((ENUM_DAY_OF_WEEK)now.day_of_week != day) return false;
   int startHour, startMinute, endHour, endMinute;
   if(!ParseHHMM(startText, startHour, startMinute) || !ParseHHMM(endText, endHour, endMinute))
      return false;
   int current = now.hour * 60 + now.min;
   return current >= startHour * 60 + startMinute && current <= endHour * 60 + endMinute;
}

bool IsTradingWindowOpen()
{
   if(!EnforceSchedule) return true;
   MqlDateTime now;
   TimeToStruct(TimeTradeServer(), now);
   return SlotIsOpen(Slot1Day, Slot1TimeHHMM, Slot1EndHHMM, now) ||
          SlotIsOpen(Slot2Day, Slot2TimeHHMM, Slot2EndHHMM, now);
}

bool HttpGet(const string url, string &resultBody, string &resultHeaders, const string extraHeaders = "")
{
   resultHeaders = "";
   resultBody = "";
   if(StringLen(url) <= 0) return false;

   string headers = "Connection: keep-alive\r\n";
   if(StringLen(extraHeaders) > 0)
   {
      headers += extraHeaders;
      if(StringSubstr(headers, StringLen(headers) - 2, 2) != "\r\n")
         headers += "\r\n";
   }

   uchar request[];
   uchar response[];
   ResetLastError();
   int status = WebRequest("GET", url, headers, TimeoutMs, request, response, resultHeaders);
   if(status == -1)
   {
      PrintFormat("[RX] GET failed (%d) %s", GetLastError(), url);
      return false;
   }
   resultBody = CharArrayToString(response, 0, WHOLE_ARRAY, CP_UTF8);
   return StringLen(resultBody) > 0;
}

bool HttpPostJSON(const string url, const string json, string &resultBody, string &resultHeaders)
{
   resultBody = "";
   resultHeaders = "";
   string headers = "Connection: close\r\nContent-Type: application/json\r\nX-Auth-Token: " + AUTH_SHARED + "\r\n";
   uchar request[];
   int jsonLength = StringLen(json);
   ArrayResize(request, jsonLength);
   StringToCharArray(json, request, 0, jsonLength, CP_UTF8);
   uchar response[];
   ResetLastError();
   int status = WebRequest("POST", url, headers, TimeoutMs, request, response, resultHeaders);
   if(status == -1)
   {
      PrintFormat("[RX] POST failed (%d) %s", GetLastError(), url);
      return false;
   }
   resultBody = CharArrayToString(response, 0, WHOLE_ARRAY, CP_UTF8);
   if(status < 200 || status >= 300)
   {
      PrintFormat("[RX] POST HTTP %d %s", status, resultBody);
      return false;
   }
   return true;
}

bool PrepareSymbol(string &symbol)
{
   if(StringLen(symbol) <= 0) return false;
   string lower = symbol;
   StringToLower(lower);
   string bases[2];
   bases[0] = symbol;
   bases[1] = lower;
   string suffixes[5] = {"", ".pro", ".c", ".cash", ".f"};

   for(int baseIndex = 0; baseIndex < 2; baseIndex++)
   {
      for(int suffixIndex = 0; suffixIndex < 5; suffixIndex++)
      {
         string candidate = bases[baseIndex] + suffixes[suffixIndex];
         if(candidate == "" || !SymbolSelect(candidate, true)) continue;
         if(SymbolInfoInteger(candidate, SYMBOL_TRADE_MODE) != SYMBOL_TRADE_MODE_FULL) continue;
         MqlTick tick;
         if(!SymbolInfoTick(candidate, tick) || tick.time <= 0) continue;
         symbol = candidate;
         return true;
      }
   }
   PrintFormat("[RX] No tradable symbol for %s", symbol);
   return false;
}

bool SnapLots(const string symbol, double &lots)
{
   double minimum = 0.0, maximum = 0.0, step = 0.0;
   if(lots <= 0.0) return false;
   if(!SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN, minimum)) return false;
   if(!SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX, maximum)) return false;
   if(!SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP, step)) return false;
   double snapped = 0.0;
   if(!ReceiverSnapLots(lots, minimum, maximum, step, snapped)) return false;
   lots = snapped;
   return true;
}

void CheckEmergencyStop()
{
   if(!EmergencyStop) return;
   Print("[RX] Emergency stop");
   LogToFile("[EMERGENCY STOP]");
   int remaining = 0;
   for(int index = PositionsTotal() - 1; index >= 0; index--)
   {
      ulong ticket = PositionGetTicket(index);
      if(PositionSelectByTicket(ticket) && PositionGetInteger(POSITION_MAGIC) == Magic)
      {
         if(Trade.PositionClose(ticket))
            ForgetVirtualStop(ticket);
         else
            remaining++;
      }
   }
   for(int index = OrdersTotal() - 1; index >= 0; index--)
   {
      ulong ticket = OrderGetTicket(index);
      if(OrderSelect(ticket) && OrderGetInteger(ORDER_MAGIC) == Magic && !Trade.OrderDelete(ticket))
         remaining++;
   }
   // Stay attached until everything is closed, so silent stops keep running
   // on anything the broker refused to close.
   if(remaining > 0)
   {
      SendNoticeOnce(StringFormat("Emergency stop: %d positions or orders could not be closed yet, retrying", remaining), 60);
      MaintainPositions();
      return;
   }
   ExpertRemove();
}

#include "../Include/ReceiverDailyState.mqh"
#include "../Include/ReceiverPositionManagement.mqh"
#include "../Include/ReceiverOrderExecution.mqh"
#include "../Include/ReceiverSignalWorkflow.mqh"
#include "../Include/ReceiverAccountStamp.mqh"

bool PublishReceiverState()
{
   // Fail closed: without a derived day-start the server would see a baseline
   // that hides today's losses. Publishing nothing lets its stale-snapshot
   // check block trading until the rebuild succeeds.
   if(g_equityAtDayStart <= 0.0 || g_dayStartPending)
   {
      if(g_dayStartPendingSince == 0) g_dayStartPendingSince = TimeCurrent();
      static datetime lastPendingNotice = 0;
      if(TimeCurrent() - lastPendingNotice >= 30)
      {
         Print("[RX] Day-start balance not derived yet, so no state snapshot was sent");
         lastPendingNotice = TimeCurrent();
      }
      if(TimeCurrent() - g_dayStartPendingSince >= 300)
         SendNoticeOnce("Receiver has not been able to read today's trade history for 5 minutes; trading is blocked", 600);
      return false;
   }
   g_dayStartPendingSince = 0;

   long secondsSinceLastLoss = g_lastLossClose > 0
      ? (long)MathMax(0, (long)TimeTradeServer() - (long)g_lastLossClose)
      : -1;
   string json = ReceiverBuildStateJson(
      IntegerToString(Magic),
      g_equityAtDayStart,
      AccountInfoDouble(ACCOUNT_BALANCE),
      g_dailyClosedNet,
      g_lossesToday,
      g_spikesToday,
      secondsSinceLastLoss,
      IsTradingWindowOpen(),
      AllOurPositionsAtBE());

   string responseBody, responseHeaders;
   if(!HttpPostJSON(URL_RECEIVER_STATE(), json, responseBody, responseHeaders))
      return false;

   string changedSummary = ReceiverJsonString(responseBody, "changed_summary");
   if(changedSummary != "")
      Print("[RX] State changed: ", changedSummary);

   static string lastGate = "";
   if(ReceiverJsonTrue(responseBody, "gate_ok"))
      lastGate = "";
   else
   {
      string gateText = ReceiverJsonString(responseBody, "gate") + ": " + ReceiverJsonString(responseBody, "reason");
      if(gateText != lastGate)
      {
         Print("[RX] Server gate closed: ", gateText);
         lastGate = gateText;
      }
   }
   return true;
}

int OnInit()
{
   if(StringLen(AUTH_SHARED) == 0)
   {
      Print("[RX] AUTH_SHARED is empty. Set it in the EA inputs to the server's shared secret.");
      return INIT_PARAMETERS_INCORRECT;
   }
   g_effRiskPerTradePct = RiskPerTradePct;
   if(g_effRiskPerTradePct <= 0.0 || g_effRiskPerTradePct > 10.0)
   {
      Print("[RX] RiskPerTradePct must be between 0 and 10. Using 1%.");
      g_effRiskPerTradePct = 1.0;
   }

   Trade.SetExpertMagicNumber(Magic);
   Trade.SetDeviationInPoints(Slippage);
   EventSetMillisecondTimer(PollMs < 100 ? 100 : PollMs);
   InitializeMarketWatch();
   WarmAtrHandles();
   RestoreVirtualStops();
   LoadLastSignalId();
   LoadPersistedDay();
   RefreshTradingDay(true);
   MaybeWriteAccountStamp(true);
   PublishReceiverState();
   PrintFormat("[RX] Account %I64d day-start %.2f balance %.2f",
               AccountInfoInteger(ACCOUNT_LOGIN), g_equityAtDayStart, AccountInfoDouble(ACCOUNT_BALANCE));

   PrintFormat("[RX] Ready. Magic=%I64d risk=%.2f%% schedule=%s auto-BE=%s state=%s",
               Magic, g_effRiskPerTradePct,
               EnforceSchedule ? "slots" : "open",
               AutoBE_3R_Enable ? "on" : "off",
               URL_RECEIVER_STATE());
   PrintFormat("[RX] Volatility ATR %s live %d bars, baseline %d bars (one day)",
               EnumToString(AtrTimeframe), AtrPeriod, AtrBaselinePeriod);
   LogToFile("[INIT] Receiver attached");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ReleaseAtrHandles();
   DeleteAccountStampOnDeinit(reason);
   LogToFile("[DEINIT] " + IntegerToString(reason));
   if(g_logFileHandle != INVALID_HANDLE)
   {
      FileClose(g_logFileHandle);
      g_logFileHandle = INVALID_HANDLE;
   }
}

void OnTimer()
{
   MaybeWriteAccountStamp(false); // First, before any early return, so the stamp stays fresh
   if(EmergencyStop)
   {
      CheckEmergencyStop();
      return;
   }
   RefreshTradingDay(false);
   CheckAndRepairState();
   NukeForeignsByMagic();
   MaintainPositions();
   PollHistoryForNewCloses();
   FlushPendingRiskClears();

   static ulong lastPublishMs = 0;
   ulong nowMs = GetTickCount64();
   int publishIntervalMs = ReceiverStatePublishMs < 250 ? 250 : ReceiverStatePublishMs;
   bool due = lastPublishMs == 0 || nowMs - lastPublishMs >= (ulong)publishIntervalMs;
   if(g_publishStateNow || due)
   {
      lastPublishMs = nowMs;
      if(!PublishReceiverState())
         return;
      g_publishStateNow = false;
   }
   // Network calls above can take seconds; check silent stops again before a new trade.
   MaintainPositions();
   // Publishing runs only on some ticks, so gate signal intake here as well.
   if(g_dayStartPending || g_equityAtDayStart <= 0.0)
      return;
   ProcessPendingServerSignal();
}
