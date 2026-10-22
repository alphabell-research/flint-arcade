---
title: Flint Arcade
emoji: 🕹️
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
license: apache-2.0
short_description: Describe a strange game. Play it. Make it stranger.
---

# Flint Arcade by alphaBell

Describe a tiny world. Flint writes the game. Play it, share the result and remix it with another sentence.

This Space presents the live [alphaBell Arcade](https://alphabell.com/arcade/) through a bounded server-side gateway. Model inference and durable game storage run on the alphaBell application backend. The initial release uses stock Flint Q8_0 with browser feedback and repair; it does not include an Arcade-trained adapter.

Published games are public. Keep private information out of your brief. Training-data contribution is optional and disabled by default. Browser smoke checks do not establish that every game is complete or winnable.

The gateway has no Hugging Face token. Its `ARCADE_EDGE_SECRET` Space secret signs a timestamp and a pseudonymous client identifier for backend quota enforcement. It never forwards raw IP addresses or exposes the signing secret to the browser. A single gateway worker limits builds to three per client per hour and thirty total per rolling day, in addition to backend limits.

[Source and evaluation method](https://github.com/alphabell-research/flint-arcade) · [alphaBell](https://alphabell.com)
