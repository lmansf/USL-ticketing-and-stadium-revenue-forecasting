# Phase 08 - Tableau

**Goal.** Three views plus a tracker strip, built against DuckDB during the Desktop
trial, with a file-based export path so the free edition carries the artifact
afterwards.

**MVP cut.** Skip the trial entirely, go straight to CSV extracts in Tableau Public.
See [docs/mvp/04-mvp-tableau.md](../mvp/04-mvp-tableau.md).

**Files.** `usl/export/extracts.py`, `tableau/`,
[reference/tableau-duckdb-connector.md](../reference/tableau-duckdb-connector.md)

---

## The licensing reality

- **Tableau Public** - free, but connects to *files only*. Not DuckDB, not Postgres.
- **Tableau Desktop** - 14-day free trial, no card required. Connects to DuckDB via the
  community connector. After 14 days the software locks; your data is untouched, but
  you cannot open the workbook.
- **Creator** - 75 USD per month billed annually, 900 up front. Not worth it for a
  portfolio piece.

**The plan:** build everything against DuckDB during the trial, record the video then,
and keep a file-based export path so Tableau Public carries the static version
afterwards.

**Start the trial only when the pipeline is finished and data is flowing.** Spend all
14 days on the dashboard, not on the plumbing.

This is the *second* clock in the project. The FootyStats subscription
([phase 00](00-data-access-and-the-clock.md)) is the first, and it is the unforgiving
one: when it lapses you lose the data unless you archived it, whereas when this trial
lapses you lose only the live connection and your CSV extracts still open. Ideally the
subscription has already ended and everything is served from `data/raw_archive/` before
you start these 14 days.

Because the trial expires, `usl/export/extracts.py` is not optional and not a fallback
you write if there is time. It is the thing that makes this repo useful to someone who
does not have Tableau at all, and it is what your own workbook falls back to on day 15.
Write it before you start the trial, not after.

---

## Connector setup

About fifteen minutes, from the Tableau Exchange (MotherDuck's DuckDB connector).
Full walkthrough with paths and failure modes:
[reference/tableau-duckdb-connector.md](../reference/tableau-duckdb-connector.md).

The short version:

1. Download the DuckDB **JDBC driver** jar into the Tableau `Drivers` directory.
2. Download the signed **`.taco`** connector file into
   `Documents\My Tableau Repository\Connectors`.
3. Restart Tableau. DuckDB appears under connections.

This is JDBC, not ODBC and not ADBC. ADBC is the newer Arrow-native standard and DuckDB
ships a driver for it, but Tableau's path here is the JDBC connector. Knowing the
difference is worth one sentence in an interview; taking the ODBC path because a search
result suggested it will cost you an afternoon.

**Version pairing matters.** The JDBC driver has to be able to read the file format
your `duckdb` Python package wrote. A mismatch surfaces as an unhelpful connection
error rather than as a version message. Pin the Python `duckdb` version in
`requirements.txt` once you know which driver you have.

---

## Who uses this, and for what

Be ready for "walk me through what someone actually does with this on a Tuesday
morning." There are two users, and the second one is the bigger prize.

**The club - an operations and budgeting input.**

- Staffing and concessions for the next home match, from the forecast plus its
  uncertainty band.
- What a slide down the table costs in gate revenue - table position translated into
  dollars.
- Season-end gate projection that updates weekly as results come in, rather than a
  figure set in February and never revisited.

**The league office - a policy input.** This is the one that makes the project
strategic rather than operational.

Under promotion and relegation, USL has to make decisions no American league has had to
make: whether to pay **parachute payments** to relegated clubs and how much, how revenue
sharing works across two divisions, whether relegation is financially survivable for a
club at all. Every one of those questions needs a number for what relegation actually
costs in attendance and gate revenue.

That number does not exist. And it cannot be produced after the fact - you need the
pre-pro-rel baseline, which means the measurement has to be running *before* 2028.

The dead-rubber decay curve is the first version of that number. Not "here is a
dashboard for clubs" but "here is the beginning of the evidence base for how you price
relegation." Say it in those terms and the project stops being a portfolio piece and
starts being a proposal.

---

## The three views

**1. League overview.** Actual versus predicted attendance by club, current season. The
model, visible immediately. Credibility first, before any argument.

**2. Pro-rel view.** Table position against attendance, with the relationship fitted.
This is the differentiated one. Label it as exploratory on the view itself, not only in
a caption: no relegation has occurred, so this describes correlation with league
position, not a measured relegation effect.

Pair it with the **dead-rubber decay curve** from [phase 06](06-features.md) -
attendance on eliminated-club home matches, plotted by `matches_since_elimination`,
across all nine seasons. Unlike the fitted relationship above it, that curve is
measured, not projected. It gives this view something real to stand on rather than only
a caveat, and it is the counterfactual baseline the 2028 relegation effect will be
measured against.

**3. Club drill-down.** One club, its season to date, and forecasts for remaining home
matches with an uncertainty band.

**Supporting strip - the tracker.** Feature importance as a bar chart, pro-rel features
in a contrasting colour, plus MAE over time by model. Keep this below the fold. The
model and its stability are the headline; the two-model comparison is the evidence, not
the pitch.

---

## Exercise 8.1 - The uncertainty band

View 3 shows forecasts with an uncertainty band. `predictions` has one `predicted`
value per match per model per run. Where does the band come from?

<details>
<summary>Solution</summary>

Three options, in increasing order of effort and honesty.

**Historical residuals.** Band = predicted plus or minus the model's MAE on the
holdout, or plus or minus a residual percentile. Cheapest, requires no model change,
and is defensible as "typical error". Its weakness is that the band is the same width
everywhere, which is not true - a well-established club's gate is far more predictable
than an expansion club's.

**Quantile regression.** Train two extra XGBoost models with
`objective="reg:quantileerror"` at, say, 0.1 and 0.9. You get a genuine per-match
interval that widens where the model is uncertain. Costs two more models per run and two
more rows in the prediction table, which is why `predictions` would need a `quantile`
column - a schema change worth deciding on before you have weeks of history in the
table.

**Residual percentiles by club.** The middle path. Compute residual spread per club
from history and apply it per club. Most of the benefit of quantile regression, no new
models, no schema change.

For a portfolio piece, historical residuals with an honest label is enough, and the
middle path is a good answer to "how would you improve this". What matters more than
which you pick is that the band is labelled with what it means - an 80% interval and a
plus-or-minus-one-MAE band look identical on a chart and mean different things.
</details>

---

## Fallback export

Even during the trial, keep an `export_extracts` step that writes CSVs, or Hyper files
via `pantab`, to `tableau/extracts/`. It is your Tableau Public path after the trial,
and it is the thing that makes the repo useful to someone who does not have Tableau
at all.

Export the tables Tableau actually needs rather than everything - the mart, predictions,
model metrics, feature importance, the standings, and the decay curve. Extracts are
gitignored; the code that writes them is not.

---

## What "done" looks like

- Three views plus the tracker strip, built and readable.
- Every view that shows a pro-rel feature carries its exploratory label on the view.
- `python -m usl.run export` writes extracts that reproduce all three views in Tableau
  Public without a live connection.
- The workbook opens against extracts after the trial expires.

Next: [phase 09 - The demo](09-break-and-fix.md).
