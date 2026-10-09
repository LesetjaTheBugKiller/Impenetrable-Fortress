package com.example.server.config;

import java.util.Locale;

/** Reads numeric and boolean settings without substituting the default for a typed value. */
final class EnvNumbers {
    private EnvNumbers() {}

    static double decimal(String key, String raw, double fallback) {
        if (raw == null || raw.isBlank()) {
            return fallback;
        }
        try {
            double value = Double.parseDouble(raw.trim());
            if (!Double.isFinite(value)) {
                throw new NumberFormatException(raw);
            }
            return value;
        } catch (NumberFormatException e) {
            throw invalid(key, raw, "a number");
        }
    }

    static int wholeInt(String key, String raw, int fallback) {
        if (raw == null || raw.isBlank()) {
            return fallback;
        }
        try {
            return (int) wholeLong(raw.trim(), Integer.MIN_VALUE, Integer.MAX_VALUE);
        } catch (NumberFormatException e) {
            throw invalid(key, raw, "a whole number");
        }
    }

    static long wholeLong(String key, String raw, long fallback) {
        if (raw == null || raw.isBlank()) {
            return fallback;
        }
        try {
            return wholeLong(raw.trim(), Long.MIN_VALUE, Long.MAX_VALUE);
        } catch (NumberFormatException e) {
            throw invalid(key, raw, "a whole number");
        }
    }

    static boolean bool(String key, String raw, boolean fallback) {
        if (raw == null || raw.isBlank()) {
            return fallback;
        }
        String value = raw.trim().toLowerCase(Locale.ROOT);
        if (value.equals("1") || value.equals("true") || value.equals("yes")) {
            return true;
        }
        if (value.equals("0") || value.equals("false") || value.equals("no")) {
            return false;
        }
        try {
            long whole = wholeLong(value, Long.MIN_VALUE, Long.MAX_VALUE);
            if (whole == 0) {
                return false;
            }
            if (whole == 1) {
                return true;
            }
        } catch (NumberFormatException ignored) {
            // Fall through to the setting error below.
        }
        throw invalid(key, raw, "true or false");
    }

    private static long wholeLong(String text, long minimum, long maximum) {
        try {
            long parsed = Long.parseLong(text);
            if (parsed < minimum || parsed > maximum) {
                throw new NumberFormatException(text);
            }
            return parsed;
        } catch (NumberFormatException ignored) {
            double value = Double.parseDouble(text);
            long rounded = Math.round(value);
            if (!Double.isFinite(value) || value < minimum || value > maximum || Math.abs(value - rounded) > 1e-9) {
                throw new NumberFormatException(text);
            }
            return rounded;
        }
    }

    private static IllegalArgumentException invalid(String key, String raw, String expected) {
        return new IllegalArgumentException(key + " must be " + expected + " but was \"" + raw.trim() + "\"");
    }
}
