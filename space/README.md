---
title: Flint Arcade
emoji: 🕹️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
license: apache-2.0
short_description: Describe a strange game. Play it. Make it stranger.
models:
  - RecursiveSI/flint
datasets:
  - alphabell/Flint-Arcade-Trajectories
tags:
  - flint
  - code-generation
  - browser-games
  - game-generation
  - agents
  - alphaBell
---

# Flint Arcade by alphaBell

Describe a tiny world. Flint writes the game. Play it, share the result and remix it with another sentence.

This Space presents the live [alphaBell Arcade](https://alphabell.com/arcade/) through a bounded server-side gateway. Model inference and durable game storage run on the alphaBell application backend. The initial release uses stock [Recursive Flint](https://huggingface.co/RecursiveSI/flint) Q8_0 with browser feedback and repair. Each build checks the selected inference model's metadata and requires 8-bit Q8_0.

New games adapt a small tested alphaBell foundation for input, rendering and game state. Flint returns a complete standalone HTML game, which the browser checker exercises before publication. Remixes use the actual parent game's code. There is no Arcade-trained adapter in this release.

Try [Moon Garden](https://alphabell.com/arcade/g/moon-garden), then give it one more twist. The [generation and repair dataset](https://huggingface.co/datasets/alphabell/Flint-Arcade-Trajectories) records alphaBell-authored briefs, actual candidate code, measured feedback and generation budgets, including failures. It excludes raw reasoning and infrastructure identifiers.

Published games are public. Keep private information out of your brief. Training-data contribution is optional and disabled by default. Browser smoke checks do not establish that every game is complete or winnable.

The gateway has no Hugging Face token. Its `ARCADE_EDGE_SECRET` Space secret signs a timestamp and a pseudonymous client identifier for backend quota enforcement. It never forwards raw IP addresses or exposes the signing secret to the browser. The primary backend enforces a durable global limit of 100 accepted builds per rolling 24 hours and a client limit of three per hour. The Space adds transient limits of three forwarded requests per client per hour and thirty total per rolling day; its counters reset on a Space restart.

[Source and evaluation method](https://github.com/alphabell-research/flint-arcade) · [alphaBell](https://alphabell.com)
