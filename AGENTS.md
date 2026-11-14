# Provider Derby

This branch is a showcase: a terminal race between Alchemy and Infura.

- Paper trades only. The program never signs or sends a transaction.
- API keys come from `op read` inside `scripts/race.sh`. Do not print them, log them, or commit them. Do not start the scoreboard under `op run`: that pipe is not a terminal.
- The provider URL contains the key. Errors and database rows must not include that URL.
- Logic stays out of `src/main.cpp`. The book, the race, the HTTP calls, and sqlite are separate files.
- `make test` must pass. A plain `./scripts/race.sh --once` must record both providers before you call a change done.
- The memory server is still on `main` and the other worktrees. Do not delete those branches.
