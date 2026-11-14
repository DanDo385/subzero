# Look at the calls

Every check and every heat is a row. You can look at the same fact three ways: the terminal, a sqlite browser, and Postman for a single call outside the race.

## sqlite from the terminal

After at least one run:

```bash
make inspect
```

That opens `derby.sqlite` and runs `docs/queries.sql`. You should see:

- `provider_stats`: one row per provider and role (`check` or `act`), with average, best, and worst milliseconds
- `scoreboard`: fills per provider
- `races`: the pattern, the paper side, the price, the winner
- `calls`: every HTTP attempt, including failures

A check row has an empty race id. An act row points at a race. `result_preview` for a check is a block number like `0x15f3abc`. For an act it is the ABI hex of `getReserves` (USDC reserve, WETH reserve, timestamp). It is public chain data. It is not an API key.

Reset with `rm -f derby.sqlite` if you want a clean session.

## A sqlite app

DB Browser for SQLite, TablePlus, or the sqlite extension in your editor can open `derby.sqlite`. Paste `docs/queries.sql`. The file is a normal database. Nothing in it is a credential.

Useful questions:

- Which provider won more fills? `SELECT * FROM scoreboard;`
- Are the failures on one host? `SELECT provider, error, count(*) FROM api_calls WHERE ok = 0 GROUP BY provider, error;`
- How wide is the gap on acts? compare `avg_ms` in `provider_stats` where `role = 'act'`.

## Postman

Postman is for one call at a time, with the response body and the timing Postman measured. It does not read `derby.sqlite`. Use it when you want to see the JSON the C++ parser is reading.

1. Import `postman/Provider-Derby.postman_collection.json`.
2. Import `postman/Provider-Derby.postman_environment.json` and select that environment.
3. Fill `alchemy_api_key` and `infura_api_key` in the local environment only.

Put the key in Postman by hand. Do not commit it, and do not sync a Postman workspace after you send a request. The key is in the URL path, so Postman's history keeps the URL.

To copy a key into the clipboard without leaving it in the shell scrollback, you can run `op read` yourself and paste it into the environment field. Do not paste it into a file in this repo.

The collection has four requests:

| Request | Same code path as |
| --- | --- |
| Alchemy `eth_blockNumber` | connection check |
| Infura `eth_blockNumber` | connection check |
| Alchemy `getReserves` | the triggered act |
| Infura `getReserves` | the triggered act |

Each request has a test script: HTTP 200, and a hex `result`. Send a pair and compare the Time column. That is the same race, one heat, outside the TUI.

The reserves `data` field is `0x0902f1ac`, the 4-byte selector for `getReserves()`. The `to` address is the mainnet Uniswap V2 USDC/WETH pair.
