# Running generated games

Arcade treats model-produced HTML, JavaScript, titles, descriptions and feedback as untrusted input. The same rule applies to code from a previous game when building a remix.

## Player boundary

Embed the game in a sandboxed iframe that permits scripts but does not permit same-origin access, top navigation, forms or popups. Add a restrictive content-security policy before any generated content executes. Restrict outgoing connections, frames, objects, form actions and base URLs. Keep the game separate from application cookies and API credentials.

Serve game HTML with an HTTP content-security-policy header as well as the iframe sandbox. An HTML download should carry its own restrictive policy near the start of the document. A downloaded game is still code; opening it outside the Arcade player changes the surrounding security boundary.

Render generated metadata with text APIs. Never concatenate a title, prompt or model error into application HTML. Resolve game IDs against a fixed format rather than accepting filesystem paths or remote URLs.

## Server boundary

The generation endpoint accepts a brief and an optional existing game ID. It must not accept a caller-supplied inference URL, system prompt, arbitrary filename or command. Keep model credentials out of prompts, client responses, screenshots, generated code and research exports.

Use bounded request bodies, bounded queues, global capacity limits and per-client limits. Treat forwarded IP headers as trusted only behind a configured trusted proxy. Do not retain raw IP addresses as research data. Limit public error messages to a stable failure category and a retry instruction.

## Validation boundary

Run the browser inside the selected Bubblewrap boundary: isolated network, mount, process and user namespaces, with no host home or application environment. Its namespace-local root identity is not host root. The browser's native sandbox is disabled inside that outer boundary. Do not give the validator model tokens, cloud credentials or a browser profile that contains an existing login. Close the entire browser process when the deadline expires, including games that enter an infinite loop. Apply hard service memory and process limits in addition to the worker's watchdog.

Use a fresh context for each attempt, block service workers, deny permissions and unsolicited network requests, disable WebRTC, handle dialogs, close popups and bound captured log output. Never execute generated Python, shell commands or package installation instructions to test a browser game.

## Release checks

Verify malicious fixtures that attempt application-origin access, navigation, remote fetches, WebSockets, workers, popups, downloads, giant output and infinite loops. Verify that a failed attempt cannot retrieve stored secrets or another user's private job token. Verify that public source, Git history, release metadata and application responses identify only alphaBell and legitimate upstream dependencies.

These are the security properties the deployment must verify. Passing a normal game smoke test does not establish them.
