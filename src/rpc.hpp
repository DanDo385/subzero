#pragma once

#include <string>

struct RpcResult {
    std::string method;
    bool ok = false;
    long http_status = 0;
    double duration_ms = 0;
    std::string result_preview;
    std::string error;
};

struct Provider {
    std::string name;
    std::string url;
};

struct ParsedRpc {
    std::string result;
    std::string error;
};

std::string trim_copy(const std::string& value);
bool key_fits_url_path(const std::string& key);
std::string alchemy_url(const std::string& key);
std::string infura_url(const std::string& key);

ParsedRpc parse_rpc_body(const std::string& body);

RpcResult eth_block_number(const Provider& provider);
RpcResult eth_get_reserves(const Provider& provider);
