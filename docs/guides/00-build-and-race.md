# Build the race and run a heat

This is a small C++ program. It keeps a toy limit order book, waits until that book forms one pattern, then fires the same read at Alchemy and Infura at the same moment. The first good answer gets the paper fill. Both answers are stored in sqlite.

No real order is sent. Alchemy and Infura are Ethereum readers. They do not take trades. The "act" is an `eth_call` that reads Uniswap V2 USDC/WETH reserves. The thing you are measuring is which feed answers first from this Mac.

## 1. Check the compiler

```bash
clang++ --version
```

Success looks like:

```text
Apple clang version 14.0.3
```

The exact number can be newer. If the command is not found, use the Command Line Tools step in `docs/troubleshooting.md`.

The program links three libraries that ship with the Mac SDK. You do not install them separately.

| Library | Job |
| --- | --- |
| libcurl | HTTPS call to each provider |
| libsqlite3 | Save every call and every heat |
| libncurses | Draw the scoreboard in the terminal |

## 2. Build and test the book, without the network

From this folder:

```bash
make test
```

Success is three lines:

```text
book ok
race ok
store ok
```

`make test` does not use API keys. It checks the pattern, the winner rule, and that a heat lands in sqlite.

## 3. Point the keys at 1Password

```bash
cp deploy/keys.op.example deploy/keys.op
```

On this machine the example already matches the Dev vault items titled `Alchemy` and `Infura`. `scripts/race.sh` reads each `op://` reference with `op read`, exports it for the race only, and does not echo it. The binary also refuses to print the URL, because the key is part of the URL.

`op run` is a fine way to inject secrets for a plain command, and a poor way to start this scoreboard: it puts a pipe in front of the program, and a pipe is not a terminal. Use the script.

## 4. Run one heat in plain text

```bash
make
./scripts/race.sh --once
```

Success looks like this shape. Your milliseconds will differ.

```text
Provider Derby
paper race. same signal, two feeds. first response fills.

connection check  (eth_blockNumber)
  alchemy  ok      140.0 ms    0x...
  infura   ok      180.0 ms    0x...
  same head

book
  ask 103 x 8
  ask 102 x 5
  ask 101 x 5
  --- spread 2 ---
  bid 99 x 28
  bid 98 x 4
  bid 97 x 4

pattern bid_stack fired
paper action: buy 1 @ 101

heat 1/1  (eth_call getReserves on USDC/WETH)
  alchemy  sent    141.0 ms  fill
  infura   sent    190.0 ms  behind
  winner: alchemy

champion: alchemy   fills alchemy 1  infura 0  ties 0
saved: derby.sqlite
```

A fill means that lane's HTTP call returned first with a usable answer. The other lane still acted. Both rows are in the database.

## 5. Run the scoreboard

```bash
./scripts/race.sh
```

That is the TUI: five heats, a book that builds on screen, then both lanes. `q` quits. The window needs at least 78 columns and 20 rows.

`./scripts/race.sh --heats 3` changes the length. The cap is 20 heats so a typo cannot hammer the providers.

## What the C++ is doing

Read the files in this order.

1. `src/book.cpp` holds the bids and asks. `bid_stack` is the whole strategy: the top three bid levels together are at least twice the top three ask levels, and the spread is 2 ticks or tighter. `showcase_frames` is a four-step tape that starts calm and ends on that pattern, so a demo does not sit waiting for a market to move.
2. `src/race.cpp` starts two threads. Each thread does the same call. `pick_winner` compares the two clocks. A gap of 0.5 ms or less is a tie, because this is a laptop clock around an HTTP client, not a colocated wire.
3. `src/rpc.cpp` speaks JSON-RPC. The check is `eth_blockNumber`. The act is `eth_call` `getReserves()` on the USDC/WETH v2 pair `0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc`. The body that comes back is kept only when it is a hex result. A raw HTML page is stored as "unreadable response" so a page that echoes the URL cannot land in sqlite.
4. `src/store.cpp` writes `races` and `api_calls`. Checks have no race id. Acts point at the heat.
5. `src/tui.cpp` draws the board. `src/main.cpp` only reads flags and keys, then hands off.

```text
scripted tape
    |
    v
limit order book -- bid_stack? -- not yet --> next frame
    |
    yes, buy 1 at the best ask (paper)
    |
    +--> Alchemy eth_call getReserves --+
    |                                    +--> first good answer fills
    +--> Infura  eth_call getReserves --+
    |
    v
sqlite: races + api_calls
```

The book and the chain read are different on purpose. The book is local, so both lanes share one signal and start together. The chain read is the real network race, and it is the same request on both sides, so the winner is latency from this machine.

## Rebuild after you edit

```bash
make test
./scripts/race.sh --once
```

`make clean` deletes `build/` only. It leaves `derby.sqlite` alone.
