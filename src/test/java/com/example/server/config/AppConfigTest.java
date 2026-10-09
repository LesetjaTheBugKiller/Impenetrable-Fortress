package com.example.server.config;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AppConfigTest {

    @Test
    void stripsTrailingCommentsFromEnvValues() {
        assertEquals("55", AppConfig.stripInlineComment("55 // Block after this many losing closes"));
        assertEquals("3.0", AppConfig.stripInlineComment("3.0 # percent"));
        assertEquals("http://127.0.0.1:5001", AppConfig.stripInlineComment("http://127.0.0.1:5001"));
        assertEquals("\"55 // keep\"", AppConfig.stripInlineComment("\"55 // keep\""));
    }

    @Test
    void readsAPercentWinRateAsAFraction() {
        assertEquals(0.5, AppConfig.fractionFromSetting(50));
        assertEquals(0.5, AppConfig.fractionFromSetting(0.5));
        assertEquals(0.0, AppConfig.fractionFromSetting(0));
    }

    @Test
    void acceptsWholeNumbersWrittenWithADecimalPoint() {
        assertEquals(55, EnvNumbers.wholeInt("RECEIVER_MAX_LOSSES_PER_DAY", "55.0", 5));
        assertEquals(0, EnvNumbers.wholeInt("LICHESS_MIN_GAMES", "0.0", 1));
        assertEquals(1000L, EnvNumbers.wholeLong("RECEIVER_STATE_MAX_AGE_MS", "1000.0", 5000));
        assertEquals(0.2, EnvNumbers.decimal("RECEIVER_COOLDOWN_AFTER_LOSS_MIN", "0.2", 5));
        assertFalse(EnvNumbers.bool("FAIL_OPEN_ON_LICHESS_ERROR", "0.0", true));
        assertTrue(EnvNumbers.bool("ENFORCE_LICHESS", "1.0", false));
    }

    @Test
    void usesTheDefaultOnlyWhenTheSettingIsBlank() {
        assertEquals(1, EnvNumbers.wholeInt("LICHESS_MIN_GAMES", "  ", 1));
        assertEquals(5.0, EnvNumbers.decimal("RECEIVER_DAILY_LOSS_CAP_PCT", "", 5.0));
        assertTrue(EnvNumbers.bool("RECEIVER_REQUIRE_ALL_AT_BE", "", true));
    }

    @Test
    void rejectsATypedValueInsteadOfSubstitutingTheDefault() {
        assertThrows(IllegalArgumentException.class,
                () -> EnvNumbers.wholeInt("LICHESS_MIN_GAMES", "0.2", 1));
        assertThrows(IllegalArgumentException.class,
                () -> EnvNumbers.wholeLong("RECEIVER_STATE_MAX_AGE_MS", "1.5", 5000));
        assertThrows(IllegalArgumentException.class,
                () -> EnvNumbers.decimal("LICHESS_MIN_WIN_RATE", "none", 0.0));
        assertThrows(IllegalArgumentException.class,
                () -> EnvNumbers.bool("FAIL_OPEN_ON_LICHESS_ERROR", "maybe", false));
    }
}
