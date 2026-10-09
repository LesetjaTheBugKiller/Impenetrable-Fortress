#property strict
#include "../../Include/ReceiverProtocol.mqh"
#include "../../Include/ReceiverDecisions.mqh"

int g_assertions = 0;
int g_failures = 0;

void AssertTrue(const bool condition, const string name)
{
   g_assertions++;
   if(condition)
      Print("PASS: ", name);
   else
   {
      g_failures++;
      Print("FAIL: ", name);
   }
}

void TestParsesSignalAndBuildsFallbackId()
{
   ReceiverSignal signal;
   string json = "{ \"ok\" : true, \"empty\" : false, \"side\" : \"sell\", \"symbol\" : \" GER40Cash \", \"lots\" : 0.10, \"ts\" : 1712345678 }";
   bool parsed = ReceiverParseSignal(json, signal);

   AssertTrue(parsed, "parse valid signal with JSON whitespace");
   AssertTrue(signal.has_signal, "valid response has a signal");
   AssertTrue(signal.side == "SELL", "normalize side to uppercase");
   AssertTrue(signal.symbol == "GER40Cash", "trim symbol whitespace");
   AssertTrue(MathAbs(signal.lots - 0.10) < 0.000001, "parse lots");
   AssertTrue(signal.timestamp == 1712345678, "parse timestamp");
   AssertTrue(signal.id == "1712345678|SELL|GER40Cash|0.10", "build fallback dedupe ID");
}

void TestUsesExplicitIdAndCreatedTimeFallback()
{
   ReceiverSignal signal;
   string json = "{\"ok\":true,\"empty\":false,\"id\":\"signal-42\",\"side\":\"BUY\",\"symbol\":\"EURUSD\",\"lots\":0.2,\"created_time\":1712345678}";
   bool parsed = ReceiverParseSignal(json, signal);

   AssertTrue(parsed, "parse signal using created_time fallback");
   AssertTrue(signal.timestamp == 1712345678, "use created_time as timestamp fallback");
   AssertTrue(signal.id == "signal-42", "preserve explicit signal ID");
}

void TestHandlesEmptyAndInvalidResponses()
{
   ReceiverSignal signal;
   AssertTrue(ReceiverJsonTrue("{ \"ok\" : true }", "ok"), "parse boolean with surrounding whitespace");
   bool emptyParsed = ReceiverParseSignal("{\"ok\":true,\"empty\":true}", signal);
   AssertTrue(emptyParsed && !signal.has_signal, "accept empty signal response");

   bool badSideParsed = ReceiverParseSignal("{\"ok\":true,\"empty\":false,\"side\":\"HOLD\",\"symbol\":\"EURUSD\",\"lots\":1,\"ts\":1712345678}", signal);
   AssertTrue(!badSideParsed, "reject unsupported side");

   bool badLotsParsed = ReceiverParseSignal("{\"ok\":true,\"empty\":false,\"side\":\"BUY\",\"symbol\":\"EURUSD\",\"lots\":0,\"ts\":1712345678}", signal);
   AssertTrue(!badLotsParsed, "reject non-positive lots");

   bool deniedParsed = ReceiverParseSignal("{\"ok\":false,\"reason\":\"AUTH\"}", signal);
   AssertTrue(!deniedParsed, "reject unsuccessful server response");
}

void TestSignalAgeBoundaries()
{
   ReceiverSignal signal;
   signal.timestamp = 1000;
   long age = 0;

   AssertTrue(!ReceiverSignalExpired(signal, 1005, 5, age) && age == 5, "signal at max age remains valid");
   AssertTrue(ReceiverSignalExpired(signal, 1006, 5, age) && age == 6, "signal over max age expires");

   signal.timestamp = 0;
   AssertTrue(ReceiverSignalExpired(signal, 1000, 5, age), "missing timestamp expires safely");
}

