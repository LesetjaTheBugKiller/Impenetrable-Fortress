#ifndef RECEIVER_POSITION_MANAGEMENT_MQH
#define RECEIVER_POSITION_MANAGEMENT_MQH

// Per-ticket backstop retry throttle and one-time notices, declared first so
// ForgetVirtualStop can clear them.
ulong g_backstopRetryTickets[];
datetime g_backstopRetryTimes[];
ulong g_unprotectableNoticed[];
string g_noTickSymbols[];
datetime g_noTickTimes[];

void ForgetBackstopTracking(const ulong ticket)
{
   for(int index = ArraySize(g_backstopRetryTickets) - 1; index >= 0; index--)
   {
      if(g_backstopRetryTickets[index] != ticket) continue;
      ArrayRemove(g_backstopRetryTickets, index, 1);
      ArrayRemove(g_backstopRetryTimes, index, 1);
   }
   for(int index = ArraySize(g_unprotectableNoticed) - 1; index >= 0; index--)
      if(g_unprotectableNoticed[index] == ticket)
         ArrayRemove(g_unprotectableNoticed, index, 1);
}

int IndexOfTicket(const ulong ticket)
{
   for(int index = 0; index < ArraySize(gTickets); index++)
      if(gTickets[index] == ticket) return index;
   return -1;
}

void ObserveOrSetBoundary(const ulong ticket, const long type, const double stopLoss);

string VirtualStopKey(const ulong ticket)
{
   return DayStatePrefix() + "VSL_" + IntegerToString(ticket);
}

string VirtualRiskKey(const ulong ticket)
{
   return DayStatePrefix() + "VR_" + IntegerToString(ticket);
}

void RememberVirtualStop(const ulong ticket, const long type, const double stop, const double riskPoints)
{
   int index = IndexOfTicket(ticket);
   if(index < 0)
      ObserveOrSetBoundary(ticket, type, stop);
   else
   {
      gTypeOfTicket[index] = type;
      gBoundarySL[index] = stop;
   }
   GlobalVariableSet(VirtualStopKey(ticket), stop);
   if(riskPoints > 0.0)
      GlobalVariableSet(VirtualRiskKey(ticket), riskPoints);
   // Write to disk now; MT5 otherwise saves globals only on a clean shutdown,
   // so a crash would lose the silent stop.
   GlobalVariablesFlush();
}

double VirtualStopPrice(const ulong ticket)
{
   int index = IndexOfTicket(ticket);
   if(index >= 0 && gBoundarySL[index] > 0.0)
      return gBoundarySL[index];
   string key = VirtualStopKey(ticket);
   if(!GlobalVariableCheck(key))
      return 0.0;
   return GlobalVariableGet(key);
}

double VirtualRiskPoints(const ulong ticket)
{
   string key = VirtualRiskKey(ticket);
   if(!GlobalVariableCheck(key))
      return 0.0;
   return GlobalVariableGet(key);
}

void RememberClosedTicket(const ulong ticket)
{
   if(ticket == 0)
      return;
   int count = ArraySize(g_recentClosedTickets);
   for(int index = 0; index < count; index++)
      if(g_recentClosedTickets[index] == ticket)
         return;
   if(count >= 32)
   {
      ArrayRemove(g_recentClosedTickets, 0, 1);
      count--;
   }
   ArrayResize(g_recentClosedTickets, count + 1);
   g_recentClosedTickets[count] = ticket;
}

bool RecentlyClosedTicket(const ulong ticket)
{
   if(ticket == 0)
      return false;
   for(int index = 0; index < ArraySize(g_recentClosedTickets); index++)
      if(g_recentClosedTickets[index] == ticket)
         return true;
   return false;
}

void ForgetVirtualStop(const ulong ticket)
{
   RememberClosedTicket(ticket);
   ForgetBackstopTracking(ticket);
   GlobalVariableDel(VirtualStopKey(ticket));
   GlobalVariableDel(VirtualRiskKey(ticket));
   GlobalVariablesFlush();
   int index = IndexOfTicket(ticket);
   if(index < 0)
      return;
   ArrayRemove(gTickets, index, 1);
   ArrayRemove(gBoundarySL, index, 1);
   ArrayRemove(gTypeOfTicket, index, 1);
}

void RestoreVirtualStops()
{
   for(int index = 0; index < PositionsTotal(); index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != Magic) continue;
      string key = VirtualStopKey(ticket);
      if(!GlobalVariableCheck(key)) continue;
      ObserveOrSetBoundary(ticket, (long)PositionGetInteger(POSITION_TYPE), GlobalVariableGet(key));
   }
}

