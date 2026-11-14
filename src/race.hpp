#pragma once

#include "book.hpp"
#include "rpc.hpp"

#include <string>

struct Heat {
    Book book;
    Signal signal;
    RpcResult alchemy;
    RpcResult infura;
    std::string winner;
};

struct Checks {
    RpcResult alchemy;
    RpcResult infura;
};

struct Score {
    int ran = 0;
    int alchemy_wins = 0;
    int infura_wins = 0;
    int ties = 0;
    int missed = 0;
    int alchemy_ok = 0;
    int infura_ok = 0;
    double alchemy_ms = 0;
    double infura_ms = 0;
    double alchemy_best = -1;
    double infura_best = -1;
    bool quit_early = false;
    bool checks_ok = false;
};

// Two successful calls within this many milliseconds count as a tie.
// The clock includes our own HTTP client, so finer than this is noise.
inline constexpr double kTieBandMs = 0.5;

std::string pick_winner(const RpcResult& alchemy, const RpcResult& infura);
std::string head_note(const RpcResult& alchemy, const RpcResult& infura);
std::string champion_line(const Score& score);
std::string best_line(const Score& score);
void tally(Score& score, const Heat& heat);

Checks run_checks(const Provider& alchemy, const Provider& infura);
Heat run_heat(const Provider& alchemy, const Provider& infura, const Book& book);
