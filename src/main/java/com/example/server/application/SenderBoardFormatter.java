package com.example.server.application;

import com.example.server.config.ReceiverGateConfig;
import com.example.server.domain.GuardModels.ReceiverGateDecision;
import com.example.server.domain.GuardModels.ReceiverStateSnapshot;
import com.example.server.domain.GuardModels.SenderBoard;
import com.example.server.domain.GuardModels.TradeRisk;

import java.util.List;
import java.util.Locale;

/** Turns the latest receiver facts into the lines drawn on the sender. */
public final class SenderBoardFormatter {
    private SenderBoardFormatter() {}

    public static SenderBoard format(
            ReceiverGateConfig config,
            ReceiverGateDecision decision,
            ReceiverStateSnapshot snapshot,
            List<TradeRisk> risks
    ) {
        boolean open = decision.allowed();
        String gate = open ? "Open" : decision.gate();
        String gateDetail = open ? "New trades are allowed" : decision.reason();
        if (snapshot == null) {
            return new SenderBoard(summary(risks), gate, gateDetail, "blocked",
                    "—", "Waiting for the receiver", "blocked",
                    "—", "—", "—", "—", "—",
                    0.0, config.dailyLossCapPercent(), config.dailyProfitTargetPercent());
        }

        int lossesLeft = Math.max(0, config.maximumLossesPerDay() - snapshot.lossesToday());
        int spikesLeft = Math.max(0, config.maximumSpikesPerDay() - snapshot.spikesToday());
        String lossesTone = lossesLeft == 0 ? "blocked" : lossesLeft <= 2 ? "warn" : "open";
        String cooldown = cooldownText(config, snapshot.secondsSinceLastLoss());
        return new SenderBoard(
                summary(risks),
                gate,
                gateDetail,
                open ? "open" : "blocked",
                lossesLeft + " left",
                snapshot.lossesToday() + " used of " + config.maximumLossesPerDay(),
                lossesTone,
                spikesLeft + " left · " + snapshot.spikesToday() + " of " + config.maximumSpikesPerDay(),
                cooldown,
                dayText(config, snapshot),
                breakEvenText(config, snapshot),
                snapshot.scheduleOpen() ? "Open" : "Closed",
                dayPercent(snapshot),
                config.dailyLossCapPercent(),
                config.dailyProfitTargetPercent()
        );
    }

    static String summary(List<TradeRisk> risks) {
        StringBuilder summary = new StringBuilder();
        for (TradeRisk risk : risks) {
            if (summary.length() > 0) {
                summary.append(" | ");
            }
            summary.append(String.format(Locale.US, "%.1f pts   %s %s   %.2f %s",
                    risk.points(), risk.symbol(), risk.side(), risk.risk(), risk.currency()));
        }
        return summary.toString();
    }

    private static String cooldownText(ReceiverGateConfig config, long secondsSinceLastLoss) {
        long cooldownSeconds = config.cooldownAfterLossSeconds();
        if (cooldownSeconds <= 0 || secondsSinceLastLoss < 0 || secondsSinceLastLoss >= cooldownSeconds) {
            return "Ready";
        }
        return (cooldownSeconds - secondsSinceLastLoss) + "s left";
    }

    private static double dayPercent(ReceiverStateSnapshot snapshot) {
        if (snapshot.dayStartBalance() <= 0) {
            return 0.0;
        }
        return (snapshot.currentBalance() - snapshot.dayStartBalance()) / snapshot.dayStartBalance() * 100.0;
    }

    private static String dayText(ReceiverGateConfig config, ReceiverStateSnapshot snapshot) {
        if (snapshot.dayStartBalance() <= 0) {
            return "—";
        }
        double dropPct = (snapshot.dayStartBalance() - snapshot.currentBalance()) / snapshot.dayStartBalance() * 100.0;
        if (dropPct >= 0) {
            return String.format(Locale.US, "Down %.2f%% of %.1f%% cap", dropPct, config.dailyLossCapPercent());
        }
        return String.format(Locale.US, "Up %.2f%% of %.1f%% target", -dropPct, config.dailyProfitTargetPercent());
    }

    private static String breakEvenText(ReceiverGateConfig config, ReceiverStateSnapshot snapshot) {
        if (!config.requireAllPositionsAtBreakEven()) {
            return "Off";
        }
        return snapshot.allPositionsAtBreakEven() ? "Clear" : "Holding a trade";
    }
}
