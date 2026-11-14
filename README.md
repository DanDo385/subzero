# Provider Derby

Two Ethereum feeds, one order book, a paper fill for whoever answers first.

Alchemy and Infura get the same signal at the same moment. The signal is a short pattern on a limit order book: the best three bid levels are at least twice the best three ask levels, and the spread is two ticks or tighter. Both providers then read Uniswap V2 USDC/WETH reserves. The first good response gets the fill. Every call is saved in `derby.sqlite`.

This is a showcase you can run in a terminal. It does not sign transactions and it does not send orders.

The C++ compiler on this Mac is already installed (Apple clang, via the Command Line Tools). The walkthrough is [docs/guides/00-build-and-race.md](docs/guides/00-build-and-race.md).

## Run it

```bash
make test
cp deploy/keys.op.example deploy/keys.op   # first time only
make                        # builds build/derby
./scripts/race.sh           # five-heat scoreboard, q to quit
./scripts/race.sh --once    # one heat, plain text
make inspect                # print the sqlite scoreboard
```

`scripts/race.sh` loads the keys with `op read` and the references in `deploy/keys.op`. The key text stays out of the repo and out of the program's logs. The script starts the scoreboard directly, so the terminal stays attached.

## Look at the calls

- `make inspect` runs [docs/queries.sql](docs/queries.sql) against `derby.sqlite`.
- Import [postman/Provider-Derby.postman_collection.json](postman/Provider-Derby.postman_collection.json) to send the same two JSON-RPC methods by hand. Details: [docs/guides/01-inspect-the-calls.md](docs/guides/01-inspect-the-calls.md).

## When it fails

[docs/troubleshooting.md](docs/troubleshooting.md) lists the messages the program prints and what to do.

## Layout

| Path | What it is |
| --- | --- |
| `src/book.cpp` | The limit book and the `bid_stack` pattern |
| `src/race.cpp` | Start both providers together and pick the fill |
| `src/rpc.cpp` | JSON-RPC over HTTPS |
| `src/store.cpp` | sqlite |
| `src/tui.cpp` | The scoreboard |
| `schema.sql` | Tables `races` and `api_calls` |
| `deploy/keys.op.example` | `op://` references, no secret values |

MIT license, see [LICENSE](LICENSE).
