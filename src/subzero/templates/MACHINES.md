# MACHINES.md

The machines agents can use, what each is for, and how to reach it. subzero puts this file into
every coding session's opening prompt so agents never guess a host name.

Edit me at ~/.config/subzero/MACHINES.md. Use tailnet names, never passwords or tokens.

| Name | Tailnet name | Role | Runs | Notes |
| --- | --- | --- | --- | --- |
| mbp | <mac-name>.<tailnet>.ts.net | personal node | subzero server, Obsidian, Anki, herdr | asleep sometimes |
| hetzner | <vps-name>.<tailnet>.ts.net | always-on workers | firstmate secondmate, herdr | Ubuntu |

## Rules
- Reach machines over Tailscale only; nothing is exposed on a public port except Funnel on the Mac.
- Long jobs go to hetzner; anything needing Obsidian or Anki runs on mbp.
