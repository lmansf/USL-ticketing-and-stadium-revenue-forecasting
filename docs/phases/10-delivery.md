# Phase 10 - Delivery

**Goal.** A repo, a video, and an email that together make the case without any one of
them depending on the other two surviving the trip.

---

## The framing - why they should care

The dashboard is not the pitch. The pitch is that USL has a 2028 problem and no data
for it.

Twenty clubs, promotion and relegation, revenue history that will span two divisions.
Every demand forecast they build will lean on European assumptions that may not
transfer to American markets. And the baseline cannot be created retroactively - it has
to exist *before* pro-rel does, or there is nothing to measure the effect against.

So the reason to care is that someone already started instrumenting it, on their data,
and can say precisely what is measurable today versus what has to wait for 2028.

Lead with the 2028 gap, not with the tooling. The stack is the answer to "how", and
nobody asks "how" before they care about "why".

---

## The story the dashboard tells

Three beats, a narrowing funnel:

1. **View one:** I can predict attendance, and here is the proof against actuals.
   *Credibility first.*
2. **View two:** and here is *why* it moves - table position. *The mechanism, which is
   what is about to matter enormously to them.*
3. **View three:** so here is what your next home match will draw. *Application.*

Trust, then mechanism, then application. The tracker strip - feature importance and MAE
over time - stays below the fold. It is the evidence, not the pitch.

---

## The video

Four to six minutes.

- Dashboard first, 30 seconds. What it shows, what it predicts.
- Then the pipeline. Run history, the weekly schedule, where each table comes from.
- Then one break-and-fix. D3 is the strongest of the four because the failure it
  catches is invisible in most pipelines.
- Say out loud, once, that the pro-rel features are not yet validated and why. That
  sentence is the credibility moment - it is the thing that separates someone
  presenting a model from someone who understands what their model can and cannot
  support.

Record during the Tableau Desktop trial, while the live connection works. That is the
one part of this you cannot redo on day 15.

---

## The email

Short. A video link and a GitHub link. Frame it as thinking about their problem, not as
a portfolio submission - "I built something about the 2028 forecasting gap" lands
differently from "please review my project".

The email may get forwarded without the video link surviving. Assume it will.

---

## The README

Carries the whole story on its own, because the email may get forwarded and the video
link may not survive the trip.

- One-paragraph thesis - the pro-rel forecasting problem
- Three or four embedded screenshots
- The video link, near the top
- Architecture diagram
- What is validated versus what is instrumented-but-unproven
- Setup instructions that actually run

That last one matters more than it sounds. A repo someone can clone and run is worth
more than a prettier one they cannot.

---

## Exercise 10.1 - The setup instructions actually run

Prove the last bullet rather than assuming it. The failure you are looking for is the
one where the repo works because of something on your machine that is not in the repo.

<details>
<summary>Solution</summary>

Clone into a fresh directory, make a new virtual environment, and follow your own
README literally - no shortcuts, no "I know what it means here". Better still, do it on
a machine that has never run this project.

The things that reliably break:

- A dependency you installed months ago for something else and never added to
  `requirements.txt`.
- A path that only exists on your machine, usually in `config.py` or a scheduler entry.
- The DuckDB file being gitignored and therefore absent - which is correct, but it means
  the first command in your instructions has to be the backfill, and the backfill has to
  work against the live site for someone who has none of your cache.
- Python version drift. State the minimum and check it in `pyproject.toml`.

The stronger version is a `make check` that runs lint, typecheck, and tests, and a note
in the README saying a fresh clone should pass it. Someone evaluating the repo will run
exactly one command before deciding whether to keep reading. Make sure that command
works.
</details>

---

## Checklist before sending

- [ ] Fresh clone runs the setup instructions to a working backfill
- [ ] `make check` passes on that fresh clone
- [ ] Screenshots in the README are current, not from an earlier version
- [ ] The validated-versus-instrumented section is in the README, not only in the video
- [ ] Every pro-rel view carries its exploratory label
- [ ] Extracts are committed or regenerable, so the workbook survives the trial expiry
- [ ] The demo scripts leave `git status` clean
- [ ] No emoji in the repo, the commits, or the email

---

Phase two, deferred and documented:
[Dagster](11-phase-two-dagster.md) and [weather](12-phase-two-weather.md).
