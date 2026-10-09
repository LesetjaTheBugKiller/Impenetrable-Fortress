package com.example.server.integration.lichess;

import com.example.server.config.AppConfig;
import com.example.server.domain.GuardModels.LichessStats;
import com.example.server.domain.LichessGateway;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/** HTTP adapter for the Lichess account and recent-games APIs. */
public class LichessClient implements LichessGateway {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final long USERNAME_CACHE_MILLIS = Duration.ofHours(1).toMillis();
    private static final long ACCOUNT_REUSE_MILLIS = Duration.ofSeconds(5).toMillis();
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(15);
    private static final String DEFAULT_API_BASE = "https://lichess.org";

    private final AppConfig config;
    private final HttpClient httpClient;
    private final String apiBase;
    private volatile CachedUsername cachedUsername;
    private volatile CachedAccount cachedAccount;

    public LichessClient(AppConfig config) {
        this(config, HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build(), DEFAULT_API_BASE);
    }

    LichessClient(AppConfig config, HttpClient httpClient) {
        this(config, httpClient, DEFAULT_API_BASE);
    }

    LichessClient(AppConfig config, HttpClient httpClient, String apiBase) {
        this.config = config;
        this.httpClient = httpClient;
        this.apiBase = apiBase.endsWith("/") ? apiBase.substring(0, apiBase.length() - 1) : apiBase;
    }

    @Override
    public String resolveUsername() throws IOException, InterruptedException {
        if (config.lichessToken().isBlank()) {
            throw new IllegalStateException("LICHESS: missing token");
        }

        CachedUsername cached = cachedUsername;
        long now = System.currentTimeMillis();
        if (cached != null && now - cached.resolvedAtMillis() < USERNAME_CACHE_MILLIS) {
            return cached.username();
        }

        synchronized (this) {
            cached = cachedUsername;
            now = System.currentTimeMillis();
            if (cached != null && now - cached.resolvedAtMillis() < USERNAME_CACHE_MILLIS) {
                return cached.username();
            }
            JsonNode account = loadAccount();
            String username = accountName(account);
            if (username.isBlank()) {
                throw new IOException("Lichess account response did not include a username");
            }
            cachedUsername = new CachedUsername(username.toLowerCase(Locale.ROOT), now);
            return cachedUsername.username();
        }
    }

    @Override
    public LichessStats fetchRecentStats(String username, int lookbackMinutes, int maximumGames) throws IOException, InterruptedException {
        JsonNode account = loadAccount();
        String resolvedName = accountName(account);
        if (!resolvedName.isBlank()) {
            username = resolvedName;
        }
        int rating = readRating(account);

        long sinceMillis = (System.currentTimeMillis() / 1000L - lookbackMinutes * 60L) * 1000L;
        Map<String, String> query = new LinkedHashMap<>();
        query.put("since", String.valueOf(sinceMillis));
        query.put("perfType", config.lichessPreference());
        query.put("max", String.valueOf(maximumGames));
        query.put("moves", "false");

        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(buildUrl(apiBase + "/api/games/user/" + encodePath(username), query)))
                .timeout(REQUEST_TIMEOUT)
                .header("Authorization", "Bearer " + config.lichessToken())
                .header("Accept", "application/x-ndjson")
                .GET()
                .build();
        HttpResponse<InputStream> response = send(request, HttpResponse.BodyHandlers.ofInputStream(), "/api/games/user");
        if (response.statusCode() >= 400) {
            throw lichessFailure(response.statusCode(), "/api/games/user");
        }