void TestReceiverStateJsonContract()
{
   string json = ReceiverBuildStateJson("demo-7", 10000.0, 9700.0, -125.5, 4, 1, 120, true, false);
   AssertTrue(ReceiverJsonString(json, "receiverId") == "demo-7", "state JSON includes receiver ID");
   AssertTrue(ReceiverJsonNumber(json, "dayStartBalance") == "10000.00", "state JSON includes day-start balance");
   AssertTrue(ReceiverJsonNumber(json, "currentBalance") == "9700.00", "state JSON includes current balance");
   AssertTrue(ReceiverJsonNumber(json, "dailyClosedNet") == "-125.50", "state JSON includes closed daily net");
   AssertTrue(ReceiverJsonNumber(json, "lossesToday") == "4", "state JSON includes daily losses");
   AssertTrue(ReceiverJsonNumber(json, "spikesToday") == "1", "state JSON includes daily spikes");
   AssertTrue(ReceiverJsonNumber(json, "secondsSinceLastLoss") == "120", "state JSON includes elapsed cooldown seconds");
   AssertTrue(ReceiverJsonTrue(json, "scheduleOpen"), "state JSON includes open schedule boolean");
   AssertTrue(!ReceiverJsonTrue(json, "allPositionsAtBreakEven"), "state JSON includes break-even boolean");
}

void TestLossDealAccountingAndDeduplication()
{
   AssertTrue(ReceiverDealBelongsToUs(20258008, 20258008, false), "matching magic belongs to the receiver");
   AssertTrue(ReceiverDealBelongsToUs(0, 20258008, true), "a tracked position still belongs when magic is missing");
   AssertTrue(!ReceiverDealBelongsToUs(0, 20258008, false), "a foreign close does not belong");

   ReceiverDailyState daily;
   daily.day_anchor = 0;
   daily.start_balance = 10000.0;
   daily.closed_net = 0.0;
   daily.losses = 0;
   daily.spikes = 0;
   daily.last_loss_time = 0;
   bool wasLoss = false;
   bool wasSpike = false;
   ulong processed_tickets[];
   ulong losing_tickets[5] = {104, 102, 103, 101, 105};

   for(int index = 0; index < 5; index++)
   {
      ulong ticket = losing_tickets[index];
      if(ReceiverMarkDealOnce(processed_tickets, ticket))
         ReceiverApplyClosedDeal(daily, true, true, true, -10.0, 1000.0,
                                 100 + index, 1e-6, wasLoss, wasSpike);
   }
   AssertTrue(daily.losses == 5, "five out-of-order closing losses are counted");
   AssertTrue(!ReceiverMarkDealOnce(processed_tickets, 102), "duplicate closing deal notification is ignored");

   int lossesBeforeIgnored = daily.losses;
   ReceiverApplyClosedDeal(daily, false, true, true, -50.0, 1000.0, 106, 1e-6, wasLoss, wasSpike);
   ReceiverApplyClosedDeal(daily, true, false, true, -50.0, 1000.0, 107, 1e-6, wasLoss, wasSpike);
   ReceiverApplyClosedDeal(daily, true, true, true, -0.000001, 1000.0, 108, 1e-6, wasLoss, wasSpike);
   ReceiverApplyClosedDeal(daily, true, true, false, -50.0, 1000.0, 109, 1e-6, wasLoss, wasSpike);
   AssertTrue(daily.losses == lossesBeforeIgnored, "foreign, non-closing, tolerance, and invalid deals do not count");
}

