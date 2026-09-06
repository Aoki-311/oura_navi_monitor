# News / Society usage release notes

This is the deployment checklist for the integrated dashboard upgrade, not proof
of deployment. The matching producer contract is
`lcs_mrchatbot-main/backend/docs/NEWS_USAGE_CONTRACT.md`; the OurA development
worktree mirrors that event contract. The configured production Chat source in
this repository is `lcs-rag-app`. Verify the actual source and resource inventory
before release. No local credential belongs in source, build input or runtime.

## Dashboard and counting contract

- News/Society usage appears below product needs in the existing summary, with
  three additional personal KPIs in user analysis. There is no standalone News
  analysis page and no article popularity ranking in this release.
- A visit is `tab_view`. A content click is one `detail_view` or one
  `outbound_click` whose `link_kind` is `primary`. Detail then original counts
  twice; repeating either action counts again. Only an identical event ID is
  removed as a technical retry. Evidence, registration and filter changes do not
  add to the content-click total. The pie uses content clicks, not tab visits.
- News ranking uses the eight existing BigQuery `event_type` categories carried
  in `content_event_type`, with domestic/overseas hover counts. Society ranking
  uses the existing five categories and individual society hover counts.
  Missing metadata remains unclassified; selected filters never invent article
  metadata. `scripts/sync_news_usage_catalog.py --producer-root <LCS repo>`
  generates the label projection from producer authority; `--check` verifies it.
- Summary uses the GLOBAL population; personal detail uses the USER_MAP
  population. Inactive employees are excluded by the published roster contract.
- Main and module date controls use inclusive JST calendar dates, then convert
  the API end to the next day's exclusive boundary. Main, environment/mode and
  trend each retain their applied ranges. Refresh reads the applied range;
  editing a date requires the separate apply action.
- All three controls use the News page's compact button and calendar popover:
  Today / past 7 / 14 / 30 days, month navigation and a highlighted date range.
  Presets include today and contain exactly 1 / 7 / 14 / 30 JST dates. Selections
  remain drafts until Apply; Cancel, outside click and Escape discard them.
  Clear empties only the draft, which cannot be applied until a range is complete.
- The separate refresh button reads the applied range without resetting dates.
  Main-period changes and main refresh leave environment/mode and trend requests,
  charts and calendar drafts intact. Those modules refresh through their own
  controls; changing the selected region also refreshes their regional data.
- The activity tooltip describes the existing Chat definition: distinct valid
  question dates in the fourteen JST days ending on the selected end date;
  high >= 6, middle 3–5, low 1–2, dormant 0. News does not change activity bands.
- Empty measured periods can show zero. Disabled, pre-measurement and failed
  reads cannot claim zero. Normal pages omit technical coverage/publication
  annotations; diagnostic fields remain in the internal API.

## Existing resources, additive changes

- Keep the existing Monitor dataset/location, Logging sink/writer, refresh Job,
  Scheduler cadence and Chat `MONITOR_SOURCE_SERVICE` unchanged.
- Add `create_news_usage_tables.sql` and `create_news_usage_source.sql`, rendered
  by `app.jobs.news_usage_ingestion.render_news_usage_sql` for the verified project,
  dataset, location and exact production source service. Apply schema before enabling
  the new branch. Do not change content datasets or assume cross-location joins.
- When News and Chat both use `lcs-rag-app`, the existing stdout
  `monitor_event=true` sink branch already admits News: do not add a duplicate
  filter branch. Only if the verified News source is a different service, extend
  the existing sink with that service's stdout
  `monitor_event=true AND event_family="news_usage"`. Preserve the actual current
  Chat filter, destination and writer identity. Existing bootstrap accepts
  `--news-usage-source-service` for future full preparations and dry-run inspection;
  do not rerun its broad prepare phase (TTL/IAM included) merely to add this filter.
- Verify the sink extension remains in subsequent infrastructure preparation;
  omitting that optional flag from a full sink rewrite would stop future usage
  delivery. Capture/read back the actual sink filter before and after any update.
- Set `MONITOR_NEWS_USAGE_SOURCE_SERVICE` and timezone-aware
  `MONITOR_NEWS_USAGE_START_AT` consistently on the Monitor reader and the existing
  refresh Job after schema/source readiness. Both absent means not enabled;
  incomplete configuration affects only News analysis, not app startup or Chat.
