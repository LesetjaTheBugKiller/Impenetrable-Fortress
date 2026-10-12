#ifndef RECEIVER_SIGNAL_WORKFLOW_MQH
#define RECEIVER_SIGNAL_WORKFLOW_MQH

// Polls /next and executes a signal the server has already released.

// The last executed signal ID is kept in a file so a restart between a fill
// and a successful /ack cannot execute the same signal twice.
string LastSignalFileName()
{
   return "RX_" + IntegerToString(Magic) + "_last_signal.txt";
}

void LoadLastSignalId()
{
   int handle = FileOpen(LastSignalFileName(), FILE_READ | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE) return;
   string stored = FileReadString(handle);
   FileClose(handle);
   StringTrimLeft(stored);
   StringTrimRight(stored);
   if(stored != "") g_lastAckedSignalID = stored;
}

void SaveLastSignalId(const string id)
{
   g_lastAckedSignalID = id;
   int handle = FileOpen(LastSignalFileName(), FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(handle == INVALID_HANDLE)
   {
      Print("[RX] Could not save the last signal ID");
      return;
   }
   FileWriteString(handle, id);
   FileClose(handle);
}

bool AcknowledgePendingSignal(const string headers)
{
   string responseBody, responseHeaders;
   return HttpGet(URL_ACK(), responseBody, responseHeaders, headers);
}

void ProcessPendingServerSignal()
{
   string body, responseHeaders;
   string headers = "X-Auth-Token: " + AUTH_SHARED;
   if(!HttpGet(URL_NEXT(), body, responseHeaders, headers)) return;
   if(!ReceiverJsonTrue(body, "ok") || ReceiverJsonTrue(body, "empty")) return;

   ReceiverSignal signal;
   if(!ReceiverParseSignal(body, signal) || !signal.has_signal)
   {
      Print("[RX] Invalid signal payload; acknowledging so it is not polled again");
      AcknowledgePendingSignal(headers);
      return;
   }

   if(signal.id != "" && signal.id == g_lastAckedSignalID)
   {
      AcknowledgePendingSignal(headers);
      return;
   }

   long ageSeconds = 0;
   if(ReceiverSignalExpired(signal, (long)TimeGMT(), SignalMaxAgeSeconds, ageSeconds))
   {
      PrintFormat("[RX] Signal expired (%d s old, max %d s)", ageSeconds, SignalMaxAgeSeconds);
      LogToFile("[SIGNAL_EXPIRED] Age: " + IntegerToString(ageSeconds) + "s");
      AcknowledgePendingSignal(headers);
      return;
   }

   string side = signal.side;
   string symbol = signal.symbol;
   PrintFormat("[RX] Signal %s %s", side, symbol);
   LogToFile("[SIGNAL_RECEIVED] " + side + " " + symbol);

   if(!EnsureSymbolInMarketWatch(symbol) || !PrepareSymbol(symbol))
   {
      PrintFormat("[RX] Symbol unavailable: %s", symbol);
      AcknowledgePendingSignal(headers);
      return;
   }

   double lots = 0.0;
   double stopLossPrice = 0.0;
   double entryPrice = 0.0;
   if(!CalcDynamicLots(symbol, side, lots, stopLossPrice, entryPrice))
   {
      Print("[RX] Lot calculation failed");
      AcknowledgePendingSignal(headers);
      return;
   }

   if(stopLossPrice <= 0.0 || entryPrice <= 0.0 ||
      (side == "BUY" && stopLossPrice >= entryPrice) ||
      (side == "SELL" && stopLossPrice <= entryPrice))
   {
      PrintFormat("[RX] Invalid trade prices entry=%.5f sl=%.5f", entryPrice, stopLossPrice);
      LogToFile("[VALIDATION_FAILED] Invalid prices");
      AcknowledgePendingSignal(headers);
      return;
   }

   PrintFormat("[RX] %s %s lots=%.6f entry=%.5f silent sl=%.5f", side, symbol, lots, entryPrice, stopLossPrice);
   // Record the ID before sending the order, so a crash mid-trade cannot repeat it.
   if(signal.id != "") SaveLastSignalId(signal.id);
   if(!SafeTradeExecute(symbol, side, lots, stopLossPrice, entryPrice))
   {
      PrintFormat("[RX] Trade failed: ret=%d (%s)", Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
      AcknowledgePendingSignal(headers);
      return;
   }

   LogToFile("[TRADE_EXECUTED] " + side + " " + symbol + " Lots:" + DoubleToString(lots, 6));
   g_publishStateNow = true;
   PublishReceiverState();

   if(!AcknowledgePendingSignal(headers))
      Print("[RX] /ack failed; the signal may be offered again");
}

#endif
