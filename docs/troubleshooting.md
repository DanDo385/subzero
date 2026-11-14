# When something fails

## `clang++: command not found`

The C++ compiler is not installed. On this Mac it comes with the Command Line Tools:

```bash
xcode-select --install
```

A window opens. Choose Install, wait, then run `clang++ --version`. Success is a line that starts with `Apple clang`.

This machine already has Apple clang 14.0.3. You do not need Homebrew or a separate C++ download.

## `Missing ALCHEMY_API_KEY` or `Missing INFURA_API_KEY`

The binary only sees keys that `scripts/race.sh` put in the environment with `op read`. From the repo root:

```bash
cp deploy/keys.op.example deploy/keys.op
./scripts/race.sh --once
```

`deploy/keys.op` holds `op://` references. It does not hold the key text. If your 1Password items use different titles, edit the references. Confirm a reference resolves without printing it:

```bash
op read "op://Dev/Alchemy/credential" >/dev/null && echo alchemy ok
op read "op://Dev/Infura/credential" >/dev/null && echo infura ok
```

Success is `alchemy ok` and `infura ok`. If `op` asks you to sign in, open the 1Password app and approve it.

## `ALCHEMY_API_KEY has characters that cannot go in a URL path`

The key is appended to the provider URL. The program accepts letters, digits, `_`, and `-` only, so a quote or a slash cannot change the URL. Check that the 1Password field is the credential and not a whole URL.

## `provider rejected the call` or `Must be authenticated!`

The HTTP call reached the provider and the provider refused it. The usual cause is a wrong key, or a key that is not enabled for Ethereum mainnet. The sqlite row stores the provider's message when that message does not contain a URL. Look at it with `make inspect`. The key itself is not in the row.

## `connection failed` or a timeout

The call did not finish within 8 seconds. Run one heat again. A single timeout does not wipe the other lane: if one provider answers, that lane gets the fill.

## `Could not read schema`

`make` bakes the path to `schema.sql` into the binary. If you move the folder, run `make clean && make` from the new location.

## `Could not open derby.sqlite`

The current directory is not writable, or the path passed to `--db` is wrong. Run from the repo root, or pass `--db` with a path you can write.

## Terminal is smaller than 78x20

The scoreboard needs 78 columns and 20 rows. Widen the window, or run `./scripts/race.sh --once` for the plain report.

## Heads differ by N blocks

Both calls worked. One provider returned an older block number. That is a real feed gap, not a crash. The race still scores whoever answered the reserves call first.

## Reset the scoreboard

```bash
rm -f derby.sqlite
```

The next run creates a new database and applies `schema.sql`.
