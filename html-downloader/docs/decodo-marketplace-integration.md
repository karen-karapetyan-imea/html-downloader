# Decodo marketplace download provider

Marketplace HTML downloads can use either the existing `curl_cffi` fetcher or Decodo's synchronous Web Data API. Auction-house downloads always use `curl_cffi`. Discovery, URL lists, SHA1 filenames, and the `html/` plus `results.jsonl` layout are unchanged.

Official references used by this integration:

- [POST /v1/scrape](https://help.decodo.com/docs/web-data-api-scrape-real-time-requests)
- [Parameters](https://help.decodo.com/docs/web-data-api-scrape-parameters)
- [JavaScript rendering](https://help.decodo.com/docs/web-data-api-scrape-javascript-rendering)
- [Response codes](https://help.decodo.com/docs/web-data-api-response-codes)
- [Status codes](https://help.decodo.com/docs/web-data-api-status-codes)
- [Billing](https://help.decodo.com/docs/web-data-api-billing)

Decodo's usage policy says `/scrape` is for publicly accessible pages. It does not bypass a site's terms or an explicit access restriction, and a target CAPTCHA or 403 is still a failed fetch.

## Provider selection

`DOWNLOAD_PROVIDER` accepts `curl_cffi` (default) or `decodo`. `--provider` on the marketplace `download` command overrides the environment variable. There is no per-marketplace provider field.

`curl_cffi` keeps the current defaults: Chrome impersonation, proxy rotation, 16 workers (cap 64), 8 requests/second, 0.5–2s jitter, 3 retries, and a 20s timeout. `--proxy-file` is still required.

`decodo` does not read the proxy file. Requests go to Decodo, which documents its own proxy pools.

Weekly marketplace scripts are unchanged. They keep using `curl_cffi` unless `DOWNLOAD_PROVIDER=decodo` is set in the environment that launches them.

## Request that is sent

```http
POST https://data.decodo.com/v1/scrape
Accept: application/json
Content-Type: application/json
Authorization: Bearer <DECODO_API_KEY>
```

```json
{"url": "https://example.com/artwork", "proxy_pool": "premium"}
```

`headless` is omitted unless `DECODO_JS_RENDERING=html`. `markdown` and `parse` are never set, so the default raw page text is requested rather than Markdown or a parsed record. `png` is rejected because a screenshot is not HTML.

The synchronous call holds the connection until Decodo returns. Their documented open-connection limit is 150 seconds, and `DECODO_TIMEOUT_SECONDS` is capped there. Asynchronous `POST /v1/task` polling is not used: this crawler already fetches one URL per worker, and polling would add another request per page.

One shared `httpx` client is used for the job. `DECODO_MAX_IN_FLIGHT` (default 4) caps concurrent calls. Effective workers are `min(--workers or 16, DECODO_MAX_IN_FLIGHT, 64)`.

## Raw HTML

A successful API body looks like:

```json
{"results": [{"content": "<html>...</html>", "headers": {}, "status_code": 200, "task_id": "..."}]}
```

`content` is a JSON string. The downloader UTF-8-encodes that string and writes those bytes. It does not parse or reserialize HTML, and it does not save the JSON wrapper. `window.__data` and script tags are kept when they are inside `content`.

This is the HTML Decodo returned, not a byte-for-byte copy of the origin response. Characters leave the wire as Unicode inside JSON, so a non-UTF-8 origin page is stored as UTF-8. The target `Content-Type` charset is not applied again.

A fetch is successful only when all of the following hold:

- The API HTTP status is 200 and `results[0].content` is a string.
- The target `status_code` is 200.
- The body is non-empty and contains `<`.
- The existing block detector does not flag it (target 403/429/503, `cf-mitigated: challenge`, or body keywords such as `captcha`).

The existing `results.jsonl` fields stay in place. Decodo rows add optional fields: `provider`, `api_status_code`, `error_category`, `retry_count`, `html_bytes`, `validated`. `status_code` remains the target site status. API failures that never reach a target status use `status_code` 0 and put the API status in `api_status_code`.

ETL code reads `html/*.html` plus `filename`, `url`, `timestamp`, and `status_code` from `results.jsonl`. It ignores unknown keys. Do not point ETL at a row with `validated` false; that row has no HTML file.

Marketplace `manifest.json` keeps the original fields and adds `provider` and `metrics`. Auction manifests are not given those keys.

## Configuration

See `.env.example`. The API key belongs in the environment, not in shell scripts or committed files.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DECODO_API_KEY` | required | Bearer token from the Decodo dashboard. |
| `DECODO_TIMEOUT_SECONDS` | 60, cap 150 | Total synchronous request timeout. |
| `DECODO_CONNECT_TIMEOUT_SECONDS` | 10 | TCP/TLS connect timeout. |
| `DECODO_MAX_IN_FLIGHT` | 4 | Concurrent Decodo calls. |
| `DECODO_REQUESTS_PER_SECOND` | 1 | Job rate limit. No human jitter. `--rps` overrides it. The curl default of 8 is not applied. |
| `DECODO_MAX_RETRIES` | 1 | Extra attempts after the first, only for transient API failures. |
| `DECODO_JS_RENDERING` | unset | Set to `html` to send `"headless": "html"`. |
| `DECODO_PROXY_POOL` | `premium` | `premium` or `standard`. Omitting it on the wire would still select premium. |

Decodo does not publish one requests-per-second number for every plan. Batch submission is documented at 1 request per second and is not used here. Keep the defaults until a small benchmark shows the plan can go faster. `429` means the subscription rate limit was exceeded, or that requests to a domain are limited. Their `429` guidance also says to check that requests remain, so it can mean the allowance is used up.

## Retries, limits, and stopping

The Decodo client does not retry. The crawler retries only when the outcome says the failure is transient. Physical `/scrape` calls per URL are at most `1 + DECODO_MAX_RETRIES`.

| Outcome | Attempts | Job |
| --- | --- | --- |
| Valid HTML | 1 | continues |
| Target 403/429/503, CAPTCHA, challenge, other target non-200 | 1 | continues |
| Timeout, connection error, 204, 500, 524, 612, 613 | up to the retry cap, exponential backoff plus jitter | continues |
| `429` with `Retry-After` | same cap; the header value is capped by `retry_max_delay` (30s) | after 3 `429` responses the job stops |
| Body text that looks like quota or an exhausted balance | 1 | stops (`decodo_quota`) |
| 401, API 403 | 1 | stops |
| 400, 422 | 1 | stops (`decodo_bad_request`) |
| API 404 | 1 | that URL only. The two code pages disagree (missing target vs missing task); sync `/scrape` is treated as this URL failing |
| 999 | 1 | that URL only. JavaScript is not toggled automatically |

`Retry-After` is honored when present. Decodo's documented response headers are `x-task-id` and `x-traffic-generated`, not `Retry-After`.

In-flight calls finish after a stop. URLs that have not been sent are recorded as `error=not_sent` and the manifest status is `failed`. The API key is not written to logs, exceptions, JSONL, or the manifest.

Target blocks are not retried. Repeating them spends credits and does not rotate a local proxy. A block is `error_category=target_block` with `api_status_code` 200 when Decodo returned the page. An API failure uses an `api_*`, `timeout`, or `network` category and `status_code` 0.

`estimated_billable_requests` counts responses whose HTTP status the billing page marks as charged: 2xx, and 4xx when the body is non-empty. It is an estimate, not a dollar amount. 500, 524, and 613 are documented as not charged. 612 and 999 are excluded because they are not on that table. `traffic_bytes` sums `x-traffic-generated` when Decodo sends it. That header is bytes, not price.

A target block returned with API HTTP 200 still counts as billable. Decodo charged for returning the page.

## Cost

`/scrape` is priced per 1,000 requests. The published rate depends on plan size, `proxy_pool`, and whether `headless` is set. You pay for successful Decodo responses. Failures `500`, `524`, and `613` are not charged. A non-empty `429` or other `4xx` can still be charged.

Premium, no JavaScript, from the billing table:

| Plan | USD per 1,000 | About USD per 1,000,000 pages |
| --- | --- | --- |
| $99 | 0.70 | 700 |
| $499 | 0.60 | 600 |

Standard, no JavaScript, is lower ($0.30 / 1,000 on the $99 plan, $0.18 / 1,000 on the $499 plan) and is documented for simpler pages. Premium + JavaScript is higher ($1.10 and $0.95 per 1,000 on those two plans).

A Decodo subscription in the $100–$500 range does not cover a 1–10 million page month at the premium rates above. One million premium pages is already more than a $499 plan's headline price, before retries and before billable 4xx responses. Run a bounded benchmark and read the dashboard Statistics page before raising concurrency.

## Benchmark

1. Put 20–50 known product URLs in a job `urls.txt`.
2. Leave JavaScript off and set `DECODO_MAX_IN_FLIGHT=1`, `DECODO_MAX_RETRIES=0`, `DECODO_REQUESTS_PER_SECOND=1`.
3. Run:

```bash
DOWNLOAD_PROVIDER=decodo \
DECODO_API_KEY=your_api_key \
DECODO_MAX_IN_FLIGHT=1 \
DECODO_MAX_RETRIES=0 \
DECODO_REQUESTS_PER_SECOND=1 \
python -m html_downloader download \
    --marketplace saatchi \
    --date 2026-10-22 \
    --urls /path/to/sample-urls.txt \
    --skip-existing
```

4. Open two HTML files and confirm they contain the page markup you expect, including `window.__data` where that marketplace embeds it. Confirm `results.jsonl` has `validated: true` only for those files.
5. Read `manifest.json` `metrics.estimated_billable_requests` and compare it with the Decodo dashboard.
6. If blocks dominate, do not raise retries. Decide separately whether `DECODO_PROXY_POOL=premium` (the default) or `DECODO_JS_RENDERING=html` is worth the higher rate on a second sample of the same size.

## Rollback

Unset `DOWNLOAD_PROVIDER` or pass `--provider curl_cffi`, and pass `--proxy-file`. No code change is required. Already written HTML files stay valid for ETL.

## Diagnosis

| What you see | What it means |
| --- | --- |
| CLI exit 1, `DECODO_API_KEY is required` | The variable is missing or blank. The process stops before any request. |
| `stop_reason=decodo_unauthorized` or `decodo_forbidden` | The key was rejected or the account cannot call this API. Fix the key. Further URLs are not sent. |
| `stop_reason=decodo_bad_request` | The request body was rejected (HTTP 400 or 422). This is a configuration bug, not a bad URL. |
| `stop_reason=decodo_quota` | The 429 body matched a quota/balance phrase. The matcher is best-effort because Decodo has no separate quota status. |
| `stop_reason=decodo_rate_limit` | Three HTTP 429 responses occurred. Wait, lower `DECODO_REQUESTS_PER_SECOND` and `DECODO_MAX_IN_FLIGHT`, and check the remaining allowance. Non-empty 429 responses can be billed, so the job stops instead of retrying the whole list. |
| `error_category=target_block` | Decodo reached the site and the existing block detector rejected the page. The API did not fail. |
| `error_category=api_transient` | Decodo returned 500, 524, 612, 613, or 204. 500/524/613 should not be billed. The URL was retried only up to the cap. |
| `error_category=invalid_body` | HTTP 200 but `content` was missing, not a string, empty, or not HTML. The file was not written. |
| `error=not_sent` | The job had already stopped. No additional API call was made for that URL. |

## Hybrid mode

`DOWNLOAD_PROVIDER=hybrid` is not implemented. Falling through to a second provider after a block would send another billable request, and a target 403 is not a reason to switch fetchers. A later `PageFetcher` can wrap `CurlCffiProvider` and `DecodoProvider`, record both attempts, and stop on authentication, quota, and rate-limit circuits. It should not retry a permanent failure or every blocked URL through the other provider.
