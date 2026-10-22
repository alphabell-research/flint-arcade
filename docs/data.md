# Games and repair trajectories

Arcade can produce a useful record of how a coding model builds and repairs small games. The training value comes from exact code revisions and executable feedback, including the failures before a successful repair.

## Release scope

Publish alphaBell-authored briefs and their generated results as a curated research dataset. Public visitor games are a separate product feature. A public share link is not blanket consent to include someone's prompt or game in a training dataset.

Visitor contributions require an explicit contribution action, clear applicable terms and a privacy review before inclusion. Keep visitor prompts out of research exports unless that process is implemented. Remove secrets, personal information, third-party credentials and content without suitable redistribution rights. Never include access logs, IP addresses, session identifiers or server diagnostics.

## Useful fields

Each record should identify:

- The game and its parent, if it is a remix.
- The brief, creation source and applicable contribution license.
- The source model, immutable model revision and quantization artifact hash.
- Generation settings and the system instruction version.
- Every generated candidate, its SHA256 and attempt index.
- Structured validator observations for that attempt.
- The repair feedback and resulting replacement candidate.
- The selected final attempt and the reason it was selected.
- The automatic checks performed and any separate human review.

Do not put a private inference endpoint, host identifier, local filesystem path, API token or hidden reasoning transcript into a public record. Publish the generated answer code and the bounded repair feedback needed to reproduce the pipeline.

## Honest labels

Use labels such as `browser_smoke_pass`, `runtime_errors` and `human_reviewed`. Avoid a generic `playable: true` label when only page loading and input smoke tests ran. If a dataset contains only selected successes, say so and publish the acceptance rule. A success-only collection cannot establish the model's overall failure rate.

## Training split

Group originals, repairs and remixes in the same split. Keep held-out mechanic families outside training and data selection. Deduplicate code and similar briefs across splits. A training-ready export should include a dataset card, machine-readable schema, release revision and a license for the actual included material.