bool VirtualStopReached(const long type, const double stop, const double bid, const double ask)
{
   if(stop <= 0.0)
      return false;
   if(type == POSITION_TYPE_BUY)
      return bid <= stop;
   return ask >= stop;
}

void ObserveOrSetBoundary(const ulong ticket, const long type, const double stopLoss)
{
   int index = IndexOfTicket(ticket);
   if(index < 0)
   {
      int count = ArraySize(gTickets);
      ArrayResize(gTickets, count + 1);
      ArrayResize(gBoundarySL, count + 1);
      ArrayResize(gTypeOfTicket, count + 1);
      gTickets[count] = ticket;
      gTypeOfTicket[count] = type;
      gBoundarySL[count] = stopLoss;
      return;
   }

   gTypeOfTicket[index] = type;
   if(type == POSITION_TYPE_BUY)
   {
      if(stopLoss > gBoundarySL[index]) gBoundarySL[index] = stopLoss;
   }
   else if(stopLoss < gBoundarySL[index])
      gBoundarySL[index] = stopLoss;
}

bool CloseIfWidened(const string symbol, const ulong ticket, const long type, const double stopLoss)
{
   int index = IndexOfTicket(ticket);
   if(index < 0)
   {
      if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
      return false;
   }

   double tolerance = 0.5 * GetSymbolPoint(symbol);
   bool widened = type == POSITION_TYPE_BUY
      ? stopLoss <= 0.0 || stopLoss < gBoundarySL[index] - tolerance
      : stopLoss <= 0.0 || stopLoss > gBoundarySL[index] + tolerance;
   if(widened)
   {
      if(!Trade.PositionClose(ticket))
         PrintFormat("[RX] Close FAIL %s #%I64u ret=%d (%s)", symbol, ticket,
                     Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
      return true;
   }

   if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
   return false;
}

void NukeForeignsByMagic()
{
   for(int index = PositionsTotal() - 1; index >= 0; index--)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) == Magic) continue;
      string symbol = PositionGetString(POSITION_SYMBOL);
      if(!Trade.PositionClose(ticket))
         PrintFormat("[RX] NUKE FAIL pos %s #%I64u ret=%d (%s)", symbol, ticket,
                     Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
   }
   for(int index = OrdersTotal() - 1; index >= 0; index--)
   {
      ulong ticket = OrderGetTicket(index);
      if(ticket == 0 || !OrderSelect(ticket)) continue;
      if((long)OrderGetInteger(ORDER_MAGIC) == Magic) continue;
      if(!Trade.OrderDelete(ticket))
         PrintFormat("[RX] NUKE FAIL order #%I64u ret=%d (%s)", ticket,
                     Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
   }
}

bool AllOurPositionsAtBE()
{
   int below = 0;
   bool unreadable = false;
   string reason = "";
   int total = PositionsTotal();
   for(int index = 0; index < total; index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0)
         continue;
      if(!PositionSelectByTicket(ticket))
      {
         if(IndexOfTicket(ticket) >= 0 || GlobalVariableCheck(VirtualStopKey(ticket)))
         {
            unreadable = true;
            reason = "managed position could not be selected";
         }
         continue;
      }
      if((long)PositionGetInteger(POSITION_MAGIC) != Magic)
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      long type = (long)PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double stopLoss = ReceiverStopReportedToGates(VirtualStopPrice(ticket), PositionGetDouble(POSITION_SL));
      double tolerance = ReceiverBreakEvenTolerance(
         GetSymbolPoint(symbol), SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE));
      if(!ReceiverPositionAtBreakEven(type == POSITION_TYPE_BUY, entry, stopLoss, tolerance))
      {
         below++;
         reason = StringFormat("%s #%I64u", symbol, ticket);
      }
   }

   bool atBreakEven = ReceiverAllManagedPositionsAtBreakEven(below, unreadable);
   static bool lastResult = true;
   static string lastReason = "";
   if(atBreakEven != lastResult || reason != lastReason)
   {
      if(atBreakEven)
         Print("[RX] Managed positions are at break-even");
      else
         Print("[RX] Break-even open: ", reason);
      lastResult = atBreakEven;
      lastReason = reason;
   }
   return atBreakEven;
}

