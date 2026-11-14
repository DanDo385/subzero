#include "book.hpp"

#include <algorithm>
#include <sstream>

namespace {

void add_level(std::vector<Level>& levels, int price, int qty, bool high_first) {
    if (qty <= 0) {
        return;
    }
    for (Level& level : levels) {
        if (level.price == price) {
            level.qty += qty;
            return;
        }
    }
    levels.push_back(Level{price, qty});
    std::sort(levels.begin(), levels.end(), [high_first](const Level& a, const Level& b) {
        return high_first ? a.price > b.price : a.price < b.price;
    });
}

int sum_top(const std::vector<Level>& levels, int n) {
    int total = 0;
    int seen = 0;
    for (const Level& level : levels) {
        if (seen == n) {
            break;
        }
        total += level.qty;
        ++seen;
    }
    return total;
}

}  // namespace

void Book::add_bid(int price, int qty) { add_level(bids_, price, qty, true); }

void Book::add_ask(int price, int qty) { add_level(asks_, price, qty, false); }

int Book::best_bid() const { return bids_.empty() ? 0 : bids_.front().price; }

int Book::best_ask() const { return asks_.empty() ? 0 : asks_.front().price; }

int Book::spread() const {
    if (bids_.empty() || asks_.empty()) {
        return 9999;
    }
    return asks_.front().price - bids_.front().price;
}

int Book::top_qty(Side side, int levels) const {
    return sum_top(side == Side::Bid ? bids_ : asks_, levels);
}

bool Book::bid_stack() const {
    if (bids_.empty() || asks_.empty()) {
        return false;
    }
    const int spr = spread();
    if (spr < 0 || spr > kMaxSpreadTicks) {
        return false;
    }
    const int ask_qty = top_qty(Side::Ask, kStackLevels);
    if (ask_qty <= 0) {
        return false;
    }
    return top_qty(Side::Bid, kStackLevels) >= kStackMultiple * ask_qty;
}

std::string Book::ladder() const {
    std::ostringstream out;
    for (auto it = asks_.rbegin(); it != asks_.rend(); ++it) {
        out << "ask " << it->price << " x " << it->qty << "\n";
    }
    const int spr = (bids_.empty() || asks_.empty()) ? -1 : spread();
    out << "--- spread " << spr << " ---\n";
    for (const Level& level : bids_) {
        out << "bid " << level.price << " x " << level.qty << "\n";
    }
    return out.str();
}

std::vector<Book> showcase_frames() {
    // A short scripted tape so a live demo always reaches the pattern.
    // Start balanced, then size joins the best bid until the stack rule hits.
    Book book;
    book.add_ask(101, 5);
    book.add_ask(102, 5);
    book.add_ask(103, 8);
    book.add_bid(99, 4);
    book.add_bid(98, 4);
    book.add_bid(97, 4);

    std::vector<Book> frames;
    frames.push_back(book);
    for (int i = 0; i < 3; ++i) {
        book.add_bid(99, 8);
        frames.push_back(book);
    }
    return frames;
}

Signal signal_from(const Book& book) {
    Signal signal;
    signal.pattern = kPatternName;
    signal.qty = 1;
    if (!book.bid_stack()) {
        return signal;
    }
    signal.fired = true;
    signal.side = "buy";
    signal.price_ticks = book.best_ask();
    return signal;
}
