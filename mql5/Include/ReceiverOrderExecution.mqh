#ifndef RECEIVER_ORDER_EXECUTION_MQH
#define RECEIVER_ORDER_EXECUTION_MQH

bool CalcDynamicLots(const string symbol, const string side,
                     double &lots_out, double &stop_loss_out, double &entry_price_out)
{
   lots_out = 0.0;
   stop_loss_out = 0.0;
   entry_price_out = 0.0;
   PrintFormat("[LOT_CALC] Starting calculation for %s %s", side, symbol);

   if(!EnsureSymbolInMarketWatch(symbol))
      PrintFormat("[LOT_CALC] WARNING: %s may have stale data", symbol);

   MqlTick tick;
   if(!SymbolInfoTick(symbol, tick))
   {
      PrintFormat("[LOT_CALC] FAIL: Cannot get tick data for %s", symbol);
      return false;
   }

   double entryPrice = side == "BUY" ? tick.ask : tick.bid;
   entry_price_out = entryPrice;

   double stopPoints = ResolveStopPoints(symbol, true);
   if(stopPoints <= 0.0)
   {
      PrintFormat("[LOT_CALC] FAIL: Invalid stop distance for %s", symbol);
      return false;
   }

   double pointSize = GetSymbolPoint(symbol);
   if(!ReceiverCalculateStopPrice(side == "BUY", entryPrice, stopPoints, pointSize, stop_loss_out))
   {
      PrintFormat("[LOT_CALC] FAIL: Invalid stop price inputs for %s", symbol);
      return false;
   }

   double valuePerPoint = CalculateValuePerPoint(symbol);
   if(valuePerPoint <= 0.0)
   {
      PrintFormat("[LOT_CALC] FAIL: Invalid value per point for %s", symbol);
      return false;
   }

   double volumeMin = 0.0;
   double volumeMax = 0.0;
   double volumeStep = 0.0;
   if(!SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN, volumeMin) ||
      !SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX, volumeMax) ||
      !SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP, volumeStep))
   {
      PrintFormat("[LOT_CALC] FAIL: Cannot read volume constraints for %s", symbol);
      return false;
   }

   ReceiverLotPlan plan = ReceiverCalculateLotPlan(
      AccountInfoDouble(ACCOUNT_EQUITY),
      g_effRiskPerTradePct,
      stopPoints,
      valuePerPoint,
      volumeMin,
      volumeMax,
      volumeStep,
      MaxLotsCap
   );
   if(!plan.valid)
   {
      PrintFormat("[LOT_CALC] FAIL: %s", plan.failure);
      return false;
   }

   double lots = plan.lots;
   ENUM_ORDER_TYPE orderType = side == "BUY" ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double marginNeeded = 0.0;
   if(!OrderCalcMargin(orderType, symbol, lots, entryPrice, marginNeeded))
   {
      PrintFormat("[LOT_CALC] FAIL: Margin calculation failed for %s", symbol);
      return false;
   }

   double freeMargin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   if(marginNeeded > freeMargin)
   {
      double previousLots = lots;
      if(!ReceiverAdjustLotsForMargin(lots, marginNeeded, freeMargin,
                                      volumeMin, volumeMax, volumeStep, lots))
      {
         PrintFormat("[LOT_CALC] FAIL: Cannot fit volume to available margin for %s", symbol);
         return false;
      }

      double adjustedMargin = 0.0;
      if(!OrderCalcMargin(orderType, symbol, lots, entryPrice, adjustedMargin) || adjustedMargin > freeMargin)
      {
         PrintFormat("[LOT_CALC] FAIL: Adjusted volume still exceeds free margin for %s", symbol);
         return false;
      }
      PrintFormat("[LOT_CALC] Margin adjustment: %.6f -> %.6f", previousLots, lots);
   }

   lots_out = lots;
   PrintFormat("[LOT_CALC] SUCCESS: %s lots=%.6f", symbol, lots_out);
   LogToFile(StringFormat("[LOT_CALC] %s %s: StopPts=%.1f, Lots=%.6f", side, symbol, stopPoints, lots_out));
   return lots_out > 0.0;
}

void ReportTradeRisk(const string symbol, const string side, const double points, const double cashRisk)
{
   if(points <= 0.0 || cashRisk <= 0.0)
      return;
   string pointsText = DoubleToString(points, 1);
   string riskText = DoubleToString(cashRisk, 2);
   StringReplace(pointsText, ",", ".");
   StringReplace(riskText, ",", ".");
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
   string json = StringFormat("{\"symbol\":\"%s\",\"side\":\"%s\",\"points\":%s,\"risk\":%s,\"currency\":\"%s\"}",
                               symbol, side, pointsText, riskText, currency);
   string responseBody, responseHeaders;
   HttpPostJSON(URL_TRADE_RISK(), json, responseBody, responseHeaders);
}

