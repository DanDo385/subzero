#include "tui.hpp"

#include <ncurses.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <unistd.h>
#include <vector>

namespace {

std::vector<std::string> split_lines(const std::string& text) {
    std::vector<std::string> lines;
    std::string current;
    for (char c : text) {
        if (c == '\n') {
            lines.push_back(current);
            current.clear();
        } else {
            current.push_back(c);
        }
    }
    if (!current.empty()) {
        lines.push_back(current);
    }
    return lines;
}

std::string bar(double ms, double scale) {
    if (!std::isfinite(ms) || ms < 0) {
        ms = 0;
    }
    int n = 0;
    if (scale > 0) {
        n = static_cast<int>(std::lround(20.0 * ms / scale));
    }
    if (n < 1 && ms > 0) {
        n = 1;
    }
    if (n > 20) {
        n = 20;
    }
    return std::string(static_cast<size_t>(n), '#') + std::string(static_cast<size_t>(20 - n), '.');
}

const char* tag_for(const std::string& winner, const std::string& provider) {
    if (winner == "tie") {
        return "fill";
    }
    if (winner == provider) {
        return "fill";
    }
    if (winner == "none") {
        return "miss";
    }
    return "behind";
}

std::string race_line(const RpcResult& result, const std::string& tag, double scale) {
    if (result.method.empty() && result.error.empty() && !result.ok) {
        return "waiting";
    }
    char ms[32];
    std::snprintf(ms, sizeof ms, "%7.1f ms", result.duration_ms);
    const char* state = result.ok ? "sent" : "fail";
    return std::string(state) + "  " + bar(result.duration_ms, scale) + "  " + ms + "  " + tag;
}

std::string check_line(const RpcResult& result) {
    if (result.method.empty() && result.error.empty() && !result.ok) {
        return "waiting";
    }
    char buf[160];
    const char* detail = result.ok ? result.result_preview.c_str() : result.error.c_str();
    std::snprintf(buf, sizeof buf, "%s  %7.1f ms  %s", result.ok ? "ok  " : "fail", result.duration_ms,
                  detail);
    return buf;
}

void print_call(const char* name, const RpcResult& result, const std::string& tag) {
    std::cout << "  " << name;
    if (result.ok) {
        const char* state = tag.empty() ? "ok  " : "sent";
        std::printf("  %s  %7.1f ms  %s", state, result.duration_ms, tag.c_str());
        if (result.method == "eth_blockNumber") {
            std::cout << "  " << result.result_preview;
        }
    } else {
        std::printf("  fail  %7.1f ms  %s", result.duration_ms, result.error.c_str());
    }
    std::cout << "\n";
}

struct Curses {
    bool active = false;
    Curses() {
        if (initscr() == nullptr) {
            return;
        }
        active = true;
        cbreak();
        noecho();
        curs_set(0);
        if (has_colors()) {
            start_color();
            use_default_colors();
            init_pair(1, COLOR_CYAN, -1);
            init_pair(2, COLOR_GREEN, -1);
            init_pair(3, COLOR_MAGENTA, -1);
            init_pair(4, COLOR_YELLOW, -1);
            init_pair(5, COLOR_RED, -1);
        }
    }
    ~Curses() {
        if (active) {
            endwin();
        }
    }
    Curses(const Curses&) = delete;
    Curses& operator=(const Curses&) = delete;
};

void put(int y, int x, int color, const std::string& text) {
    if (y < 0 || y >= LINES || x >= COLS) {
        return;
    }
    int room = COLS - x - 1;
    if (room < 0) {
        return;
    }
    std::string clipped = text;
    if (static_cast<int>(clipped.size()) > room) {
        clipped.resize(static_cast<size_t>(room));
    }
    if (color > 0 && has_colors()) {
        attron(COLOR_PAIR(color));
    }
    mvprintw(y, x, "%s", clipped.c_str());
    if (color > 0 && has_colors()) {
        attroff(COLOR_PAIR(color));
    }
}

bool wait_ms(int ms) {
    int left = ms;
    while (left > 0) {
        const int slice = left > 100 ? 100 : left;
        timeout(slice);
        const int ch = getch();
        if (ch == 'q' || ch == 'Q') {
            return true;
        }
        left -= slice;
    }
    return false;
}

struct Frame {
    std::string status;
    std::string ladder;
    std::string alchemy_check = "waiting";
    std::string infura_check = "waiting";
    std::string alchemy_race = "waiting";
    std::string infura_race = "waiting";
    std::string score = "alchemy 0   infura 0   ties 0";
    std::string champion;
    std::string best;
    int alchemy_color = 2;
    int infura_color = 3;
};

void draw(const Frame& frame) {
    erase();
    put(0, 1, 1, "PROVIDER DERBY");
    put(0, 20, 4, frame.score);
    put(1, 1, 0, "two feeds, one book, first response fills");
    put(3, 1, 0, frame.status);
    put(5, 1, 1, "BOOK");
    put(5, 36, 1, "FEEDS");
    const auto lines = split_lines(frame.ladder);
    for (size_t i = 0; i < lines.size() && i < 8; ++i) {
        put(6 + static_cast<int>(i), 1, 0, lines[i]);
    }
    put(6, 36, frame.alchemy_color, "alchemy  " + frame.alchemy_check);
    put(7, 36, frame.infura_color, "infura   " + frame.infura_check);
    put(9, 36, 1, "RACE  lower is faster");
    put(10, 36, frame.alchemy_color, "alchemy  " + frame.alchemy_race);
    put(11, 36, frame.infura_color, "infura   " + frame.infura_race);
    if (!frame.champion.empty()) {
        put(13, 1, 4, frame.champion);
        put(14, 1, 0, frame.best);
    }
    put(LINES - 2, 1, 0, "q quits");
    refresh();
}

std::string score_text(const Score& score) {
    char buf[80];
    std::snprintf(buf, sizeof buf, "alchemy %d   infura %d   ties %d", score.alchemy_wins,
                  score.infura_wins, score.ties);
    return buf;
}

}  // namespace

