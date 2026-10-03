# Prentiss County News publishing

Quality-controlled RSS publishing for https://prentissnews.com. The system is allowed
to publish **zero** articles. Word counts are editorial guardrails, not a Google
ranking target or a reason to expand thin source material.

## Editorial policy

1. Collect dated RSS sources from the last 72 hours. The official Mississippi
   Secretary of State newsroom uses a seven-day window for public-service releases.
   Skip future/undated items and
   previously published source URLs. Prefer local, substantive sources.
2. Propose combinations of up to four sources of up to 300 words each. In addition to lexical matches,
   one bounded planning call finds complementary reports or a useful roundup on one
   local topic, within 72 hours. A roundup must label separate events in descriptive
   sections and preserve each one's who/what/where/when/why. Random news bundles,
   merged crime incidents and repeated notices fail the independent editorial review.
3. Require at least 180 source words after removing repeated sentences. Images,
   video links and repeated/syndicated text do not count as additional reporting.
4. Write 250–900 words **only if supported**; limit expansion to 1.6 times source
   length. A draft that misses length or formatting gets one bounded revision using
   the same evidence. Otherwise skip. No filler, invented dates, inferred motives or promotional copy.
5. A separate review call checks every claim in the headline, excerpt and article,
   who/what/where/when/why, local relevance, attribution, useful details, duplication,
   and contradictions. Require six distinct facts and verbatim evidence found in the
   supplied source text. A mechanical coverage/quote error may be rechecked once;
   an editor's factual, relevance or duplicate rejection is never overruled.
   Reviews use strict structured output with every coverage field required and
   quotes selected from actual source passages. Missing evidence is a rejection.
   Invisible feed formatting and HTML boundary spacing are normalized before matching.
   Missing/malformed/truncated responses fail closed.
6. Compare against recent WordPress headlines and excerpts to avoid competing
   articles about the same event. Updates to existing articles require editorial work.
7. Only then resolve categories, upload an available source image, and publish.
   Stock fallback is disabled. Each contributing source receives a named link;
   an AI-assistance disclosure and corrections link are appended after word checks.
   Read the post back and verify status, approved text and all source links before
   recording success. Published posts must be readable without authentication.

These automated checks reduce risk but do not establish factual truth. Periodically
review accepted and rejected examples. Do not lower the thresholds just to fill a quota.
Sources are supplied RSS text plus full releases from the explicitly configured
Secretary of State newsroom. That adapter reads only dated releases on sos.ms.gov;
external interview links and page navigation are excluded. The system does not read text inside images,
watch videos, bypass login walls, or silently fetch arbitrary linked pages.

## Run locally

Python 3.11 or newer:

```sh
python -m venv .venv
# Activate the environment for your OS
python -m pip install -e '.[dev]'
cp .env.example .env
python -m pytest -q
python -m rss_to_wp run --config feeds.yaml --dry-run
python -m rss_to_wp run --config feeds.yaml
```

Required environment variables: `OPENAI_API_KEY`, `WORDPRESS_BASE_URL`,
`WORDPRESS_USERNAME`, `WORDPRESS_APP_PASSWORD`. Keep credentials in `.env` locally
and GitHub Actions secrets remotely. Never commit them.

`OPENAI_MODEL` and `OPENAI_REVIEW_MODEL` default to `gpt-5.4-mini`.
Writing/grouping use low reasoning and editorial review uses medium, with a
8,000-token completion cap including reasoning. The two roles use separate calls.
API logs record token usage. As of October 2, 2026, standard pricing is $0.75/M input
and $4.50/M output: https://developers.openai.com/api/docs/models/gpt-5.4-mini .
For illustration, 10,000 input plus 4,000 output tokens across writing/review cost
$0.0255. Actual cost includes reasoning, grouping, rejected candidates and retries.
Manual dry runs also run four live API evaluations: supported articles must pass,
while versions that change when service begins must fail factual review. These
include a fictional library example and an archived real-source election example
that caught an error missed by the simpler fixture. Test articles never reach
WordPress. Live evaluations use the configured review model and incur API usage;
ordinary unit tests skip them.
`WORDPRESS_POST_STATUS` defaults to `publish`; set `draft` for an editorial queue.
The production workflow uses `America/Chicago` and disables stock images.

