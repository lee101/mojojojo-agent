# Models and prompt caching

MJJ offers intent aliases so users do not need to memorize a changing model
catalog. The alias is resolved after credentials choose the transport, keeping
provider selection and model selection separate.

## Coding model map

| alias | OpenAI transport | OpenPaths transport | OpenRouter transport | recommended reasoning |
| --- | --- | --- | --- | --- |
| `auto-code` | `gpt-5.6-terra` | `openpaths/auto-code` | `openrouter/auto` | medium |
| `auto-fast` | `gpt-5.6-luna` | `openpaths/auto-fast` | `openrouter/auto` | low |
| `auto-cheap` | `gpt-5.6-luna` | `openpaths/auto-cheap` | `openrouter/auto` | low |
| `auto-best` | `gpt-5.6-sol` | `openpaths/auto-reasoning` | `openrouter/auto` | high |
| `auto-openai` | `gpt-5.6-terra` | `gpt-5.6-terra` | `openai/gpt-5.6-terra` | medium |
| `auto-openai-fast` | `gpt-5.6-luna` | `gpt-5.6-luna` | `openai/gpt-5.6-luna` | low |
| `auto-openai-best` | `gpt-5.6-sol` | `gpt-5.6-sol` | `openai/gpt-5.6-sol` | high |

OpenPaths-native aliases continue to use its live embedding router and fallback
catalog. The OpenAI-only aliases constrain the model lab while allowing
OpenPaths or OpenRouter to remain the billing and transport gateway. Custom
providers fall back to their configured default model.

Legacy OpenPaths-style intents remain accepted: `auto-medium-task` maps to
`auto-code`, `auto-easy-task` to `auto-cheap`, and `auto-hard-task` to
`auto-best`.

The reasoning column is a starting recommendation, not an override. An explicit
`--effort` or live `/reasoning` choice is preserved.

The map deliberately records tiers rather than dollar prices. Model prices,
availability, and gateway catalogs change independently. OpenAI's current
[model guidance](https://developers.openai.com/api/docs/guides/latest-model)
describes Sol as capability-first, Terra as balanced, and Luna as efficient
high-volume. Re-run project evals before changing a tier.

## Automatic cache policy

Prompt caches store provider-side KV tensors for an exact reusable prefix; they
do not store or replay an old answer. A cache hit reduces prompt prefill work,
while the model still generates a new response.

MJJ fingerprints the effective model, stable instructions, and sorted tool
schemas. Prefix tracking is bounded to 512 entries and a two-hour observation
window.

### OpenAI

For GPT-5.6 API-key requests, `auto` uses explicit cache mode with no breakpoint
on a cold prefix. This prevents an implicit one-shot write. When observed reuse
fits the current 30-minute TTL and repays the write premium, MJJ adds a stable
developer-message breakpoint and a prefix-derived `prompt_cache_key`. Prefixes
below the approximate 1,024-token minimum are not marked.

Earlier OpenAI models retain provider-managed automatic caching and the stable
session cache key. ChatGPT-plan traffic also keeps its compatible implicit path
because that backend may not expose the public API's explicit controls.

The behavior follows the official OpenAI
[prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching):
cache hits require exact stable prefixes, current cache writes and reads are
reported separately, and explicit mode avoids writes on an unmarked changing
suffix.

### Anthropic models through gateways

When a concrete Claude model is selected through OpenPaths or OpenRouter, MJJ
marks the stable system/tool prefix with an ephemeral cache control. The timing
optimizer chooses:

- 5 minutes for tightly repeated prefixes;
- 1 hour when reuse is spaced beyond five minutes but remains within an hour;
- no write when reuse is too sparse to repay the write premium.

This mirrors OpenPaths' prompt-cache optimizer. When an OpenPaths auto route
selects Anthropic internally, OpenPaths owns the same decision server-side.
Explicit caller controls are preserved by that gateway.

## Controls and telemetry

`MJJ_CACHE_MODE` and `/cache` accept:

| mode | behavior |
| --- | --- |
| `auto` | observe reuse and choose the cost-aware policy |
| `off` | do not add cache keys or explicit breakpoints |
| `implicit` | leave breakpoint selection to the provider |
| `explicit` | mark every cacheable stable prefix |

`/cache` reports tracked prefixes plus realized read/write tokens. `/usage`
shows cache hit percentage and writes alongside input, output, and reasoning
tokens. Chat-completions usage is normalized from both OpenAI-style and
Anthropic-style cache fields.

The hosted agent keeps this bounded reuse tracker for the service-process
lifetime instead of resetting it for every run. Independent runs with the same
model, instructions, and tool contract can therefore inform the automatic cost
decision. The tracker retains only prefix hashes and timestamps, not prompts or
tool arguments.

Automatic policy is conservative, not clairvoyant: a marked prefix can still
miss due to eviction, routing, minimum-length rules, or a changed tool schema.
Use telemetry and representative evals to decide whether `auto`, `implicit`, or
`off` is cheapest for a workload.
