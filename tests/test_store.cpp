#include "store.hpp"

#include <sqlite3.h>

#include <cmath>
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

double one_double(sqlite3* db, const char* sql) {
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        std::fprintf(stderr, "query failed: %s\n", sqlite3_errmsg(db));
        return -1;
    }
    double value = -1;
    if (sqlite3_step(stmt) == SQLITE_ROW) {
        value = sqlite3_column_double(stmt, 0);
    }
    sqlite3_finalize(stmt);
    return value;
}

std::string one_text(sqlite3* db, const char* sql) {
    sqlite3_stmt* stmt = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, nullptr) != SQLITE_OK) {
        return {};
    }
    std::string value;
    if (sqlite3_step(stmt) == SQLITE_ROW && sqlite3_column_text(stmt, 0) != nullptr) {
        value = reinterpret_cast<const char*>(sqlite3_column_text(stmt, 0));
    }
    sqlite3_finalize(stmt);
    return value;
}

}  // namespace

#define CHECK(cond) check((cond), __FILE__, __LINE__, #cond)

int main() {
    const char* path = "/tmp/provider-derby-store-test.sqlite";
    std::remove(path);
    std::string wal = std::string(path) + "-wal";
    std::string shm = std::string(path) + "-shm";
    std::remove(wal.c_str());
    std::remove(shm.c_str());

    {
        Store store(path);
        RpcResult check_call;
        check_call.ok = true;
        check_call.http_status = 200;
        check_call.duration_ms = 30;
        check_call.method = "eth_blockNumber";
        check_call.result_preview = "0x10";
        store.save_check("alchemy", check_call);
        store.save_check("infura", check_call);

        Heat heat;
        heat.book = showcase_frames().back();
        heat.signal = signal_from(heat.book);
        heat.alchemy.ok = true;
        heat.alchemy.http_status = 200;
        heat.alchemy.duration_ms = 10;
        heat.alchemy.method = "eth_call";
        heat.alchemy.result_preview = "0xabc";
        heat.infura.ok = true;
        heat.infura.http_status = 200;
        heat.infura.duration_ms = 20;
        heat.infura.method = "eth_call";
        heat.infura.result_preview = "0xdef";
        heat.winner = "alchemy";
        CHECK(heat.signal.fired);
        store.save_heat(heat);
    }

    sqlite3* db = nullptr;
    CHECK(sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nullptr) == SQLITE_OK);
    CHECK(one_text(db, "SELECT winner FROM races") == "alchemy");
    CHECK(one_text(db, "SELECT book_text FROM races").find("bid 99 x 28") != std::string::npos);
    CHECK(one_double(db, "SELECT count(*) FROM api_calls WHERE role = 'check' AND race_id IS NULL") ==
          2);
    CHECK(one_double(db, "SELECT count(*) FROM api_calls WHERE role = 'act'") == 2);
    CHECK(std::fabs(one_double(db,
                               "SELECT avg_ms FROM provider_stats "
                               "WHERE provider = 'alchemy' AND role = 'act'") -
                    10.0) < 0.01);
    CHECK(one_double(db, "SELECT fills FROM scoreboard WHERE provider = 'alchemy'") == 1);
    sqlite3_close(db);
    std::remove(path);

    if (g_failed != 0) {
        std::fprintf(stderr, "%d store checks failed\n", g_failed);
        return 1;
    }
    std::printf("store ok\n");
    return 0;
}
