#ifndef RECEIVER_DAILY_STATE_MQH
#define RECEIVER_DAILY_STATE_MQH

// Tracks the facts posted to /receiver-state. Thresholds that turn those facts
// into allow/block decisions live in the server configuration.

string DayStatePrefix()
{
   return "RX_" + IntegerToString(Magic) + "_";
}

void PersistTradingDay()
{
   string prefix = DayStatePrefix();
   GlobalVariableSet(prefix + "day", (double)g_dayAnchor);
   GlobalVariableSet(prefix + "start", g_equityAtDayStart);
   GlobalVariableSet(prefix + "login", (double)AccountInfoInteger(ACCOUNT_LOGIN));
   GlobalVariablesFlush();
}

void LoadPersistedDay()
{
   string prefix = DayStatePrefix();
   bool dayStored = GlobalVariableCheck(prefix + "day") && GlobalVariableCheck(prefix + "start");
   bool loginStored = GlobalVariableCheck(prefix + "login");
   long storedLogin = loginStored ? (long)MathRound(GlobalVariableGet(prefix + "login")) : 0;
   long currentLogin = (long)AccountInfoInteger(ACCOUNT_LOGIN);

   if(ReceiverShouldResetForAccount(dayStored, loginStored, storedLogin, currentLogin))
   {
      if(!loginStored)
         PrintFormat("[RX] Saved day-start had no account number. Using account %I64d.", currentLogin);
      else
         PrintFormat("[RX] Account changed from %I64d to %I64d. Day-start balance will follow this account.",
                     storedLogin, currentLogin);
      LogToFile(StringFormat("[ACCOUNT] %I64d", currentLogin));
      g_dayAnchor = DayAnchor(TimeTradeServer());
      g_equityAtDayStart = 0.0;
      g_dailyClosedNet = 0.0;
      g_lossesToday = 0;
      g_spikesToday = 0;
      g_lastLossClose = 0;
      GlobalVariableDel(prefix + "start");
      GlobalVariableSet(prefix + "login", (double)currentLogin);
      return;
   }

   if(!dayStored)
      return;

   datetime storedDay = (datetime)GlobalVariableGet(prefix + "day");
   double storedStart = GlobalVariableGet(prefix + "start");
   if(storedDay != DayAnchor(TimeTradeServer()) || storedStart <= 0.0)
      return;

   g_dayAnchor = storedDay;
   g_equityAtDayStart = storedStart;
}

void CopyDailyState(ReceiverDailyState &state)
{
   state.day_anchor = (long)g_dayAnchor;
   state.start_balance = g_equityAtDayStart;
   state.closed_net = g_dailyClosedNet;
   state.losses = g_lossesToday;
   state.spikes = g_spikesToday;
   state.last_loss_time = (long)g_lastLossClose;
}

void StoreDailyState(const ReceiverDailyState &state)
{
   g_dayAnchor = (datetime)state.day_anchor;
   g_equityAtDayStart = state.start_balance;
   g_dailyClosedNet = state.closed_net;
   g_lossesToday = state.losses;
   g_spikesToday = state.spikes;
   g_lastLossClose = (datetime)state.last_loss_time;
}

double SpikeThresholdMoney()
{
   if(g_equityAtDayStart <= 0.0 || SpikeThresholdPct <= 0.0)
      return DBL_MAX;
   return g_equityAtDayStart * SpikeThresholdPct / 100.0;
}

// A flat close that is negative only by commission is not a loss. 0.05% of the
// day-start balance is below a real stop-out and above a typical scratch.
double LossCountTolerance()
{
   if(g_equityAtDayStart <= 0.0)
      return 0.01;
   double floor = g_equityAtDayStart * 0.0005;
   return floor < 0.01 ? 0.01 : floor;
}

ulong g_processedCloseKeys[];

bool RememberThisClose(const ulong deal, const long closeTime, const double net)
{
   ulong positionId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   ulong key = ReceiverCloseKey(positionId, closeTime, net);
   return ReceiverRememberClose(g_processedDeals, g_processedCloseKeys, deal, key);
}

