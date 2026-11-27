"""Direct CLI runner for this Mac.

Independent of the dispatch runner in ``agents/``: no Firstmate, no Herdr,
no tmux-axi. Each job is a fresh detached tmux session on a private socket.
"""
