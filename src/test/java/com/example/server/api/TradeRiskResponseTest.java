package com.example.server.api;

import com.example.server.domain.GuardModels.SenderBoard;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class TradeRiskResponseTest {
    @Test
    void boardJsonUsesTheNamesTheSenderReads() {
        var response = LichessGuardResponses.senderBoard(new SenderBoard(
                "150.4 pts   XAUUSD BUY   48.20 USD",
                "Open",
                "New trades are allowed",
                "open",
                "52 left",
                "3 used of 55",
                "open",
                "2 left · 0 of 2",
                "Ready",
                "Down 2.00% of 3.0%",
                "Clear",
                "Open",
                -2.0,
                3.0,
                2.0
        ));

        assertEquals("52 left", response.get("losses"));
        assertEquals("150.4 pts   XAUUSD BUY   48.20 USD", response.get("summary"));
        assertEquals("Open", response.get("gate"));
        assertEquals(-2.0, (Double) response.get("dayPct"));
        assertEquals(3.0, (Double) response.get("lossCapPct"));
        assertEquals(2.0, (Double) response.get("targetPct"));
    }
}