bool ReadOurClosingNet(const ulong deal, double &net, long &closeTime)
{
   net = 0.0;
   closeTime = 0;
   if(deal == 0) return false;
   ulong positionId = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   bool tracked = IndexOfTicket(positionId) >= 0 || RecentlyClosedTicket(positionId)
      || (positionId > 0 && GlobalVariableCheck(VirtualStopKey(positionId)));
   if(!ReceiverDealBelongsToUs((long)HistoryDealGetInteger(deal, DEAL_MAGIC), (long)Magic, tracked))
      return false;

   long entry = (long)HistoryDealGetInteger(deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_INOUT) return false;

   closeTime = (long)HistoryDealGetInteger(deal, DEAL_TIME);
   if(g_dayAnchor > 0 && closeTime > 0 && closeTime < (long)g_dayAnchor) return false;

   net = HistoryDealGetDouble(deal, DEAL_PROFIT)
       + HistoryDealGetDouble(deal, DEAL_COMMISSION)
       + HistoryDealGetDouble(deal, DEAL_SWAP);
   return IsFiniteNumber(net);
}

void ApplyReceiverDealToRuntime(const double net, const long closeTime, bool &wasLoss, bool &wasSpike, const bool announce)
{
   ReceiverDailyState state;
   CopyDailyState(state);
   if(!ReceiverApplyClosedDeal(state, true, true, true, net, SpikeThresholdMoney(), closeTime, LossCountTolerance(), wasLoss, wasSpike))
      return;
   StoreDailyState(state);
   if(announce)
      g_publishStateNow = true;
   if(wasLoss && announce)
      g_lastLossClose = TimeTradeServer();
   if(!announce) return;
   LogToFile(StringFormat("%s %.2f", wasLoss ? "[LOSS]" : "[CLOSE]", net));
}

void RebuildDailyFactsFromHistory()
{
   // With no saved day-start, derive it below as balance minus today's closed
   // net. Setting it to the current balance here would hide today's losses.
   datetime from = g_dayAnchor > 0 ? g_dayAnchor : DayAnchor(TimeTradeServer());
   if(!HistorySelect(from, TimeTradeServer()))
   {
      // Any failed rebuild leaves today's facts unknown: block and retry on the
      // next timer instead of guessing.
      g_dayStartPending = true;
      return;
   }

   int total = HistoryDealsTotal();
   if(g_equityAtDayStart <= 0.0)
   {
      double closedNet = 0.0;
      for(int index = 0; index < total; index++)
      {
         double net = 0.0;
         long closeTime = 0;
         if(ReadOurClosingNet(HistoryDealGetTicket(index), net, closeTime))
            closedNet += net;
      }

      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double derived = balance - closedNet;
      g_equityAtDayStart = derived > 0.0 ? derived : balance;
      if(g_equityAtDayStart < 0.0)
         g_equityAtDayStart = 0.0;
   }

   g_dailyClosedNet = 0.0;
   g_lossesToday = 0;
   g_spikesToday = 0;
   g_lastLossClose = 0;
   ArrayResize(g_processedDeals, 0);
   ArrayResize(g_processedCloseKeys, 0);
   g_dayStartPending = false;

   for(int index = 0; index < total; index++)
   {
      ulong deal = HistoryDealGetTicket(index);
      double net = 0.0;
      long closeTime = 0;
      if(!ReadOurClosingNet(deal, net, closeTime)) continue;
      if(!RememberThisClose(deal, closeTime, net)) continue;

      bool wasLoss = false;
      bool wasSpike = false;
      ApplyReceiverDealToRuntime(net, closeTime, wasLoss, wasSpike, false);
   }
   PersistTradingDay();
}

void RefreshTradingDay(const bool rebuildFacts)
{
   datetime anchor = DayAnchor(TimeTradeServer());
   ReceiverDailyState state;
   CopyDailyState(state);
   long previousAnchor = state.day_anchor;
   bool reset = ReceiverApplyDailyReset(state, (long)anchor, AccountInfoDouble(ACCOUNT_BALANCE));
   if(reset)
   {
      StoreDailyState(state);
      // First load with no saved day: the current balance already includes
      // today's closed trades, so clear it and let the rebuild derive it.
      if(ReceiverDayStartNeedsDerive(reset, previousAnchor))
         g_equityAtDayStart = 0.0;
   }

   if(ReceiverShouldRebuildDay(rebuildFacts, g_equityAtDayStart, g_dayStartPending))
      RebuildDailyFactsFromHistory();

   if(reset && previousAnchor != 0)
   {
      string message = StringFormat("New trading day. Day-start balance %.2f", g_equityAtDayStart);
      Print("[RX] ", message);
      LogToFile("[NEW_DAY] " + message);
      SendNoticeOnce(message, 1);
   }
}

string g_pendingRiskClears[];