CLI options: `--single-feed "Local Feed 1"`, `--hours 72` (1–168), `--dry-run`.
`status` lists processed entries. `clear-db --yes` clears processed records; it does
not clear editorial rejection decisions. Avoid clearing production dedupe state.

## GitHub Actions

The publisher runs at 02:23, 08:23, 14:23 and 20:23 UTC (four times daily). At most
three qualifying articles per run and two per contributing feed may publish.
At most 12 candidates use model review per run. The limits are ceilings, not targets.

Manual runs default to dry-run. Feature branches can only run in dry-run mode.
The publisher runs regression tests first, serializes all manual/scheduled runs,
and saves dedupe state after partial failures. Manual feed names are passed through
environment variables into a Bash argument array, never interpolated as shell code.

Model selection uses repository **variables** `OPENAI_MODEL` and
`OPENAI_REVIEW_MODEL`; the old `OPENAI_MODEL` secret no longer silently forces the
previous nano model. API and WordPress credentials remain Actions secrets.

The `editorial-decisions` artifact records counts, source URLs, rejection reasons,
and the full proposed article and evidence for accepted stories, so successful
writing can be inspected rather than inferred from a green job. Errors fail the job even after partial success.
Valid empty feeds and editorial rejections are successful outcomes.

Editorial response failures include fixed diagnostic codes, call stage,
finish reason, refusal flag and token counts, without raw API exception messages
or response bodies. Failed candidate groups retain public source snapshots and
policy settings for a bounded replay. The manual `Editorial diagnostic replay`
workflow reviews the four-source group from October 3 run 37110316545 using the
unchanged configured models and 8,000-token cap. It has no WordPress credentials
or publication/state writes. That historical response and draft were not saved;
the fixture uses currently retained RSS text matched by the exact source URLs.
For another saved group, use `python -m rss_to_wp.replay --snapshot snapshot.json`
with its `replay` object. This incurs model usage and never publishes.

Dry-run calls the writing/review APIs but performs no WordPress requests or content
uploads and never records published/rejected entries. Thus its output is a quality
preview, **not** a prediction of new publication: Actions dry runs start with a
fresh scratch database, while production restores state and checks existing
WordPress posts. Stories accepted earlier in the same dry-run are supplied as
duplicate-review context. Dry-run API usage is billable.

SQLite is restored from the latest Actions cache, including legacy caches on first
upgrade. Cache is not durable storage: eviction is possible. WordPress source-link
and slug checks provide a second guard; lookup failures block publishing. A lost
response to POST is retried only on a later run after duplicate lookup, never blindly.
Only run a single external scheduler if deploying outside GitHub Actions.

Unchanged editorial rejections are cached by source content, source URLs, policy
version, thresholds and models. Enriched sources or a newly formed source group
are reviewed again. Short candidates are not permanently marked as processed.

## Categories and images

Keep the negative lookahead in the county category rule. Existing site permalinks
use the lowest category ID; adding categories to old posts can change their URL.
This publisher only assigns categories to new posts. Generic county tags are no
longer stamped on every statewide story. Local relevance is decided by the editor.

Existing RSS image selection is retained; source attribution does not establish
image reuse rights. Review source permissions. No fabricated image description is
used as alt text. Stock images require explicit `USE_STOCK_IMAGES=true` and provider
keys; a visible illustrative-image notice is then added.

## Operations and site audit

- Use the report artifact to inspect rejections and API/WordPress failures.
- A source feed entry with insufficient text must be enriched through original
  reporting or better source feeds, not inflated by changing the prompt.
- Monitor Search Console's server errors, successful fetches, submitted canonical
  URLs, indexed articles, and clicks to local article pages.
- Compare AdSense U.S. page RPM, impressions per page and viewability separately
  from overall traffic. Geographic traffic alone does not prove invalid activity.
- Preserve established URLs; review old duplicates for consolidation and redirects
  individually, with source verification. Do not bulk delete short posts for SEO.
- This repository does not contain the live WordPress theme, server configuration,
  cache/CDN rules or AdSense settings. Its deployment cannot repair host outages.

Optional SMTP utilities remain available as library code, but the publishing CLI
does not email run summaries. Use Actions artifacts and job status for this workflow.
