#pragma once

#include <string>
#include <vector>

// Prices are integer ticks in a toy book. One tick is just the unit we
// count in. 101 and 99 are two ticks apart. This is not a dollar price.

enum class Side { Bid, Ask };

struct Level {
    int price = 0;
    int qty = 0;
};

inline constexpr int kStackLevels = 3;
inline constexpr int kStackMultiple = 2;
inline constexpr int kMaxSpreadTicks = 2;
inline constexpr const char* kPatternName = "bid_stack";

struct Signal {
    bool fired = false;
    std::string pattern;
    std::string side;
    int price_ticks = 0;
    int qty = 0;
};

class Book {
  public:
    void add_bid(int price, int qty);
    void add_ask(int price, int qty);

    int best_bid() const;
    int best_ask() const;
    int spread() const;
    int top_qty(Side side, int levels) const;
    bool bid_stack() const;
    std::string ladder() const;

  private:
    std::vector<Level> bids_;
    std::vector<Level> asks_;
};

std::vector<Book> showcase_frames();
Signal signal_from(const Book& book);
