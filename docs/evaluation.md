# What Arcade checks

Flint Arcade pairs generation with an executable browser check. A game that loads without an exception can still have unclear controls, a broken objective or an unwinnable level. Browser smoke tests are evidence about the actions they execute.

New-game generation includes a tested alphaBell starter for input, state and rendering. Flint adapts that context into a complete standalone document. Remix generation instead includes the actual parent game. Report this foundation-assisted setup alongside the model and harness; it is part of the method.

Repair uses a fresh context containing the current source and measured errors. Flint returns schema-constrained JSON source edits at temperature 0.2, with a maximum of 4096 output tokens per repair. The current source is numbered. Each edited line must exist and appear only once in the patch; unchanged and oversized edits are rejected atomically. The repaired document must pass the same static and isolated browser checks before publication. There are at most two repair rounds. Initial HTML generation retains its original sampling settings and 8192-token budget. Preserve raw edit responses and reconstructed candidate hashes when comparing this method to the earlier full-document repair pipeline.

Bounded lexical hints highlight possible missing arguments in common Canvas calls so a repair can address multiple drawing errors together. These hints guide the model; they do not determine acceptance. The isolated browser tests remain the publication gate.

## A reproducible comparison

Use the same stock Flint Q8_0 checkpoint, runtime, prompts, sampling settings and token budget for both conditions:

1. **First attempt:** the initial generated game.
2. **With repair:** the same attempt after a bounded number of feedback-and-repair rounds.

Keep the same starter revision in both conditions. Separate runs made before a starter or prompt change into a different cohort. A comparison that changes the foundation as well as enabling repair does not isolate the repair effect. The pipeline requires the inference server's selected model metadata to report `Q8_0` before every build.

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
