#include "race.hpp"

#include <cstdlib>
#include <cstdio>
#include <thread>

std::string pick_winner(const RpcResult& alchemy, const RpcResult& infura) {
    if (alchemy.ok && infura.ok) {
        const double diff = alchemy.duration_ms - infura.duration_ms;
        const double gap = diff < 0 ? -diff : diff;
        if (gap <= kTieBandMs) {
            return "tie";
        }
        return alchemy.duration_ms < infura.duration_ms ? "alchemy" : "infura";
    }
    if (alchemy.ok) {
        return "alchemy";
    }
    if (infura.ok) {
        return "infura";
    }
    return "none";
}

std::string head_note(const RpcResult& alchemy, const RpcResult& infura) {
    if (!alchemy.ok || !infura.ok) {
        return "one feed failed the check";
    }
    const unsigned long long a = std::strtoull(alchemy.result_preview.c_str(), nullptr, 16);
    const unsigned long long b = std::strtoull(infura.result_preview.c_str(), nullptr, 16);
    if (a == b) {
        return "same head";
    }
    const unsigned long long gap = a > b ? a - b : b - a;
    return "heads differ by " + std::to_string(gap) + " blocks";
}

void tally(Score& score, const Heat& heat) {
    score.ran += 1;
    if (heat.winner == "alchemy") {
        score.alchemy_wins += 1;
    } else if (heat.winner == "infura") {
        score.infura_wins += 1;
    } else if (heat.winner == "tie") {
        score.ties += 1;
    } else {
        score.missed += 1;
    }

    if (heat.alchemy.ok) {
        score.alchemy_ms += heat.alchemy.duration_ms;
        score.alchemy_ok += 1;
        if (score.alchemy_best < 0 || heat.alchemy.duration_ms < score.alchemy_best) {
            score.alchemy_best = heat.alchemy.duration_ms;
        }
    }
    if (heat.infura.ok) {
        score.infura_ms += heat.infura.duration_ms;
        score.infura_ok += 1;
        if (score.infura_best < 0 || heat.infura.duration_ms < score.infura_best) {
            score.infura_best = heat.infura.duration_ms;
        }
    }
}

std::string champion_line(const Score& score) {
    if (score.ran == 0) {
        return "no heats ran";
    }
    const auto avg = [](double sum, int n) { return n > 0 ? sum / n : 1e300; };
    const double alchemy_avg = avg(score.alchemy_ms, score.alchemy_ok);
    const double infura_avg = avg(score.infura_ms, score.infura_ok);

    const char* name = "tie";
    if (score.alchemy_wins > score.infura_wins) {
        name = "alchemy";
    } else if (score.infura_wins > score.alchemy_wins) {
        name = "infura";
    } else if (score.alchemy_ok == 0 && score.infura_ok == 0) {
        return "no champion: both feeds missed";
    } else if (alchemy_avg < infura_avg) {
        name = "alchemy";
    } else if (infura_avg < alchemy_avg) {
        name = "infura";
    }

    char buf[160];
    std::snprintf(buf, sizeof buf, "champion: %s   fills alchemy %d  infura %d  ties %d", name,
                  score.alchemy_wins, score.infura_wins, score.ties);
    return buf;
}

std::string best_line(const Score& score) {
    char alchemy[32] = "n/a";
    char infura[32] = "n/a";
    if (score.alchemy_best >= 0) {
        std::snprintf(alchemy, sizeof alchemy, "%.1f ms", score.alchemy_best);
    }
    if (score.infura_best >= 0) {
        std::snprintf(infura, sizeof infura, "%.1f ms", score.infura_best);
    }
    char buf[120];
    std::snprintf(buf, sizeof buf, "best lap  alchemy %s   infura %s", alchemy, infura);
    return buf;
}

Checks run_checks(const Provider& alchemy, const Provider& infura) {
    Checks checks;
    std::thread a([&] { checks.alchemy = eth_block_number(alchemy); });
    std::thread b([&] { checks.infura = eth_block_number(infura); });
    a.join();
    b.join();
    return checks;
}

Heat run_heat(const Provider& alchemy, const Provider& infura, const Book& book) {
    Heat heat;
    heat.book = book;
    heat.signal = signal_from(book);
    heat.alchemy.method = "eth_call";
    heat.infura.method = "eth_call";
    if (!heat.signal.fired) {
        heat.winner = "none";
        heat.alchemy.error = "pattern did not fire";
        heat.infura.error = "pattern did not fire";
        return heat;
    }

    std::thread a([&] { heat.alchemy = eth_get_reserves(alchemy); });
    std::thread b([&] { heat.infura = eth_get_reserves(infura); });
    a.join();
    b.join();
    heat.winner = pick_winner(heat.alchemy, heat.infura);
    return heat;
}
