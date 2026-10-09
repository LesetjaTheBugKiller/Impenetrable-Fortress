package com.example.server.state;

import com.example.server.domain.GuardModels.TradeRisk;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.Iterator;
import java.util.List;

/** Recent stop-risk amounts reported by the receiver for the sender to read. */
public class TradeRiskStore {
    static final int LIMIT = 8;

    private final Deque<TradeRisk> risks = new ArrayDeque<>();

    public synchronized void add(TradeRisk risk) {
        risks.addFirst(risk);
        while (risks.size() > LIMIT) {
            risks.removeLast();
        }
    }

    public synchronized List<TradeRisk> latest() {
        return List.copyOf(new ArrayList<>(risks));
    }

    public synchronized boolean removeLatest(String symbol, String side) {
        Iterator<TradeRisk> risks = this.risks.iterator();
        while (risks.hasNext()) {
            TradeRisk risk = risks.next();
            if (risk.symbol().equalsIgnoreCase(symbol) && risk.side().equalsIgnoreCase(side)) {
                risks.remove();
                return true;
            }
        }
        return false;
    }
}
