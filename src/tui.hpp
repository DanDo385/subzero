#pragma once

#include "store.hpp"

#include <string>

struct SessionOptions {
    int heats = 5;
    bool animate = true;
    bool exit_after = false;
    std::string db_path;
};

Score run_plain(Store& store, const Provider& alchemy, const Provider& infura,
                const SessionOptions& options);
Score run_tui(Store& store, const Provider& alchemy, const Provider& infura,
              const SessionOptions& options);