void TestStopSelectionAndPrice()
{
   double points = ReceiverSelectStopPoints(true, false, false, false, true, 2.0, 25.0, 150.0, 80.0, 100.0, 60.0, 45.0);
   AssertTrue(points == 50.0, "forex stop uses spread multiple");

   points = ReceiverSelectStopPoints(true, false, false, false, true, 0.0, 25.0, 150.0, 80.0, 100.0, 60.0, 45.0);
   AssertTrue(points == 50.0, "invalid forex spread settings use fallback");

   points = ReceiverSelectStopPoints(false, true, true, false, true, 2.0, 25.0, 150.0, 80.0, 100.0, 60.0, 45.0);
   AssertTrue(points == 150.0, "gold-specific stop takes commodity priority");

   double stop = 0.0;
   AssertTrue(ReceiverCalculateStopPrice(true, 1.1000, 50.0, 0.0001, stop) && MathAbs(stop - 1.0950) < 1e-8,
              "buy stop is below entry");
   AssertTrue(ReceiverCalculateStopPrice(false, 1.1000, 50.0, 0.0001, stop) && MathAbs(stop - 1.1050) < 1e-8,
              "sell stop is above entry");
   AssertTrue(!ReceiverCalculateStopPrice(true, 1.1, 50.0, 0.0, stop), "reject invalid point size");

   AssertTrue(MathAbs(ReceiverTakeProfitPrice(true, 1.1000, 1.0950, 4.0) - 1.1200) < 1e-8,
              "buy target is four times the stop distance above entry");
   AssertTrue(MathAbs(ReceiverTakeProfitPrice(false, 1.1000, 1.1050, 4.0) - 1.0800) < 1e-8,
              "sell target is four times the stop distance below entry");
   AssertTrue(ReceiverTakeProfitPrice(true, 1.1, 1.1, 4.0) == 0.0, "a stop on the entry has no target");
   AssertTrue(ReceiverTakeProfitPrice(true, 1.1, 1.09, 0.0) == 0.0, "a zero reward has no target");

   AssertTrue(MathAbs(ReceiverExpectedDayTargetPercent(1.0, 0.5, 4.0, 4) - 6.0) < 1e-8,
              "four 1% trades at 50% and 1:4 are a 6% day");
   AssertTrue(MathAbs(ReceiverExpectedDayTargetPercent(0.5, 0.5, 4.0, 4) - 3.0) < 1e-8,
              "halving the risk halves the day target");
}

void TestVolatilityScaledStop()
{
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 10.0, 5.0) - 200.0) < 1e-8,
              "doubled ATR doubles the configured stop");
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 5.025, 5.0) - 100.5) < 1e-6,
              "half-percent ATR increase widens the stop by half a percent");
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 2.5, 5.0) - 50.0) < 1e-8,
              "halved ATR halves the configured stop");
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 5.0, 5.0) - 100.0) < 1e-8,
              "matching ATR keeps the configured stop");
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 0.0, 5.0) - 100.0) < 1e-8,
              "missing live ATR keeps the configured stop");
   AssertTrue(MathAbs(ReceiverScaleStopPoints(100.0, 8.0, 0.0) - 100.0) < 1e-8,
              "missing reference ATR keeps the configured stop");
   AssertTrue(ReceiverScaleStopPoints(-10.0, 8.0, 5.0) == 0.0, "invalid configured stop stays invalid");

   AssertTrue(ReceiverReferenceAtr(true, false, false, false, true, 0.0008, 15.0, 8.0, 40.0, 3.0) == 0.0008,
              "forex uses the forex reference ATR");
   AssertTrue(ReceiverReferenceAtr(false, true, true, false, true, 0.0008, 15.0, 8.0, 40.0, 3.0) == 15.0,
              "gold uses its own reference ATR");
   AssertTrue(ReceiverReferenceAtr(false, true, true, false, true, 0.0008, 0.0, 8.0, 40.0, 3.0) == 0.0,
              "gold with no reference does not borrow the commodity reference");
   AssertTrue(ReceiverReferenceAtr(false, true, true, false, false, 0.0008, 15.0, 8.0, 40.0, 3.0) == 8.0,
              "gold without specific settings uses the commodity reference");
   AssertTrue(ReceiverReferenceAtr(false, false, false, true, true, 0.0008, 15.0, 8.0, 40.0, 3.0) == 40.0,
              "index uses the index reference ATR");
   AssertTrue(ReceiverReferenceAtr(false, false, false, false, true, 0.0008, 15.0, 8.0, 40.0, 3.0) == 3.0,
              "other symbols use the other reference ATR");

   AssertTrue(MathAbs(ReceiverApplyMinimumStopPoints(40.0, true, 50.0) - 50.0) < 1e-8,
              "minimum stop raises a tighter scaled distance");
   AssertTrue(MathAbs(ReceiverApplyMinimumStopPoints(80.0, true, 50.0) - 80.0) < 1e-8,
              "minimum stop leaves a wider scaled distance");
   AssertTrue(MathAbs(ReceiverApplyMinimumStopPoints(40.0, false, 50.0) - 40.0) < 1e-8,
              "disabled minimum leaves the scaled distance");

   AssertTrue(MathAbs(ReceiverValuePerPoint(1.0, 0.00001, 0.00001) - 1.0) < 1e-8,
              "value per point equals tick value when tick and point match");
   AssertTrue(MathAbs(ReceiverValuePerPoint(1.0, 1.0, 0.01) - 0.01) < 1e-8,
              "value per point scales a coarse tick down to one point");
   AssertTrue(ReceiverValuePerPoint(1.0, 0.0, 0.01) == 0.0, "missing tick size has no point value");
   AssertTrue(MathAbs(ReceiverCashRisk(0.20, 50.0, 10.0) - 100.0) < 1e-8,
              "cash risk is volume times stop points times value per point");
   AssertTrue(ReceiverCashRisk(0.10, 100.0, 1.0) == ReceiverCashRisk(1.00, 50.0, 0.2),
              "different symbols with the same cash risk compare as equal");
   AssertTrue(ReceiverCashRisk(0.0, 50.0, 10.0) == 0.0, "no volume has no cash risk");
}