// A rejected backstop is retried at most every 30 s per ticket, not on every
// 300 ms timer.
bool BackstopRetryDue(const ulong ticket)
{
   datetime now = TimeCurrent();
   for(int index = 0; index < ArraySize(g_backstopRetryTickets); index++)
   {
      if(g_backstopRetryTickets[index] != ticket) continue;
      if(now - g_backstopRetryTimes[index] < 30) return false;
      g_backstopRetryTimes[index] = now;
      return true;
   }
   int count = ArraySize(g_backstopRetryTickets);
   if(count >= 64)
   {
      ArrayRemove(g_backstopRetryTickets, 0, 1);
      ArrayRemove(g_backstopRetryTimes, 0, 1);
      count--;
   }
   ArrayResize(g_backstopRetryTickets, count + 1);
   ArrayResize(g_backstopRetryTimes, count + 1);
   g_backstopRetryTickets[count] = ticket;
   g_backstopRetryTimes[count] = now;
   return true;
}

// A position from before this build that is already at break-even has no
// stored risk distance, so no backstop can be computed. Say so once.
void NoticeUnprotectableOnce(const ulong ticket, const string symbol)
{
   for(int index = 0; index < ArraySize(g_unprotectableNoticed); index++)
      if(g_unprotectableNoticed[index] == ticket) return;
   int count = ArraySize(g_unprotectableNoticed);
   ArrayResize(g_unprotectableNoticed, count + 1);
   g_unprotectableNoticed[count] = ticket;
   SendNoticeOnce(StringFormat("%s #%I64u has no broker stop and no stored risk distance; set a stop manually", symbol, ticket), 1);
}

bool NoTickWarningDue(const string symbol)
{
   datetime now = TimeCurrent();
   for(int index = 0; index < ArraySize(g_noTickSymbols); index++)
   {
      if(g_noTickSymbols[index] != symbol) continue;
      if(now - g_noTickTimes[index] < 30) return false;
      g_noTickTimes[index] = now;
      return true;
   }
   int count = ArraySize(g_noTickSymbols);
   ArrayResize(g_noTickSymbols, count + 1);
   ArrayResize(g_noTickTimes, count + 1);
   g_noTickSymbols[count] = symbol;
   g_noTickTimes[count] = now;
   return true;
}