Score run_plain(Store& store, const Provider& alchemy, const Provider& infura,
                const SessionOptions& options) {
    Score score;
    std::cout << "Provider Derby\n";
    std::cout << "paper race. same signal, two feeds. first response fills.\n\n";
    std::cout << "connection check  (eth_blockNumber)\n";
    const Checks checks = run_checks(alchemy, infura);
    store.save_check("alchemy", checks.alchemy);
    store.save_check("infura", checks.infura);
    score.checks_ok = checks.alchemy.ok && checks.infura.ok;
    print_call("alchemy", checks.alchemy, "");
    print_call("infura ", checks.infura, "");
    std::cout << "  " << head_note(checks.alchemy, checks.infura) << "\n\n";

    const std::vector<Book> frames = showcase_frames();
    const Book& book = frames.back();
    const Signal signal = signal_from(book);
    std::cout << "book\n" << book.ladder() << "\n";
    if (!signal.fired) {
        std::cout << "pattern did not fire\n";
        return score;
    }
    std::cout << "pattern " << signal.pattern << " fired\n";
    std::cout << "paper action: " << signal.side << " " << signal.qty << " @ " << signal.price_ticks
              << "\n\n";

    for (int i = 0; i < options.heats; ++i) {
        const Heat heat = run_heat(alchemy, infura, book);
        store.save_heat(heat);
        tally(score, heat);
        std::cout << "heat " << (i + 1) << "/" << options.heats
                  << "  (eth_call getReserves on USDC/WETH)\n";
        print_call("alchemy", heat.alchemy, tag_for(heat.winner, "alchemy"));
        print_call("infura ", heat.infura, tag_for(heat.winner, "infura"));
        std::cout << "  winner: " << heat.winner << "\n\n";
    }

    std::cout << champion_line(score) << "\n";
    std::cout << best_line(score) << "\n";
    std::cout << "saved: " << options.db_path << "\n";
    return score;
}

