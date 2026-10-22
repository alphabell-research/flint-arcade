# Flint Arcade

**Describe a strange little game. Play it. Make it stranger.**

Flint Arcade by [alphaBell](https://alphabell.com) turns a short idea into a self-contained browser game. Flint writes the HTML, CSS and JavaScript. A browser check tries the result, and bounded repair rounds send failures back to Flint. Successful games get a playable page, a remix action and an HTML download.

Try a tiny gardener whose plants change gravity, a delivery game on a planet of jelly, or a city where the buildings move at night. Remix an existing game with one more twist.

## What is released

- A web playground with a public gallery, generation queue, share pages and remix lineage.
- An inference-and-repair pipeline for stock [Recursive Flint](https://huggingface.co/RecursiveSI/flint), served in 8-bit Q8_0 form.
- Browser validation tools and structured generation records for reproducible experiments.
- A method for building an alphaBell-authored game-and-repair dataset.

The initial release uses stock Flint. It does not include an Arcade-trained adapter or modified model weights. Browser checks exercise a bounded interaction sequence; they do not establish that every generated game is fun, complete or winnable. See [evaluation](docs/evaluation.md) for the comparison protocol.

## Run from source

Use Python 3.11 or later, from this checkout:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,sandbox]'
ARCADE_SECURE_COOKIE=false uvicorn arcade.app:create_app --factory --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860`. This starts the application with generation disabled. Generation needs a configured Flint inference endpoint and the isolated browser validator. Model weights are separate from the application package.

Run automated checks with:

```sh
pytest
```

## Configure a deployment

Keep deployment values outside source control. The principal application settings are:

| Variable | Purpose |
|---|---|
| `ARCADE_DATA_DIR` | Persistent SQLite state, queue and local signing key. |
| `ARCADE_GENERATION_ENABLED` | Enable requests after inference and validation are ready. |
| `ARCADE_UPSTREAM_URL` | Server-side OpenAI-compatible inference endpoint. |
| `ARCADE_UPSTREAM_API_KEY` | Server-side inference credential. |
| `ARCADE_UPSTREAM_MODEL` | Model identifier accepted by the inference server. |
| `ARCADE_UPSTREAM_REVISION` | Immutable model and quantization provenance recorded with builds. |
| `ARCADE_CHROMIUM` | Installed Chromium executable for the isolated Linux validator. |
| `ARCADE_QA_TIMEOUT` | Browser-check deadline. |
| `ARCADE_INFERENCE_TIMEOUT` | Inference request timeout. |
| `ARCADE_COMPLETION_TOKENS` | Maximum output token budget. |
| `ARCADE_REASONING_BUDGET` | Reasoning token budget supported by the inference runtime. |
| `ARCADE_PER_HOUR` | Hourly generation limit for a client. |
| `ARCADE_QUEUE_CAP` | Maximum pending and active builds. |
| `ARCADE_JOB_TIMEOUT` | Upper bound for one build. |
| `ARCADE_PUBLIC_ORIGIN` | Canonical public origin for write-request checks. |
| `ARCADE_ROOT_PATH` | Public path prefix when deployed below a domain root. |
| `ARCADE_FRAME_ANCESTORS` | Explicit origins allowed to embed the application. |
| `ARCADE_COOKIE_SAMESITE` | Job-cookie policy; `none` requires secure HTTPS cookies. |
| `ARCADE_TRUSTED_PROXY_HOPS` | Number of explicitly trusted proxy hops, default zero. |
| `ARCADE_TRUSTED_PROXY_CIDRS` | Required trusted proxy networks when using forwarded addresses. |
| `ARCADE_SOURCE_URL` | Public source link shown in the application. |
| `ARCADE_HUGGINGFACE_URL` | Public Hugging Face link shown in the application. |

Run one Uvicorn worker for the application queue. Put the data directory on durable storage, restrict its access and back it up. Do not expose model credentials in frontend configuration. See [deployment](docs/deployment.md) and [security](docs/security.md) before enabling anonymous generation.

Generation validation requires Linux, installed Bubblewrap and Chromium, and permission to create isolated user and network namespaces. The checker fails closed when that isolation is unavailable. Its Python environment must be a virtual environment. Use hard deployment memory and process limits alongside the checker watchdog.

To import a checked seed collection, run `python -m arcade.seed DIRECTORY`. Each JSON record must have an accepted QA result and contain its HTML or have a sibling HTML file. Import trusted alphaBell-generated records; this is a publication tool, not a validator.

## Games, contributions and research data

Published games are public. Keep personal information and secrets out of game briefs. A visitor's public game is not automatically a training contribution. The research dataset starts with alphaBell-authored prompts and actual generation traces. See [data collection and provenance](docs/data.md).

## License

The Arcade application and tools are licensed under [Apache 2.0](LICENSE). Upstream model weights retain their own license and provenance. The application license does not automatically license visitor prompts, generated games or a separately published dataset.
