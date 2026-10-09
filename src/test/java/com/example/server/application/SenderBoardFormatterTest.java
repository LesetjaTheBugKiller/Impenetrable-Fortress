package com.example.server.application;

import com.example.server.config.ReceiverGateConfig;
import com.example.server.domain.GuardModels.ReceiverGateDecision;
import com.example.server.domain.GuardModels.ReceiverStateSnapshot;
import com.example.server.domain.GuardModels.SenderBoard;
import com.example.server.domain.GuardModels.TradeRisk;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class SenderBoardFormatterTest {
    private static final ReceiverGateConfig CONFIG = new ReceiverGateConfig(3, 6, 55, 2, 0.2, true, 5000);

    @Test
    void showsLossesLeftAndPutsPointsBeforeTheMoney() {
        SenderBoard board = SenderBoardFormatter.format(
                CONFIG,
                new ReceiverGateDecision(true, "", "", 100),
                snapshot(3, 0, -1, true, true, 10000, 9800),
                List.of(new TradeRisk("XAUUSD", "BUY", 150.4, 48.2, "USD", 1))
        );

        assertEquals("Open", board.gate());
        assertEquals("52 left", board.losses());
        assertEquals("3 used of 55", board.lossesDetail());
        assertEquals("open", board.lossesTone());
        assertEquals("2 left · 0 of 2", board.spikes());
        assertEquals("Ready", board.cooldown());
        assertEquals("Down 2.00% of 3.0% cap", board.day());
        assertEquals(-2.0, board.dayPercent(), 1e-9);
        assertEquals(3.0, board.lossCapPercent(), 1e-9);
        assertEquals(6.0, board.targetPercent(), 1e-9);
        assertEquals("Clear", board.breakEven());
        assertEquals("150.4 pts   XAUUSD BUY   48.20 USD", board.summary());
    }

    @Test
    void warnsWhenTwoLossesRemainAndCountsCooldown() {
        SenderBoard board = SenderBoardFormatter.format(
                CONFIG,
                new ReceiverGateDecision(false, "Cooldown After Loss", "2 seconds remaining", 100),
                snapshot(53, 1, 10, false, true, 10000, 10100),
                List.of()
        );

        assertEquals("Cooldown After Loss", board.gate());
        assertEquals("blocked", board.gateTone());
        assertEquals("2 left", board.losses());
        assertEquals("warn", board.lossesTone());
        assertEquals("1 left · 1 of 2", board.spikes());
        assertEquals("2s left", board.cooldown());
        assertEquals("Up 1.00% of 6.0% target", board.day());
        assertEquals(1.0, board.dayPercent(), 1e-9);
        assertEquals("Holding a trade", board.breakEven());
        assertEquals("", board.summary());
    }

    private static ReceiverStateSnapshot snapshot(
            int losses, int spikes, long secondsSinceLastLoss, boolean atBreakEven, boolean scheduleOpen,
            double start, double current
    ) {
        return new ReceiverStateSnapshot("receiver", start, current, 0, losses, spikes, secondsSinceLastLoss, scheduleOpen, atBreakEven);
    }
}