Score run_tui(Store& store, const Provider& alchemy, const Provider& infura,
              const SessionOptions& options) {
    if (!isatty(STDOUT_FILENO)) {
        std::cerr << "No terminal for the TUI. Printing the plain report.\n";
        return run_plain(store, alchemy, infura, options);
    }

    bool too_small = false;
    Score score;
    {
        Curses curses;
        if (!curses.active) {
            too_small = true;
        } else if (COLS < 78 || LINES < 20) {
            too_small = true;
        } else {
            const std::vector<Book> frames = showcase_frames();
            const Book& book = frames.back();
            Frame frame;
            frame.status = "checking both providers (eth_blockNumber)";
            frame.ladder = frames.front().ladder();
            draw(frame);

            const Checks checks = run_checks(alchemy, infura);
            store.save_check("alchemy", checks.alchemy);
            store.save_check("infura", checks.infura);
            score.checks_ok = checks.alchemy.ok && checks.infura.ok;
            frame.alchemy_check = check_line(checks.alchemy);
            frame.infura_check = check_line(checks.infura);
            frame.status = std::string("check: ") + head_note(checks.alchemy, checks.infura);
            draw(frame);
            if (wait_ms(400)) {
                score.quit_early = true;
                return score;
            }

            if (options.animate) {
                for (size_t i = 0; i < frames.size(); ++i) {
                    frame.ladder = frames[i].ladder();
                    frame.status = "tape " + std::to_string(i + 1) + "/" +
                                   std::to_string(frames.size()) +
                                   "  size joining the best bid";
                    draw(frame);
                    if (wait_ms(280)) {
                        score.quit_early = true;
                        return score;
                    }
                }
            } else {
                frame.ladder = book.ladder();
            }

            const Signal signal = signal_from(book);
            if (!signal.fired) {
                frame.status = "pattern did not fire";
                draw(frame);
                return score;
            }
            frame.status = "PATTERN bid_stack. both lanes buy " + std::to_string(signal.qty) +
                           " @ " + std::to_string(signal.price_ticks);
            frame.ladder = book.ladder();
            draw(frame);
            if (wait_ms(500)) {
                score.quit_early = true;
                return score;
            }

            for (int i = 0; i < options.heats; ++i) {
                frame.alchemy_race = "waiting";
                frame.infura_race = "waiting";
                frame.status = "heat " + std::to_string(i + 1) + "/" + std::to_string(options.heats) +
                               "  both providers triggered";
                frame.score = score_text(score);
                draw(frame);

                const Heat heat = run_heat(alchemy, infura, book);
                store.save_heat(heat);
                tally(score, heat);
                const double scale = std::max(heat.alchemy.duration_ms, heat.infura.duration_ms);
                frame.alchemy_race = race_line(heat.alchemy, tag_for(heat.winner, "alchemy"), scale);
                frame.infura_race = race_line(heat.infura, tag_for(heat.winner, "infura"), scale);
                frame.alchemy_color = heat.winner == "alchemy" || heat.winner == "tie" ? 2 : 5;
                frame.infura_color = heat.winner == "infura" || heat.winner == "tie" ? 3 : 5;
                frame.score = score_text(score);
                frame.status = "heat " + std::to_string(i + 1) + " winner: " + heat.winner;
                if (i + 1 == options.heats) {
                    frame.champion = champion_line(score);
                    frame.best = best_line(score);
                }
                draw(frame);
                if (wait_ms(i + 1 == options.heats ? 200 : 700)) {
                    score.quit_early = true;
                    return score;
                }
            }

            frame.champion = champion_line(score);
            frame.best = best_line(score);
            frame.status = "session done";
            draw(frame);
            if (!options.exit_after) {
                timeout(-1);
                while (true) {
                    const int ch = getch();
                    if (ch == 'q' || ch == 'Q') {
                        break;
                    }
                }
            } else {
                wait_ms(300);
            }
            return score;
        }
    }

    if (too_small) {
        std::cerr << "Terminal is smaller than 78x20, or ncurses could not start. "
                     "Printing the plain report.\n";
        return run_plain(store, alchemy, infura, options);
    }
    return score;
}
