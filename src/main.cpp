#include "rpc.hpp"
#include "store.hpp"
#include "tui.hpp"

#include <curl/curl.h>

#include <cstdlib>
#include <iostream>
#include <string>
#include <unistd.h>

namespace {

void usage() {
    std::cerr
        << "provider derby races Alchemy and Infura when a book pattern fires\n"
        << "\n"
        << "Usage: derby [--once] [--plain] [--tui] [--exit-after] [--heats N] [--db PATH]\n"
        << "\n"
        << "Keys come from the environment. ./scripts/race.sh reads them with op read.\n"
        << "Run that script from the repo root so the scoreboard keeps your terminal.\n"
        << "\n"
        << "This sends no orders. A fill is the first successful read.\n";
}

struct KeyRead {
    bool present = false;
    bool safe = false;
    std::string value;
};

KeyRead read_key(const char* name) {
    KeyRead out;
    const char* raw = std::getenv(name);
    if (raw == nullptr) {
        return out;
    }
    out.value = trim_copy(raw);
    out.present = !out.value.empty();
    out.safe = out.present && key_fits_url_path(out.value);
    return out;
}

void wipe(std::string& value) {
    value.assign(value.size(), '\0');
    value.clear();
}

}  // namespace

int main(int argc, char** argv) {
    bool once = false;
    bool plain = false;
    bool tui = false;
    bool exit_after = false;
    int heats = -1;
    std::string db_path = "derby.sqlite";

    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--once") {
            once = true;
        } else if (arg == "--plain") {
            plain = true;
        } else if (arg == "--tui") {
            tui = true;
        } else if (arg == "--exit-after") {
            exit_after = true;
        } else if (arg == "--heats" || arg == "--db") {
            if (i + 1 >= argc) {
                std::cerr << arg << " needs a value\n";
                return 2;
            }
            const std::string value = argv[++i];
            if (arg == "--heats") {
                heats = std::atoi(value.c_str());
            } else {
                db_path = value;
            }
        } else if (arg == "--help" || arg == "-h") {
            usage();
            return 0;
        } else {
            std::cerr << "Unknown argument: " << arg << "\n";
            usage();
            return 2;
        }
    }

    if (plain && tui) {
        std::cerr << "Use only one of --plain and --tui.\n";
        return 2;
    }
    if (heats == 0 || heats > 20 || heats < -1) {
        std::cerr << "--heats must be from 1 to 20.\n";
        return 2;
    }
    if (heats < 0) {
        heats = once ? 1 : 5;
    }

    KeyRead alchemy_key = read_key("ALCHEMY_API_KEY");
    KeyRead infura_key = read_key("INFURA_API_KEY");
    if (!alchemy_key.present || !infura_key.present || !alchemy_key.safe || !infura_key.safe) {
        if (!alchemy_key.present) {
            std::cerr << "Missing ALCHEMY_API_KEY.\n";
        } else if (!alchemy_key.safe) {
            std::cerr << "ALCHEMY_API_KEY has characters that cannot go in a URL path.\n";
        }
        if (!infura_key.present) {
            std::cerr << "Missing INFURA_API_KEY.\n";
        } else if (!infura_key.safe) {
            std::cerr << "INFURA_API_KEY has characters that cannot go in a URL path.\n";
        }
        usage();
        return 2;
    }

    curl_global_init(CURL_GLOBAL_DEFAULT);
    struct CurlGuard {
        ~CurlGuard() { curl_global_cleanup(); }
    } curl_guard;

    try {
        Provider alchemy{"alchemy", alchemy_url(alchemy_key.value)};
        Provider infura{"infura", infura_url(infura_key.value)};
        wipe(alchemy_key.value);
        wipe(infura_key.value);

        Store store(db_path);
        SessionOptions options;
        options.heats = heats;
        options.animate = !once;
        options.exit_after = exit_after || once;
        options.db_path = db_path;

        const bool use_tui = tui || (!plain && !once && isatty(STDOUT_FILENO) != 0);
        const Score score = use_tui ? run_tui(store, alchemy, infura, options)
                                     : run_plain(store, alchemy, infura, options);
        if (score.quit_early) {
            return 0;
        }
        if (score.ran == 0) {
            return 1;
        }
        if (score.alchemy_wins + score.infura_wins + score.ties == 0) {
            return 1;
        }
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << ex.what() << "\n";
        return 1;
    }
}
