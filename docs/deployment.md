# Deployment architecture

The web application and game validator are separate from model inference. The application sends prompts to a configured OpenAI-compatible chat-completions endpoint. Flint Q8_0 weights stay on the inference host. A browser visitor never receives that endpoint's credential.

## Hugging Face

The selected deployment uses a Docker Space as a bounded same-origin gateway to [the canonical alphaBell application](https://alphabell.com/arcade/). The durable database and Bubblewrap browser validator stay on the alphaBell backend. The Space runs neither the model nor the browser validator and holds no Hugging Face token.

The gateway listens on port 7860 as a nonprivileged user. `ARCADE_ORIGIN` is restricted to the canonical alphaBell Arcade URL. `ARCADE_EDGE_SECRET` must match the backend secret. Keep it in Space secrets and outside source control. Run one gateway worker and one primary application worker.

The default CPU environment has 2 CPU cores, 16 GB memory and 50 GB ephemeral disk. It can support a lightweight web service and bounded browser checks; it is not the inference host for the full Flint Q8_0 checkpoint. Compute Spaces require a qualifying paid plan to create. [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker).

Space container storage can disappear on restart. This deployment keeps game records and generated files in the durable primary application, so a Space restart does not erase published games. Gateway admission counters are transient and reset on restart. [Space storage](https://huggingface.co/docs/hub/spaces-storage).

A static Space can embed an externally hosted application or serve its public frontend. Static files cannot safely keep an inference credential. All generation requests must still reach a server that enforces authentication, request limits and storage rules. [Static Spaces](https://huggingface.co/docs/hub/spaces-sdks-static).

## Quotas and edge identity

The primary backend uses `ARCADE_DAILY_CAP`, default 100, to enforce a durable global limit on accepted build jobs in a rolling 24-hour window. `ARCADE_PER_HOUR`, default three, limits individual clients. The Space adds process-local limits of three forwarded requests per client per hour and thirty total per rolling day. These additional gateway counters reset on a Space restart.

The Space sends a pseudonymous client hash rather than a raw address. It signs three headers with `ARCADE_EDGE_SECRET`:

- `X-Arcade-Edge-Timestamp`: decimal Unix seconds.
- `X-Arcade-Edge-Client`: a 64-character lowercase hexadecimal client pseudonym.
- `X-Arcade-Edge-Signature`: hexadecimal HMAC-SHA256 of the timestamp, a newline and the client pseudonym.

The primary backend requires the complete valid header set and accepts only a 60-second timestamp window. Forged, partial or expired edge identities fail closed. The gateway validates a browser's write origin before forwarding a canonical application origin, forwards only the ownership cookie and approved headers, and returns generic upstream failures. It rewrites the application base path, local redirects and ownership-cookie path for the Space origin.

For an application mounted below a domain root, set `ARCADE_ROOT_PATH` to its public prefix and `ARCADE_PUBLIC_ORIGIN` to the scheme and host. Configure the proxy path rewrite consistently. Allow only the intended Hugging Face parent origins in `ARCADE_FRAME_ANCESTORS`. Embedded job polling also needs a cookie policy compatible with that deployment; cross-site cookies require HTTPS and can be restricted by the visitor's browser. Verify the complete create-and-poll flow in the actual embedded Space.

## Serving checklist

- Terminate TLS and expose only the application, never the model management or browser debugging ports.
- Keep inference authentication on the server and use a narrowly scoped credential.
- Restrict accepted public origins and validate request sizes before queuing inference.
- Bound queue length, active inference work, repair count, browser time and output size.
- Return generic public errors; keep diagnostic traces in private server logs.
- Back up the durable game store and test restoration before changing its schema.
- Check embedded access through the actual Hugging Face Space, including share links and mobile controls.

Run the code's automated checks and a real generation, repair, remix, download and reload before release. Recheck that only alphaBell owns and authors the public project.

## Validator isolation

Generated HTML is untrusted code. The validator uses Bubblewrap to create isolated network, mount, process and user namespaces. It mounts the browser, Python environment, one input document and a writable output directory. It does not mount the host home or pass application credentials. The browser's native sandbox is disabled inside this outer isolation boundary. The validator fails closed when Bubblewrap is unavailable.

The worker bounds browser time, CPU, file size and captured output. Its process-tree memory monitor supplements the deployment service's hard cgroup memory limit. Deploy with a hard service memory and process limit; the monitor alone is not a complete resource boundary.

Playwright request interception and offline mode are useful additional controls. Disable service workers, abort unsolicited requests, close popups and deny downloads. Offline mode alone does not stop WebRTC traffic. A browser context is session isolation, not a replacement for an operating-system security boundary. [Browser contexts](https://playwright.dev/python/docs/api/class-browsercontext), [Playwright Docker guidance](https://playwright.dev/python/docs/docker).
