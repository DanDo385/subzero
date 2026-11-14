#pragma once

#include "race.hpp"

#include <string>

struct sqlite3;

class Store {
  public:
    explicit Store(const std::string& path);
    ~Store();
    Store(const Store&) = delete;
    Store& operator=(const Store&) = delete;

    void save_check(const std::string& provider, const RpcResult& result);
    int save_heat(const Heat& heat);

  private:
    sqlite3* db_ = nullptr;
    void insert_call(int race_id, const std::string& provider, const std::string& role,
                     const RpcResult& result);
};
