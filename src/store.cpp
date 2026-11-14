#include "store.hpp"

#include <sqlite3.h>

#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <sstream>
#include <stdexcept>

#ifndef SCHEMA_PATH
#define SCHEMA_PATH "schema.sql"
#endif

namespace {

std::string now_iso() {
    const auto now = std::chrono::system_clock::now();
    const auto secs = std::chrono::time_point_cast<std::chrono::seconds>(now);
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - secs).count();
    const std::time_t tt = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
    gmtime_r(&tt, &tm);
    char buf[40];
    std::snprintf(buf, sizeof buf, "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ", tm.tm_year + 1900,
                  tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, static_cast<int>(ms));
    return buf;
}

std::string read_schema() {
    std::ifstream in(SCHEMA_PATH);
    if (!in) {
        throw std::runtime_error(std::string("Could not read schema at ") + SCHEMA_PATH +
                                 ". Rebuild from the repo root with make.");
    }
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
}

struct Stmt {
    sqlite3_stmt* raw = nullptr;
    Stmt() = default;
    ~Stmt() {
        if (raw != nullptr) {
            sqlite3_finalize(raw);
        }
    }
    Stmt(const Stmt&) = delete;
    Stmt& operator=(const Stmt&) = delete;
};

void require(int rc, sqlite3* db, const char* what) {
    if (rc != SQLITE_OK && rc != SQLITE_DONE && rc != SQLITE_ROW) {
        throw std::runtime_error(std::string(what) + ": " + sqlite3_errmsg(db));
    }
}

void bind_text(sqlite3_stmt* stmt, int index, const std::string& value) {
    sqlite3_bind_text(stmt, index, value.c_str(), static_cast<int>(value.size()), SQLITE_TRANSIENT);
}

}  // namespace

Store::Store(const std::string& path) {
    const int rc = sqlite3_open_v2(path.c_str(), &db_,
                                   SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
                                   nullptr);
    if (rc != SQLITE_OK) {
        const std::string message = db_ != nullptr ? sqlite3_errmsg(db_) : "sqlite open failed";
        if (db_ != nullptr) {
            sqlite3_close(db_);
            db_ = nullptr;
        }
        throw std::runtime_error("Could not open " + path + ": " + message);
    }
    sqlite3_busy_timeout(db_, 2000);
    const std::string schema = read_schema();
    char* error = nullptr;
    const int exec_rc = sqlite3_exec(db_, schema.c_str(), nullptr, nullptr, &error);
    if (exec_rc != SQLITE_OK) {
        const std::string message = error != nullptr ? error : sqlite3_errmsg(db_);
        sqlite3_free(error);
        throw std::runtime_error("Could not apply schema: " + message);
    }
}

Store::~Store() {
    if (db_ != nullptr) {
        sqlite3_close(db_);
    }
}

void Store::insert_call(int race_id, const std::string& provider, const std::string& role,
                        const RpcResult& result) {
    const char* sql =
        "INSERT INTO api_calls ("
        "race_id, provider, role, method, http_status, ok, duration_ms, result_preview, error, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
    Stmt stmt;
    require(sqlite3_prepare_v2(db_, sql, -1, &stmt.raw, nullptr), db_, "prepare api_calls");
    if (race_id < 0) {
        sqlite3_bind_null(stmt.raw, 1);
    } else {
        sqlite3_bind_int(stmt.raw, 1, race_id);
    }
    bind_text(stmt.raw, 2, provider);
    bind_text(stmt.raw, 3, role);
    bind_text(stmt.raw, 4, result.method);
    sqlite3_bind_int(stmt.raw, 5, static_cast<int>(result.http_status));
    sqlite3_bind_int(stmt.raw, 6, result.ok ? 1 : 0);
    sqlite3_bind_double(stmt.raw, 7, result.duration_ms);
    if (result.result_preview.empty()) {
        sqlite3_bind_null(stmt.raw, 8);
    } else {
        bind_text(stmt.raw, 8, result.result_preview);
    }
    if (result.error.empty()) {
        sqlite3_bind_null(stmt.raw, 9);
    } else {
        bind_text(stmt.raw, 9, result.error);
    }
    bind_text(stmt.raw, 10, now_iso());
    require(sqlite3_step(stmt.raw), db_, "insert api_calls");
}

void Store::save_check(const std::string& provider, const RpcResult& result) {
    insert_call(-1, provider, "check", result);
}

int Store::save_heat(const Heat& heat) {
    const char* sql =
        "INSERT INTO races (started_at, pattern, winner, side, price_ticks, qty, book_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)";
    Stmt stmt;
    require(sqlite3_prepare_v2(db_, sql, -1, &stmt.raw, nullptr), db_, "prepare races");
    bind_text(stmt.raw, 1, now_iso());
    bind_text(stmt.raw, 2, heat.signal.pattern);
    bind_text(stmt.raw, 3, heat.winner);
    bind_text(stmt.raw, 4, heat.signal.side);
    sqlite3_bind_int(stmt.raw, 5, heat.signal.price_ticks);
    sqlite3_bind_int(stmt.raw, 6, heat.signal.qty);
    bind_text(stmt.raw, 7, heat.book.ladder());
    require(sqlite3_step(stmt.raw), db_, "insert races");
    const int id = static_cast<int>(sqlite3_last_insert_rowid(db_));
    insert_call(id, "alchemy", "act", heat.alchemy);
    insert_call(id, "infura", "act", heat.infura);
    return id;
}
