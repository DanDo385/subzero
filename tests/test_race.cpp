#include "race.hpp"

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

RpcResult ok_call(double ms) {
    RpcResult result;
    result.ok = true;
    result.http_status = 200;
    result.duration_ms = ms;
    result.method = "eth_call";
    result.result_preview = "0x10";
    return result;
}

}  // namespace

#define CHECK(cond) check((cond), __FILE__, __LINE__, #cond)

int main() {
    CHECK(pick_winner(ok_call(10), ok_call(20)) == "alchemy");
    CHECK(pick_winner(ok_call(20), ok_call(10)) == "infura");
    CHECK(pick_winner(ok_call(10), ok_call(10.5)) == "tie");
    CHECK(pick_winner(ok_call(10), ok_call(10.51)) == "alchemy");

    RpcResult failed;
    failed.method = "eth_call";
    failed.error = "connection failed";
    CHECK(pick_winner(ok_call(10), failed) == "alchemy");
    CHECK(pick_winner(failed, ok_call(10)) == "infura");
    CHECK(pick_winner(failed, failed) == "none");

    RpcResult same = ok_call(1);
    same.method = "eth_blockNumber";
    same.result_preview = "0x10";
    RpcResult other = same;
    other.result_preview = "0x12";
    CHECK(head_note(same, same) == "same head");
    CHECK(head_note(same, other) == "heads differ by 2 blocks");
    CHECK(head_note(same, failed) == "one feed failed the check");

    Score score;
    Heat heat;
    heat.winner = "alchemy";
    heat.alchemy = ok_call(12);
    heat.infura = ok_call(18);
    tally(score, heat);
    heat.winner = "alchemy";
    heat.alchemy = ok_call(9);
    heat.infura = ok_call(11);
    tally(score, heat);
    CHECK(score.alchemy_wins == 2);
    CHECK(score.alchemy_best == 9);
    CHECK(champion_line(score).find("champion: alchemy") == 0);

    Score tied;
    Heat left;
    left.winner = "tie";
    left.alchemy = ok_call(15);
    left.infura = ok_call(15);
    tally(tied, left);
    CHECK(champion_line(tied).find("champion: tie") == 0);

    CHECK(key_fits_url_path("abcDEF0123"));
    CHECK(!key_fits_url_path(""));
    CHECK(!key_fits_url_path("has space"));
    CHECK(!key_fits_url_path("a/b"));
    CHECK(trim_copy("  abc\n") == "abc");
    CHECK(alchemy_url("abc") == "https://eth-mainnet.g.alchemy.com/v2/abc");
    CHECK(infura_url("abc") == "https://mainnet.infura.io/v3/abc");

    const ParsedRpc good = parse_rpc_body("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":\"0x10\"}");
    CHECK(good.error.empty());
    CHECK(good.result == "0x10");

    const ParsedRpc spaced =
        parse_rpc_body("{\"jsonrpc\":\"2.0\",\"id\":1,\"result\": \"0xab\"}");
    CHECK(spaced.result == "0xab");

    const ParsedRpc rejected = parse_rpc_body(
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"error\":{\"code\":401,\"message\":\"Must be authenticated!\"}}");
    CHECK(rejected.result.empty());
    CHECK(rejected.error == "Must be authenticated!");

    const ParsedRpc leak = parse_rpc_body(
        "{\"error\":{\"message\":\"see https://eth-mainnet.g.alchemy.com/v2/SECRET\"}}");
    CHECK(leak.error.find("SECRET") == std::string::npos);
    CHECK(leak.error == "provider rejected the call");

    const ParsedRpc html = parse_rpc_body("<html>no key here</html>");
    CHECK(html.result.empty());
    CHECK(html.error == "unreadable response");
    CHECK(html.error.find("no key") == std::string::npos);

    if (g_failed != 0) {
        std::fprintf(stderr, "%d race checks failed\n", g_failed);
        return 1;
    }
    std::printf("race ok\n");
    return 0;
}