- The recurring Job executes Chat publication then the independent News branch.
  News maintains its own cursor, so a retry does not skip events if Chat advanced.
  Its referenced roster snapshot remains protected from normal old-roster cleanup.
- A first-time News-only user can match an existing roster without creating Chat
  data: the source carries an optional server-verified email digest. No raw email
  is copied into usage logs. Admission fixes the stable employee ID; subsequent
  reporting uses that ID and the current roster snapshot, not a repeated email
  match. The digest never enters the published view, report API or CSV.

## Order and checks

1. Review exact code and tests. Commit/push only authorized scoped files.
2. Confirm actual target project/region, dataset location, runtime/build identities,
   current sink filter, current image and current traffic. Do not use ambient ADC.
3. Apply additive usage schema and source, verify the sink's exact filter, deploy
   the Monitor reader/refresh image and configure the two usage settings.
4. Verify the News branch can publish an empty bounded window without changing
   Chat metrics. Before-start, no usage, stale and unavailable are distinct states.
5. Deploy the matching LCS frontend/backend release to the verified production
   service. Keep the OurA development mirror consistent. The new POST is independent
   of every existing News/Society GET response. No IAP/IAM or collector changes.
6. On an authorized logged-in user device, exercise all seven events and retain
   only their IDs/counts for reconciliation, not private payloads or credentials.
   Include a roster member without prior Chat history; confirm that fallback
   email headers cannot establish a News identity and missing optional digests
   do not invalidate previously bound subjects.
7. Run the existing refresh owner and read back usage facts, independent pointer,
   bound roster and actual Monitor report. Test/prod source selection must match.
8. Verify a later scheduled refresh separately. Record Git, Build, deployment,
   authenticated UI, data/report and Scheduler evidence as distinct results.

## Local acceptance and performance

Run the complete Monitor Python suite and browser suite against the candidate
frontend. New dashboard regressions cover category hover totals, applied dates,
independent requests, refresh errors retaining prior charts, delayed response
ordering, user selection feedback and mobile overflow. Producer acceptance must
include both receiver tests and real browser actions, including cached details
and native original links.

Independent module endpoints read only their selected event range and roster;
they do not read activity-distribution history. Concurrent identical reads share
one in-flight request. Only immutable roster snapshots are retained, keyed by
their publication identity and query parameters. Mutable publication pointers
and fact views are read again on the next refresh. Static assets use private
HTTP revalidation (ETag/304); documents and APIs retain no-store behavior.
These are implemented reductions in work, not measured production latency
claims. Record comparable first-load and refresh timings after real deployment.

Local validation includes syntax/column projection checks, not execution by
BigQuery. Execute synthetic TEMP fixtures and a bounded representative reporting
window before calling the SQL/data/performance release accepted. The roster uses
NFKC plus case folding; the matching SQL uses GoogleSQL's documented
[NORMALIZE_AND_CASEFOLD](https://docs.cloud.google.com/bigquery/docs/reference/standard-sql/string_functions#normalize_and_casefold).

`scripts/validate_news_usage_temp_sql.py --render-only` prepares the anonymous
fixture script without cloud access. `--execute --credential-file <approved
regular file>` submits that script with explicit SDK credentials and a 100 MiB
billing ceiling. It derives schemas and source/publisher statements from the
canonical SQL, replacing only exact table owners with session TEMP tables;
unexpected persistent references are rejected before submission. Assertions
cover repeated actions, event-ID replay, primary versus secondary links,
classification/geography, JST dates and an unchanged Chat publication pointer.
Keep its actual BigQuery job receipt separate from local rendering checks.

## Failure and rollback

- Stop/roll back the new producer release if it disrupts UI; legacy GET contracts
  remain identical. A failed usage POST must not block reading or downloading.
- News SQL failure must not undo already committed Chat. Its published pointer
  stays on the last successful result; do not advance it manually or show zero.
- Roll back the Monitor image/config only for the affected release. Retain the
  additive usage tables for diagnosis; do not drop tables or erase collected data.
- Do not widen user roles, invent historical events, create a second Scheduler,
  or disable authentication to make an acceptance count pass.
