#include "rpc.hpp"

#include <curl/curl.h>

#include <chrono>
#include <cctype>
#include <string>

namespace {

// Uniswap V2 USDC/WETH on Ethereum mainnet. getReserves() selector.
constexpr const char* kUsdcWethPair = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc";
constexpr const char* kGetReserves = "0x0902f1ac";

struct CurlEasy {
    CURL* handle = nullptr;
    CurlEasy() : handle(curl_easy_init()) {}
    ~CurlEasy() {
        if (handle != nullptr) {
            curl_easy_cleanup(handle);
        }
    }
    CurlEasy(const CurlEasy&) = delete;
    CurlEasy& operator=(const CurlEasy&) = delete;
};

size_t write_cb(char* ptr, size_t size, size_t nmemb, void* userdata) {
    auto* out = static_cast<std::string*>(userdata);
    const size_t bytes = size * nmemb;
    if (out->size() + bytes > 1000000) {
        return 0;
    }
    out->append(ptr, bytes);
    return bytes;
}

bool leaks_secret(const std::string& text) {
    return text.find("http") != std::string::npos || text.find("alchemy") != std::string::npos ||
           text.find("infura") != std::string::npos || text.find("v2/") != std::string::npos ||
           text.find("v3/") != std::string::npos;
}

std::string json_string_field(const std::string& body, const std::string& key) {
    const std::string token = "\"" + key + "\"";
    const auto key_at = body.find(token);
    if (key_at == std::string::npos) {
        return {};
    }
    const auto colon = body.find(':', key_at + token.size());
    if (colon == std::string::npos) {
        return {};
    }
    const auto quote = body.find('"', colon + 1);
    if (quote == std::string::npos) {
        return {};
    }
    std::string out;
    for (size_t i = quote + 1; i < body.size(); ++i) {
        if (body[i] == '\\' && i + 1 < body.size()) {
            out.push_back(body[++i]);
            continue;
        }
        if (body[i] == '"') {
            break;
        }
        out.push_back(body[i]);
    }
    return out;
}

bool hex_result(const std::string& value) {
    if (value.size() < 3 || value[0] != '0' || (value[1] != 'x' && value[1] != 'X')) {
        return false;
    }
    for (size_t i = 2; i < value.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(value[i]);
        const bool hex = std::isxdigit(c) != 0;
        if (!hex) {
            return false;
        }
    }
    return true;
}

std::string clip(std::string text, size_t max) {
    if (text.size() > max) {
        text.resize(max);
    }
    return text;
}

RpcResult post_json(const Provider& provider, const std::string& method, const std::string& body) {
    // provider.url contains the API key. Never put that URL in a result,
    // an error string, or the database.
    RpcResult result;
    result.method = method;

    CurlEasy curl;
    if (curl.handle == nullptr) {
        result.error = "could not start the HTTP client";
        return result;
    }

    std::string response;
    char errbuf[CURL_ERROR_SIZE];
    errbuf[0] = '\0';
    curl_slist* headers = curl_slist_append(nullptr, "Content-Type: application/json");

    curl_easy_setopt(curl.handle, CURLOPT_URL, provider.url.c_str());
    curl_easy_setopt(curl.handle, CURLOPT_POST, 1L);
    curl_easy_setopt(curl.handle, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl.handle, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl.handle, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(curl.handle, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl.handle, CURLOPT_ERRORBUFFER, errbuf);
    curl_easy_setopt(curl.handle, CURLOPT_TIMEOUT, 8L);
    curl_easy_setopt(curl.handle, CURLOPT_CONNECTTIMEOUT, 5L);
    curl_easy_setopt(curl.handle, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl.handle, CURLOPT_USERAGENT, "provider-derby/0.1");
    curl_easy_setopt(curl.handle, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(curl.handle, CURLOPT_SSL_VERIFYHOST, 2L);
    curl_easy_setopt(curl.handle, CURLOPT_FOLLOWLOCATION, 0L);

    const auto t0 = std::chrono::steady_clock::now();
    const CURLcode rc = curl_easy_perform(curl.handle);
    const auto t1 = std::chrono::steady_clock::now();
    result.duration_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    curl_slist_free_all(headers);

    if (rc != CURLE_OK) {
        result.error = errbuf[0] != '\0' ? errbuf : curl_easy_strerror(rc);
        if (leaks_secret(result.error)) {
            result.error = "connection failed";
        }
        result.error = clip(result.error, 180);
        return result;
    }

    long http = 0;
    curl_easy_getinfo(curl.handle, CURLINFO_RESPONSE_CODE, &http);
    result.http_status = http;

    const ParsedRpc parsed = parse_rpc_body(response);
    result.result_preview = parsed.result;
    result.error = parsed.error;
    if (http != 200 && result.error.empty()) {
        result.error = "HTTP " + std::to_string(http);
    }
    result.ok = http == 200 && result.error.empty() && !result.result_preview.empty();
    return result;
}

}  // namespace

std::string trim_copy(const std::string& value) {
    size_t begin = 0;
    while (begin < value.size() &&
           std::isspace(static_cast<unsigned char>(value[begin])) != 0) {
        ++begin;
    }
    size_t end = value.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(value[end - 1])) != 0) {
        --end;
    }
    return value.substr(begin, end - begin);
}

bool key_fits_url_path(const std::string& key) {
    if (key.empty()) {
        return false;
    }
    for (unsigned char c : key) {
        const bool ok = std::isalnum(c) != 0 || c == '_' || c == '-';
        if (!ok) {
            return false;
        }
    }
    return true;
}

std::string alchemy_url(const std::string& key) {
    return "https://eth-mainnet.g.alchemy.com/v2/" + key;
}

std::string infura_url(const std::string& key) {
    return "https://mainnet.infura.io/v3/" + key;
}

ParsedRpc parse_rpc_body(const std::string& body) {
    ParsedRpc out;
    if (body.empty() || body[0] != '{') {
        out.error = "unreadable response";
        return out;
    }
    if (body.find("\"error\"") != std::string::npos) {
        const std::string message = json_string_field(body, "message");
        if (message.empty() || leaks_secret(message)) {
            out.error = "provider rejected the call";
        } else {
            out.error = clip(message, 180);
        }
        return out;
    }
    std::string result = json_string_field(body, "result");
    if (!hex_result(result)) {
        out.error = "missing result";
        return out;
    }
    out.result = clip(result, 220);
    return out;
}

RpcResult eth_block_number(const Provider& provider) {
    const std::string body =
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_blockNumber\",\"params\":[]}";
    return post_json(provider, "eth_blockNumber", body);
}

RpcResult eth_get_reserves(const Provider& provider) {
    const std::string body = std::string("{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_call\",\"params\":[{\"to\":\"") +
                              kUsdcWethPair + "\",\"data\":\"" + kGetReserves + "\"},\"latest\"]}";
    return post_json(provider, "eth_call", body);
}
