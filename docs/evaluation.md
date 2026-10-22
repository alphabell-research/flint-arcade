# What Arcade checks

Flint Arcade pairs generation with an executable browser check. A game that loads without an exception can still have unclear controls, a broken objective or an unwinnable level. Browser smoke tests are evidence about the actions they execute.

## A reproducible comparison

Use the same stock Flint Q8_0 checkpoint, runtime, prompts, sampling settings and token budget for both conditions:

1. **First attempt:** the initial generated game.
2. **With repair:** the same attempt after a bounded number of feedback-and-repair rounds.

Keep every sampled attempt, including timeouts, rejected code, truncated generations and exhausted repairs. Record the model repository and immutable revision, GGUF filename and SHA256, inference-runtime revision, context length, decoding parameters, generation tokens, latency and validator version. Record the exact brief and any parent game used for a remix.

Report first-attempt and final success separately, with counts and denominators. A selected gallery demonstrates possibilities; it does not estimate success across all requests. Do not describe repair gains as a fine-tuning gain.

## Test dimensions

| Dimension | Evidence |
|---|---|
| Loads | A complete HTML document opens within the deadline. |
| Runtime | No captured uncaught exceptions during the exercised sequence. |
| Presentation | A nonempty game surface appears at desktop and mobile sizes. |
| Interaction | Keyboard or pointer input produces an observable state change. |
| Objective | Task-specific assertions check score, progress or a reachable end condition. |
| Restart | Restart returns the game to an initial playable state. |
| Self-contained | The game requires no external requests or runtime dependencies. |
| Remix | The requested change works while required original mechanics remain. |

Generic visual changes or a canvas repaint do not establish progress, correct controls or a winning condition. Tests that lack task-specific assertions should be labeled browser smoke tests.

## Held-out briefs

Split by mechanic family before building training data. Keep related prompts and remixes together. Include platformers, puzzles, survival games, timing games, resource management, unusual input mechanics and small simulation games. Reserve an untouched set for a final evaluation.

For deeper playability claims, publish human judgments of controls, objective clarity, completion and enjoyment, with the review instructions and sample selection. Report human and automatic results separately.

## A future adapter

The initial system uses stock Flint with no Arcade-trained adapter. A later adapter needs a separate release with its training recipe, data revision and held-out comparison against the same stock model and harness. Training on successful repairs is a research direction until that experiment has actually run.
