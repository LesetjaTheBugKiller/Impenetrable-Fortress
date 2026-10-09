package com.example.server.config;

/** Server-owned thresholds for the receiver-reported trading gates. */
public record ReceiverGateConfig(
        double dailyLossCapPercent,
        double dailyProfitTargetPercent,
        int maximumLossesPerDay,
        int maximumSpikesPerDay,
        double cooldownAfterLossMinutes,
        boolean requireAllPositionsAtBreakEven,
        long maximumSnapshotAgeMillis
) {
    public ReceiverGateConfig {
        if (!Double.isFinite(dailyLossCapPercent) || dailyLossCapPercent <= 0
                || !Double.isFinite(dailyProfitTargetPercent) || dailyProfitTargetPercent <= 0
                || maximumLossesPerDay <= 0 || maximumSpikesPerDay <= 0
                || !Double.isFinite(cooldownAfterLossMinutes) || cooldownAfterLossMinutes < 0
                || maximumSnapshotAgeMillis <= 0) {
            throw new IllegalArgumentException("Receiver gate settings must be positive and finite (cooldown may be zero)");
        }
    }

    /** Cooldown length in whole seconds. Fractional minutes such as 0.2 become 12 seconds. */
    public long cooldownAfterLossSeconds() {
        return Math.round(cooldownAfterLossMinutes * 60.0);
    }

    /**
     * Expected closed profit for the planned day, as a percent of the day-start balance.
     * Four trades. Half of them win. A win pays four times the risk per trade.
     * At 1 percent risk that is 6 percent for the day.
     */
    public static double expectedDailyProfitPercent() {
        return expectedDailyProfitPercent(1.0);
    }

    public static double expectedDailyProfitPercent(double riskPercent) {
        int tradesPerDay = 4;
        double winRate = 0.5;
        double rewardMultiple = 4.0;
        double risk = Double.isFinite(riskPercent) && riskPercent > 0.0 ? riskPercent : 1.0;
        double perTrade = winRate * rewardMultiple * risk - (1.0 - winRate) * risk;
        return tradesPerDay * perTrade;
    }

    public static ReceiverGateConfig load() {
        return new ReceiverGateConfig(
                getDouble("RECEIVER_DAILY_LOSS_CAP_PCT", 3.0),
                getDouble("RECEIVER_DAILY_PROFIT_TARGET_PCT", expectedDailyProfitPercent()),
                getInt("RECEIVER_MAX_LOSSES_PER_DAY", 5),
                getInt("RECEIVER_MAX_SPIKES_PER_DAY", 2),
                getDouble("RECEIVER_COOLDOWN_AFTER_LOSS_MIN", 5),
                getBoolean("RECEIVER_REQUIRE_ALL_AT_BE", true),
                getLong("RECEIVER_STATE_MAX_AGE_MS", 5000)
        );
    }

    private static String value(String key, String defaultValue) {
        String envValue = System.getenv(key);
        if (envValue != null && !envValue.isBlank()) return envValue.trim();
        return AppConfig.dotEnvValue(key).orElse(defaultValue).trim();
    }

    private static double getDouble(String key, double fallback) {
        return EnvNumbers.decimal(key, value(key, ""), fallback);
    }

    private static int getInt(String key, int fallback) {
        return EnvNumbers.wholeInt(key, value(key, ""), fallback);
    }

    private static long getLong(String key, long fallback) {
        return EnvNumbers.wholeLong(key, value(key, ""), fallback);
    }

    private static boolean getBoolean(String key, boolean fallback) {
        return EnvNumbers.bool(key, value(key, ""), fallback);
    }
}