void TestSilentStopGates()
{
   double hidden = ReceiverStopReportedToGates(90.0, 40.0);
   AssertTrue(MathAbs(hidden - 90.0) < 1e-8, "a wide ticket stop does not replace the hidden stop");
   AssertTrue(!ReceiverPositionAtBreakEven(true, 100.0, hidden, 0.5),
              "break-even stays closed while the hidden stop is below entry");
   hidden = ReceiverStopReportedToGates(100.0, 40.0);
   AssertTrue(ReceiverPositionAtBreakEven(true, 100.0, hidden, 0.5),
              "break-even opens from the hidden stop while the wide ticket stop remains");
   double legacy = ReceiverStopReportedToGates(0.0, 100.0);
   AssertTrue(ReceiverPositionAtBreakEven(true, 100.0, legacy, 0.5),
              "a position with no hidden stop still uses its ticket stop");

   AssertTrue(!ReceiverPositionAtBreakEven(true, 100.0, 0.0, 0.5),
              "a position with no stop is not at break-even");
   AssertTrue(!ReceiverPositionAtBreakEven(true, 100.0, 90.0, 0.5),
              "a buy stop below entry keeps the break-even gate closed");
   AssertTrue(ReceiverPositionAtBreakEven(true, 100.0, 100.0, 0.5),
              "a buy stop at entry opens the break-even gate");
   AssertTrue(!ReceiverPositionAtBreakEven(false, 100.0, 110.0, 0.5),
              "a sell stop above entry keeps the break-even gate closed");
   AssertTrue(ReceiverPositionAtBreakEven(false, 100.0, 100.0, 0.5),
              "a sell stop at entry opens the break-even gate");

   AssertTrue(ReceiverAllManagedPositionsAtBreakEven(0, false),
              "no managed positions leaves the break-even gate open");
   AssertTrue(!ReceiverAllManagedPositionsAtBreakEven(1, false),
              "a managed stop below entry closes the break-even gate");
   AssertTrue(!ReceiverAllManagedPositionsAtBreakEven(0, true),
              "an unreadable managed position keeps the break-even gate closed");
   AssertTrue(MathAbs(ReceiverBreakEvenTolerance(0.1, 0.1) - 0.1) < 1e-12,
              "break-even tolerance is at least one tick");
}

