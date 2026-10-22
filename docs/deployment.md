# Deployment architecture

The web application and game validator are separate from model inference. The application sends prompts to a configured OpenAI-compatible chat-completions endpoint. Flint Q8_0 weights stay on the inference host. A browser visitor never receives that endpoint's credential.

## Hugging Face

A Docker Space can host the application and its validation worker. Listen on `0.0.0.0` at the port declared in the Space configuration, conventionally 7860. Store authentication values in Space secrets. Run one application worker when using an in-process queue or local SQLite state.

The default CPU environment has 2 CPU cores, 16 GB memory and 50 GB ephemeral disk. It can support a lightweight web service and bounded browser checks; it is not the inference host for the full Flint Q8_0 checkpoint. Compute Spaces require a qualifying paid plan to create. [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker).

Space container storage can disappear on restart. Use a durable external database or a mounted storage bucket for game records and generated files before promising permanent share links. Keep the release's bundled gallery independent from mutable visitor storage. [Space storage](https://huggingface.co/docs/hub/spaces-storage).

A static Space can embed an externally hosted application or serve its public frontend. Static files cannot safely keep an inference credential. All generation requests must still reach a server that enforces authentication, request limits and storage rules. [Static Spaces](https://huggingface.co/docs/hub/spaces-sdks-static).

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