        int games = 0;
        int wins = 0;
        int ratingFromGames = 0;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isBlank()) {
                    continue;
                }
                JsonNode game;
                try {
                    game = MAPPER.readTree(line);
                } catch (IOException ignored) {
                    continue;
                }
                if (game.hasNonNull("error") && !game.has("players")) {
                    throw new IOException("Lichess games: " + game.path("error").asText("request failed"));
                }
                if (!countsForPreference(game)) {
                    continue;
                }
                games++;
                String userColor = userColor(game, username);
                if (!userColor.isEmpty() && userColor.equalsIgnoreCase(game.path("winner").asText(""))) {
                    wins++;
                }
                if (ratingFromGames == 0 && !userColor.isEmpty()) {
                    ratingFromGames = game.path("players").path(userColor).path("rating").asInt(0);
                }
            }
        }
        if (rating == 0) {
            rating = ratingFromGames;
        }
        return new LichessStats(username, rating, games, wins);
    }

    private JsonNode loadAccount() throws IOException, InterruptedException {
        CachedAccount cached = cachedAccount;
        long now = System.currentTimeMillis();
        if (cached != null && now - cached.fetchedAtMillis() < ACCOUNT_REUSE_MILLIS) {
            return cached.body();
        }
        synchronized (this) {
            cached = cachedAccount;
            now = System.currentTimeMillis();
            if (cached != null && now - cached.fetchedAtMillis() < ACCOUNT_REUSE_MILLIS) {
                return cached.body();
            }
            JsonNode body = fetchJson(apiBase + "/api/account");
            cachedAccount = new CachedAccount(body, now);
            return body;
        }
    }

    private JsonNode fetchJson(String url) throws IOException, InterruptedException {
        HttpRequest request = HttpRequest.newBuilder()
                .uri(URI.create(url))
                .timeout(REQUEST_TIMEOUT)
                .header("Authorization", "Bearer " + config.lichessToken())
                .header("Accept", "application/json")
                .GET()
                .build();
        HttpResponse<String> response = send(request, HttpResponse.BodyHandlers.ofString(), "/api/account");
        if (response.statusCode() >= 400) {
            throw lichessFailure(response.statusCode(), "/api/account");
        }
        try {
            return MAPPER.readTree(response.body());
        } catch (IOException e) {
            throw new IOException("Lichess account response was not JSON");
        }
    }

    private <T> HttpResponse<T> send(HttpRequest request, HttpResponse.BodyHandler<T> handler, String endpoint)
            throws IOException, InterruptedException {
        try {
            return httpClient.send(request, handler);
        } catch (HttpTimeoutException e) {
            throw new IOException("Lichess timed out calling " + endpoint);
        }
    }

    private static IOException lichessFailure(int status, String endpoint) {
        String reason = switch (status) {
            case 401, 403 -> "rejected the token";
            case 429 -> "rate limit";
            default -> "HTTP " + status;
        };
        return new IOException("Lichess " + reason + " calling " + endpoint);
    }

    private int readRating(JsonNode account) {
        JsonNode perfs = account.path("perfs");
        if (!perfs.isObject()) {
            return 0;
        }
        String preference = config.lichessPreference().toLowerCase(Locale.ROOT);
        JsonNode perf = perfs.get(preference);
        if (perf == null) {
            var fields = perfs.fields();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> field = fields.next();
                if (field.getKey().equalsIgnoreCase(preference)) {
                    perf = field.getValue();
                    break;
                }
            }
        }
        if (perf == null || !perf.path("rating").isNumber()) {
            return 0;
        }
        return perf.path("rating").asInt();
    }

    private boolean countsForPreference(JsonNode game) {
        String preference = config.lichessPreference().toLowerCase(Locale.ROOT);
        String perf = game.path("perf").asText("");
        String speed = game.path("speed").asText("");
        if (perf.isBlank() && speed.isBlank()) {
            return true;
        }
        return preference.equalsIgnoreCase(perf) || preference.equalsIgnoreCase(speed);
    }

    private static String accountName(JsonNode account) {
        String username = account.path("username").asText("");
        if (!username.isBlank()) {
            return username;
        }
        return account.path("id").asText("");
    }

    private static String userColor(JsonNode game, String username) {
        JsonNode players = game.path("players");
        if (samePlayer(players.path("white"), username)) {
            return "white";
        }
        if (samePlayer(players.path("black"), username)) {
            return "black";
        }
        return "";
    }

    private static boolean samePlayer(JsonNode player, String username) {
        return username.equalsIgnoreCase(playerName(player.path("user")));
    }

    private static String playerName(JsonNode userNode) {
        if (userNode.isTextual()) {
            return userNode.asText("");
        }
        String name = userNode.path("name").asText("");
        if (!name.isBlank()) {
            return name;
        }
        return userNode.path("id").asText("");
    }

    private static String encodePath(String username) {
        return URLEncoder.encode(username, StandardCharsets.UTF_8).replace("+", "%20");
    }

    private String buildUrl(String baseUrl, Map<String, String> params) {
        StringBuilder url = new StringBuilder(baseUrl);
        boolean first = true;
        for (Map.Entry<String, String> param : params.entrySet()) {
            url.append(first ? '?' : '&');
            url.append(URLEncoder.encode(param.getKey(), StandardCharsets.UTF_8));
            url.append('=');
            url.append(URLEncoder.encode(param.getValue(), StandardCharsets.UTF_8));
            first = false;
        }
        return url.toString();
    }

    private record CachedUsername(String username, long resolvedAtMillis) {}

    private record CachedAccount(JsonNode body, long fetchedAtMillis) {}
}