bool PlaceVisibleTakeProfit(const string symbol, const ulong ticket, const double target);

bool SafeTradeExecute(const string symbol, const string side, double lots, double stopLossPrice, double plannedEntry)
{
   double reward = TakeProfitReward > 0.0 ? TakeProfitReward : 4.0;
   bool isBuy = side == "BUY";
   double entryGuess = plannedEntry > 0.0
      ? plannedEntry
      : (isBuy ? SymbolInfoDouble(symbol, SYMBOL_ASK) : SymbolInfoDouble(symbol, SYMBOL_BID));
   double takeProfit = ReceiverNormalizePrice(
      symbol, ReceiverTakeProfitPrice(isBuy, entryGuess, stopLossPrice, reward));

   string logMessage = StringFormat("[SAFE_TRADE] %s %s Lots=%.6f SL=%.5f TP=%.5f",
                                    side, symbol, lots, stopLossPrice, takeProfit);
   Print(logMessage);
   LogToFile(logMessage);

   bool success = isBuy
      ? Trade.Buy(lots, symbol, 0.0, 0.0, takeProfit)
      : Trade.Sell(lots, symbol, 0.0, 0.0, takeProfit);
   if(!success && takeProfit > 0.0 && Trade.ResultDeal() == 0)
   {
      PrintFormat("[SAFE_TRADE] Open with TP %.5f rejected ret=%d (%s); retrying without TP",
                  takeProfit, Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
      success = isBuy
         ? Trade.Buy(lots, symbol, 0.0, 0.0, 0.0)
         : Trade.Sell(lots, symbol, 0.0, 0.0, 0.0);
   }
   if(!success)
   {
      string errorMessage = StringFormat("[SAFE_TRADE_FAILED] ret=%d (%s)",
                                         Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
      Print(errorMessage);
      LogToFile(errorMessage);
      return false;
   }

   ulong positionTicket = 0;
   ulong deal = Trade.ResultDeal();
   if(deal > 0 && HistoryDealSelect(deal))
      positionTicket = (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID);
   if(positionTicket == 0 || !PositionSelectByTicket(positionTicket))
   {
      datetime newest = 0;
      for(int index = 0; index < PositionsTotal(); index++)
      {
         ulong ticket = PositionGetTicket(index);
         if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
         if((long)PositionGetInteger(POSITION_MAGIC) != Magic) continue;
         if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
         if(IndexOfTicket(ticket) >= 0) continue;
         datetime opened = (datetime)PositionGetInteger(POSITION_TIME);
         if(positionTicket == 0 || opened >= newest)
         {
            newest = opened;
            positionTicket = ticket;
         }
      }
   }
   if(positionTicket == 0 || !PositionSelectByTicket(positionTicket))
   {
      PrintFormat("[SAFE_TRADE] Opened %s %s but the position ticket was not found", side, symbol);
      return true;
   }

   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   long type = (long)PositionGetInteger(POSITION_TYPE);
   double point = GetSymbolPoint(symbol);
   double riskPoints = point > 0.0 ? MathAbs(entry - stopLossPrice) / point : 0.0;
   RememberVirtualStop(positionTicket, type, stopLossPrice, riskPoints);
   double target = ReceiverNormalizePrice(
      symbol, ReceiverTakeProfitPrice(type == POSITION_TYPE_BUY, entry, stopLossPrice, reward));
   PlaceVisibleTakeProfit(symbol, positionTicket, target);
   double cashRisk = ReceiverCashRisk(lots, riskPoints, CalculateValuePerPoint(symbol));
   ReportTradeRisk(symbol, side, riskPoints, cashRisk);
   PrintFormat("[SAFE_TRADE] Silent stop %.5f take profit %.5f (1:%.0f) on #%I64u",
               stopLossPrice, target, reward, positionTicket);
   return true;
}

bool PlaceVisibleTakeProfit(const string symbol, const ulong ticket, const double target)
{
   if(ticket == 0 || target <= 0.0 || !PositionSelectByTicket(ticket))
      return false;
   double price = ReceiverNormalizePrice(symbol, target);
   if(price <= 0.0)
      return false;
   double point = GetSymbolPoint(symbol);
   double tolerance = point > 0.0 ? 0.5 * point : 1e-8;
   if(MathAbs(PositionGetDouble(POSITION_TP) - price) <= tolerance)
      return true;
   if(Trade.PositionModify(ticket, PositionGetDouble(POSITION_SL), price))
      return true;
   PrintFormat("[SAFE_TRADE] Take profit %.5f rejected on #%I64u ret=%d (%s)",
               price, ticket, Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
   LogToFile(StringFormat("[SAFE_TRADE] Take profit rejected #%I64u", ticket));
   return false;
}

#endif