package com.example.server.integration.lichess;

import com.example.server.config.AppConfig;
import com.example.server.domain.GuardModels.LichessStats;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class LichessClientTest {
    private HttpServer server;
    private AtomicReference<String> gamesQuery;

    @BeforeEach
    void startServer() throws IOException {
        gamesQuery = new AtomicReference<>("");
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.start();
    }

    @AfterEach
    void stopServer() {
        server.stop(0);
    }

    @Test
    void countsOnlyTheConfiguredSpeedAndReadsRatingFromTheAccount() throws Exception {
        account("""
                {"id":"player","username":"Player","perfs":{"blitz":{"rating":1800}}}
                """);
        games("""
                {"speed":"rapid","perf":"rapid","players":{"white":{"user":{"name":"Player"},"rating":1800},"black":{"user":{"name":"Other"}}},"winner":"white"}
                {"speed":"blitz","perf":"blitz","players":{"white":{"user":{"id":"player"},"rating":1800},"black":{"user":{"name":"Other"}}},"winner":"white"}
                not-json
                {"speed":"blitz","perf":"blitz","players":{"white":{"user":{"name":"Other"}},"black":{"user":{"name":"Player"},"rating":1800}},"winner":"white"}
                """);

        LichessStats stats = client().fetchRecentStats("player", 30, 200);

        assertTrue(gamesQuery.get().contains("perfType=blitz"));
        assertEquals(1800, stats.rating());
        assertEquals(2, stats.games());
        assertEquals(1, stats.wins());
    }

    @Test
    void usesTheGameRatingWhenTheAccountPayloadOmitsIt() throws Exception {
        account("""
                {"username":"Player","perfs":{}}
                """);
        games("""
                {"perf":"blitz","players":{"white":{"user":{"name":"Player"},"rating":2210},"black":{"user":"Other"}},"winner":"black"}
                """);

        LichessStats stats = client().fetchRecentStats("player", 30, 200);

        assertEquals(2210, stats.rating());
        assertEquals(1, stats.games());
        assertEquals(0, stats.wins());
    }

    @Test
    void reportsARejectedTokenWithoutCallingTheGamesStream() {
        server.createContext("/api/account", exchange -> respond(exchange, 401, ""));

        IOException error = assertThrows(IOException.class, () -> client().fetchRecentStats("player", 30, 200));

        assertTrue(error.getMessage().contains("rejected the token"));
    }

    @Test
    void reportsRateLimitAndAGamesErrorDocument() throws Exception {
        account("""
                {"username":"Player","perfs":{"blitz":{"rating":1800}}}
                """);
        server.createContext("/api/games/user/Player", exchange -> respond(exchange, 429, ""));

        IOException limited = assertThrows(IOException.class, () -> client().fetchRecentStats("player", 30, 200));
        assertTrue(limited.getMessage().contains("rate limit"));
    }

    @Test
    void failsWhenTheGamesBodyIsAnErrorDocument() throws Exception {
        account("""
                {"username":"Player","perfs":{"blitz":{"rating":1800}}}
                """);
        games("""
                {"error":"No such user"}
                """);

        IOException error = assertThrows(IOException.class, () -> client().fetchRecentStats("player", 30, 200));

        assertTrue(error.getMessage().contains("No such user"));
    }

    private void account(String body) {
        server.createContext("/api/account", exchange -> respond(exchange, 200, body));
    }

    private void games(String body) {
        server.createContext("/api/games/user/Player", exchange -> {
            gamesQuery.set(exchange.getRequestURI().getRawQuery());
            respond(exchange, 200, body);
        });
    }

    private LichessClient client() {
        AppConfig config = new AppConfig(
                "127.0.0.1", 5001, "", true, "token", "blitz", 2100, 10, 0.5, 30, 200, false
        );
        int port = server.getAddress().getPort();
        return new LichessClient(config, java.net.http.HttpClient.newHttpClient(), "http://127.0.0.1:" + port);
    }

    private static void respond(com.sun.net.httpserver.HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }
}
