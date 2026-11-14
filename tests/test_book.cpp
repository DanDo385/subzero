#include "book.hpp"

#include <cstdio>
#include <string>

namespace {

int g_failed = 0;

void check(bool cond, const char* file, int line, const char* text) {
    if (!cond) {
        std::fprintf(stderr, "FAIL %s:%d %s\n", file, line, text);
        g_failed += 1;
    }
}

}  // namespace

#define CHECK(cond) check((cond), __FILE__, __LINE__, #cond)

int main() {
    const std::vector<Book> frames = showcase_frames();
    CHECK(frames.size() == 4);
    CHECK(!frames.front().bid_stack());
    CHECK(!frames[1].bid_stack());
    CHECK(!frames[2].bid_stack());

    const Book& last = frames.back();
    CHECK(last.bid_stack());
    CHECK(last.best_bid() == 99);
    CHECK(last.best_ask() == 101);
    CHECK(last.spread() == 2);
    CHECK(last.top_qty(Side::Bid, 3) == 36);
    CHECK(last.top_qty(Side::Ask, 3) == 18);
    CHECK(last.ladder().find("bid 99 x 28") != std::string::npos);

    const Signal quiet = signal_from(frames.front());
    CHECK(!quiet.fired);
    CHECK(quiet.pattern == "bid_stack");

    const Signal fired = signal_from(last);
    CHECK(fired.fired);
    CHECK(fired.side == "buy");
    CHECK(fired.qty == 1);
    CHECK(fired.price_ticks == 101);

    if (g_failed != 0) {
        std::fprintf(stderr, "%d book checks failed\n", g_failed);
        return 1;
    }
    std::printf("book ok\n");
    return 0;
}
