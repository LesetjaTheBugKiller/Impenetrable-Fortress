#ifndef RECEIVER_POSITION_MANAGEMENT_MQH
#define RECEIVER_POSITION_MANAGEMENT_MQH

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
   if(!CloseForeignPositions) return;
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
               EnsureBackstop(symbol, ticket, type, entry, basis, false);
            PositionSelectByTicket(ticket);
         }
         MqlTick tick;
         if(!SymbolInfoTick(symbol, tick))
         {
            static datetime lastNoTickWarning = 0;
            if(TimeCurrent() - lastNoTickWarning >= 30)
            {
               PrintFormat("[RX] No tick for silent stop on %s", symbol);
               lastNoTickWarning = TimeCurrent();
            }
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