void MaintainPositions()
{
   for(int index = 0; index < PositionsTotal(); index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0 || !PositionSelectByTicket(ticket)) continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != Magic) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      long type = (long)PositionGetInteger(POSITION_TYPE);
      double entry = PositionGetDouble(POSITION_PRICE_OPEN);
      double virtualStop = VirtualStopPrice(ticket);
      if(virtualStop > 0.0 && IndexOfTicket(ticket) < 0)
         ObserveOrSetBoundary(ticket, type, virtualStop);

      if(virtualStop > 0.0)
      {
         double tolerance = 0.5 * GetSymbolPoint(symbol);
         bool isBuy = type == POSITION_TYPE_BUY;

         // Re-seat a missing broker backstop. Use the original risk distance,
         // because after break-even the silent stop sits on the entry price.
         if(PositionGetDouble(POSITION_SL) <= 0.0)
         {
            double originalRisk = VirtualRiskPoints(ticket);
            double basis = originalRisk > 0.0
               ? entry + (isBuy ? -1.0 : 1.0) * originalRisk * GetSymbolPoint(symbol)
               : virtualStop;
            if(MathAbs(entry - basis) > tolerance)
            {
               if(BackstopRetryDue(ticket))
                  EnsureBackstop(symbol, ticket, type, entry, basis, false);
            }
            else
               NoticeUnprotectableOnce(ticket, symbol);
            if(!PositionSelectByTicket(ticket)) continue;
         }
         MqlTick tick;
         if(!SymbolInfoTick(symbol, tick))
         {
            if(NoTickWarningDue(symbol))
               PrintFormat("[RX] No tick for silent stop on %s", symbol);
            continue;
         }
         if(VirtualStopReached(type, virtualStop, tick.bid, tick.ask))
         {
            if(Trade.PositionClose(ticket))
            {
               PrintFormat("[RX] Silent stop %.5f closed %s #%I64u", virtualStop, symbol, ticket);
               LogToFile("[SILENT_STOP] " + symbol + " #" + IntegerToString(ticket));
               ForgetVirtualStop(ticket);
            }
            else
               PrintFormat("[RX] Silent stop close FAIL %s #%I64u ret=%d (%s)", symbol, ticket,
                           Trade.ResultRetcode(), Trade.ResultRetcodeDescription());
            continue;
         }

         if(AutoBE_3R_Enable)
         {
            double point = GetSymbolPoint(symbol);
            double riskPoints = VirtualRiskPoints(ticket);
            if(riskPoints <= 0.0 && point > 0.0)
               riskPoints = MathAbs(entry - virtualStop) / point;
            double current = type == POSITION_TYPE_BUY ? tick.bid : tick.ask;
            // Only a move in the trade's favour counts toward break-even.
            double movePoints = point > 0.0
               ? (type == POSITION_TYPE_BUY ? current - entry : entry - current) / point
               : 0.0;
            if(riskPoints > 0.0 && movePoints >= AutoBE_Multiplier * riskPoints - 0.5)
            {
               if(!ReceiverPositionAtBreakEven(isBuy, entry, virtualStop, tolerance))
               {
                  RememberVirtualStop(ticket, type, entry, 0.0);
                  g_publishStateNow = true;
                  PrintFormat("[AUTO-BE] Silent stop moved to entry %.5f for %s #%I64u", entry, symbol, ticket);
                  LogToFile("[AUTO-BE] Silent BE: " + symbol + " #" + IntegerToString(ticket));
               }
            }
         }
         continue;
      }

      double stopLoss = PositionGetDouble(POSITION_SL);
      if(CloseIfWidened(symbol, ticket, type, stopLoss)) continue;
      if(!AutoBE_3R_Enable)
      {
         if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
         continue;
      }

      double point = GetSymbolPoint(symbol);
      double stopDistance = stopLoss > 0.0 ? MathAbs(entry - stopLoss) / point : 0.0;
      double riskPoints = stopDistance > 0.0 ? stopDistance : ResolveStopPoints(symbol, false);

      if(riskPoints <= 0.0)
      {
         if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
         continue;
      }

      MqlTick tick;
      if(!SymbolInfoTick(symbol, tick))
      {
         PrintFormat("[AUTO-BE] WARN: Cannot get tick data for %s", symbol);
         if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
         continue;
      }
      double current = type == POSITION_TYPE_BUY ? tick.bid : tick.ask;
      double movePoints = (type == POSITION_TYPE_BUY ? current - entry : entry - current) / point;
      if(movePoints >= AutoBE_Multiplier * riskPoints - 0.5)
      {
         double desiredStop = entry;
         double tolerance = 0.5 * point;
         bool needsModify = stopLoss == 0.0 ||
            (type == POSITION_TYPE_BUY && stopLoss < desiredStop - tolerance) ||
            (type == POSITION_TYPE_SELL && stopLoss > desiredStop + tolerance);
         if(needsModify)
         {
            double takeProfit = PositionGetDouble(POSITION_TP);
            if(Trade.PositionModify(ticket, desiredStop, takeProfit))
            {
               PrintFormat("[AUTO-BE] SUCCESS: Moved SL to BE at +%.1fR (%.5f) for %s #%I64u",
                           AutoBE_Multiplier, desiredStop, symbol, ticket);
               ObserveOrSetBoundary(ticket, type, desiredStop);
               g_publishStateNow = true;
               LogToFile("[AUTO-BE] Moved to BE: " + symbol + " #" + IntegerToString(ticket));
            }
            else
            {
               PrintFormat("[AUTO-BE] FAILED: Modify error %d (%s) for %s #%I64u",
                           Trade.ResultRetcode(), Trade.ResultRetcodeDescription(), symbol, ticket);
            }
         }
      }
      if(stopLoss > 0.0) ObserveOrSetBoundary(ticket, type, stopLoss);
   }
}

void CheckAndRepairState()
{
   if(!EnableSelfHealing) return;
   static datetime lastCheck = 0;
   if(TimeCurrent() - lastCheck < 3600) return;
   lastCheck = TimeCurrent();

   int removed = 0;
   for(int index = ArraySize(gTickets) - 1; index >= 0; index--)
   {
      if(PositionSelectByTicket(gTickets[index])) continue;
      ForgetVirtualStop(gTickets[index]);
      removed++;
   }
   if(removed > 0)
   {
      PrintFormat("[SELF-HEAL] Removed %d orphaned anti-widen entries", removed);
      LogToFile("[SELF-HEAL] Cleaned " + IntegerToString(removed) + " orphaned entries");
   }

   if(AccountInfoDouble(ACCOUNT_EQUITY) <= 0.0)
   {
      Print("[SELF-HEAL] CRITICAL: Zero equity detected!");
      LogToFile("[SELF-HEAL] Zero equity emergency");
   }
}

#endif