void TestLotRiskAndMarginPlanning()
{
   ReceiverLotPlan plan = ReceiverCalculateLotPlan(10000.0, 1.0, 50.0, 10.0, 0.01, 100.0, 0.01, 100.0);
   AssertTrue(plan.valid && MathAbs(plan.risk_budget - 100.0) < 1e-8, "risk budget uses configured equity percentage");
   AssertTrue(MathAbs(plan.raw_lots - 0.2) < 1e-8 && MathAbs(plan.lots - 0.2) < 1e-8,
              "calculate and normalize risk-based lots");

   plan = ReceiverCalculateLotPlan(100.0, 0.1, 500.0, 10.0, 0.01, 100.0, 0.01, 100.0);
   AssertTrue(!plan.valid && plan.failure == "risk budget below minimum volume",
              "reject minimum lot when it would exceed risk budget");

   double snapped = 0.0;
   AssertTrue(ReceiverSnapLots(0.127, 0.01, 10.0, 0.01, snapped) && MathAbs(snapped - 0.12) < 1e-8,
              "snap volumes down to broker step");
   AssertTrue(ReceiverSnapLots(0.001, 0.01, 10.0, 0.01, snapped) && MathAbs(snapped - 0.01) < 1e-8,
              "floor subminimum volume at broker minimum as existing receiver does");

   double adjusted = 0.0;
   AssertTrue(ReceiverAdjustLotsForMargin(1.0, 1000.0, 500.0, 0.01, 10.0, 0.01, adjusted) &&
              MathAbs(adjusted - 0.5) < 1e-8, "shrink and normalize lot size to available margin");
   AssertTrue(!ReceiverAdjustLotsForMargin(1.0, 1000.0, 0.0, 0.01, 10.0, 0.01, adjusted),
              "reject margin adjustment when no free margin remains");
   AssertTrue(!ReceiverAdjustLotsForMargin(1.0, 1000.0, 5.0, 0.01, 10.0, 0.01, adjusted),
              "reject minimum lot when margin reduction would round volume back up");
}

void TestDailyResetTransitions()
{
   ReceiverDailyState state;
   state.day_anchor = 100;
   state.start_balance = 10000.0;
   state.closed_net = 75.0;
   state.losses = 1;
   state.spikes = 1;
   state.last_loss_time = 150;

   AssertTrue(!ReceiverApplyDailyReset(state, 100, 11000.0), "same-day update does not reset state");
   AssertTrue(state.closed_net == 75.0 && state.losses == 1, "same-day state remains unchanged");

   AssertTrue(!ReceiverShouldResetForAccount(false, false, 0, 200),
              "a terminal with no saved day does not reset");
   AssertTrue(ReceiverShouldResetForAccount(true, false, 0, 200),
              "a saved day with no account number is reset once");
   AssertTrue(ReceiverShouldResetForAccount(true, true, 100, 200),
              "a different account replaces the saved day-start balance");
   AssertTrue(!ReceiverShouldResetForAccount(true, true, 200, 200),
              "the same account keeps today's baseline");

   AssertTrue(ReceiverApplyDailyReset(state, 200, 12000.0), "new day triggers reset");
   AssertTrue(state.day_anchor == 200 && state.start_balance == 12000.0, "new day sets anchor and balance");
   AssertTrue(state.closed_net == 0.0 && state.losses == 0 && state.spikes == 0 && state.last_loss_time == 0,
              "new day clears daily counters");
}

void OnStart()
{
   TestParsesSignalAndBuildsFallbackId();
   TestUsesExplicitIdAndCreatedTimeFallback();
   TestHandlesEmptyAndInvalidResponses();
   TestSignalAgeBoundaries();
   TestReceiverStateJsonContract();
   TestLossDealAccountingAndDeduplication();
   TestStopSelectionAndPrice();
   TestVolatilityScaledStop();
   TestSilentStopGates();
   TestLotRiskAndMarginPlanning();
   TestDailyResetTransitions();

   PrintFormat("ReceiverProtocolTests: %d assertions, %d failures", g_assertions, g_failures);
}