// Returns the HTTP status, or -1 when the request never reached the server.
int PostTradeRiskQuiet(const string json)
{
   string headers = "Connection: close\r\nContent-Type: application/json\r\nX-Auth-Token: " + AUTH_SHARED + "\r\n";
   uchar request[];
   int jsonLength = StringLen(json);
   ArrayResize(request, jsonLength);
   StringToCharArray(json, request, 0, jsonLength, CP_UTF8);
   uchar response[];
   string responseHeaders;
   ResetLastError();
   int status = WebRequest("POST", URL_TRADE_RISK(), headers, TimeoutMs, request, response, responseHeaders);
   return status;
}

void QueueRiskClear(const string symbol, const string side)
{
   string key = symbol + "|" + side;
   for(int index = 0; index < ArraySize(g_pendingRiskClears); index++)
      if(g_pendingRiskClears[index] == key)
         return;
   int count = ArraySize(g_pendingRiskClears);
   ArrayResize(g_pendingRiskClears, count + 1);
   g_pendingRiskClears[count] = key;
}

void FlushPendingRiskClears()
{
   for(int index = ArraySize(g_pendingRiskClears) - 1; index >= 0; index--)
   {
      string key = g_pendingRiskClears[index];
      int bar = StringFind(key, "|");
      if(bar <= 0)
      {
         ArrayRemove(g_pendingRiskClears, index, 1);
         continue;
      }
      string symbol = StringSubstr(key, 0, bar);
      string side = StringSubstr(key, bar + 1);
      string json = StringFormat("{\"symbol\":\"%s\",\"side\":\"%s\",\"closed\":true}", symbol, side);
      int status = PostTradeRiskQuiet(json);
      if(status >= 200 && status < 300)
         ArrayRemove(g_pendingRiskClears, index, 1);
      else if(status == 400 || status == 404 || status == 409 || status == 410 || status == 422)
      {
         // The server rejected this entry itself; drop it so it cannot block the rest.
         // Auth, timeout and rate-limit codes (401, 403, 408, 429) are retried.
         PrintFormat("[RX] Trade-risk clear for %s rejected with HTTP %d; dropped", key, status);
         LogToFile(StringFormat("[RISK_CLEAR_DROPPED] %s HTTP %d", key, status));
         ArrayRemove(g_pendingRiskClears, index, 1);
      }
      else
         break; // No answer, auth, rate limit or server error; retry next cycle.
   }
}

void ClearClosedTradeRisk(const ulong deal)
{
   if(deal == 0 || !HistoryDealSelect(deal))
      return;

   long dealType = HistoryDealGetInteger(deal, DEAL_TYPE);
   string side = "";
   if(dealType == DEAL_TYPE_SELL)
      side = "BUY";
   else if(dealType == DEAL_TYPE_BUY)
      side = "SELL";
   else
      return;

   string symbol = HistoryDealGetString(deal, DEAL_SYMBOL);
   if(StringLen(symbol) == 0)
      return;
   QueueRiskClear(symbol, side);
   FlushPendingRiskClears();
}

void OnTradeTransaction(const MqlTradeTransaction &transaction,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
{
   if(transaction.type != TRADE_TRANSACTION_DEAL_ADD || transaction.deal == 0) return;

   datetime from = g_dayAnchor > 0 ? g_dayAnchor : TimeTradeServer();
   HistorySelect(from, TimeTradeServer());

   double net = 0.0;
   long closeTime = 0;
   if(!ReadOurClosingNet(transaction.deal, net, closeTime)) return;
   if(!RememberThisClose(transaction.deal, closeTime, net)) return;
   if(closeTime <= 0) closeTime = (long)TimeTradeServer();

   bool wasLoss = false;
   bool wasSpike = false;
   ApplyReceiverDealToRuntime(net, closeTime, wasLoss, wasSpike, true);
   ClearClosedTradeRisk(transaction.deal);
}

void PollHistoryForNewCloses()
{
   datetime from = g_dayAnchor > 0 ? g_dayAnchor : DayAnchor(TimeTradeServer());
   if(!HistorySelect(from, TimeTradeServer())) return;

   int total = HistoryDealsTotal();
   for(int index = total - 1; index >= 0 && index >= total - 400; index--)
   {
      ulong deal = HistoryDealGetTicket(index);
      double net = 0.0;
      long closeTime = 0;
      if(!ReadOurClosingNet(deal, net, closeTime)) continue;
      if(!RememberThisClose(deal, closeTime, net)) continue;
      if(closeTime <= 0) closeTime = (long)TimeTradeServer();

      bool wasLoss = false;
      bool wasSpike = false;
      ApplyReceiverDealToRuntime(net, closeTime, wasLoss, wasSpike, true);
      ClearClosedTradeRisk(deal);
   }
}

#endif
