# Tableau DuckDB connector setup

About fifteen minutes. Needed only for the live-connection path in
[phase 08](../phases/08-tableau.md). The
[MVP track](../mvp/04-mvp-tableau.md) uses CSV extracts and needs none of this.

---

## What you need, and what you do not

**JDBC, not ODBC and not ADBC.** Tableau's supported path for DuckDB is the community
connector, which is JDBC-based. ADBC is the newer Arrow-native standard and DuckDB
ships a driver for it, but that is not the route here. Going down the ODBC path because
a search result suggested it will cost you an afternoon.

**Tableau Desktop, not Tableau Public.** Public connects to files only. The connector
will not appear, and there is no workaround - it is a licensing boundary, not a
technical one. Desktop is a 14-day trial with no card required.

**Start the trial last.** It is the *second* clock in this project - the
FootyStats subscription is the first and the expensive one, and it should have
lapsed before this one starts. See
[phase 00](../phases/00-data-access-and-the-clock.md).
Finish the pipeline,
let it run for two weeks so you have real history, and only then install this.

---

## Steps

1. **Get the connector from the Tableau Exchange.** Search for the DuckDB connector
   published by MotherDuck. You need two files: the signed `.taco` connector, and the
   DuckDB JDBC driver jar it depends on. The Exchange listing links both.

2. **Install the JDBC driver.** Put the jar in Tableau's `Drivers` directory:
   - Windows: `C:\Program Files\Tableau\Drivers`
   - macOS: `~/Library/Tableau/Drivers`

   Create the directory if it does not exist.

3. **Install the `.taco`.** Put the connector file in your Tableau repository's
   `Connectors` directory:
   - Windows: `Documents\My Tableau Repository\Connectors`
   - macOS: `~/Documents/My Tableau Repository/Connectors`

   Again, create it if it is missing.

4. **Restart Tableau.** DuckDB appears in the connection list under **To a Server**,
   possibly under **More**.

5. **Connect.** Point it at the absolute path to `data/usl.duckdb`.

---

## Failure modes

**The connector does not appear after restart.** Almost always the `.taco` is in the
wrong directory - the `Connectors` folder inside *My Tableau Repository*, not inside
the Tableau program directory where the drivers go. The two paths are easy to swap.

**The connector appears but connecting errors out.** Usually the JDBC driver is missing
or is a version that cannot read the database file your Python `duckdb` package wrote.
DuckDB's storage format has changed across major versions, and the error message for a
version mismatch is not a version message - it is a generic connection failure. Check
the driver version against your `duckdb` version, and pin the Python one in
`requirements.txt` once you know what pairs with what.

**Connecting works but the file is locked.** DuckDB is single-writer, and Tableau holds
the file open while the workbook is open. This is the same problem as
[phase 02](../phases/02-duckdb-and-the-lock-problem.md), seen from the other end. It is
also demo scenario D1.

**Tableau blocks the connector as unsigned.** Use the signed `.taco` from the Exchange
rather than one built from source. If you must use an unsigned one, Tableau has a
command-line flag to disable connector signature verification - but a signed connector
from the Exchange is the path of less resistance and less explaining.

---

## Live versus extract

Once connected you can use a live connection or a Tableau extract.

**Live** re-queries DuckDB as you interact. Good while building, and it means the
dashboard reflects Tuesday's run with no action. It also means Tableau holds the file
open, which is what breaks Tuesday's run.

**Extract** snapshots the data into Tableau's own format. Releases the file, and is
what you want for anything you leave open.

There is a real tension here and it does not have a clean answer at this scale. Live
during development, extract for anything you walk away from, and solve the lock properly
so it does not matter - which is the phase 02 exercise.

---

## After the trial expires

The software locks. Your data is untouched, and the `.twb` file is intact, but you
cannot open it.

This is why `usl/export/extracts.py` is written *before* the trial starts rather than
after. On day 15 you rebuild the workbook against `tableau/extracts/*.csv` in Tableau
Public, and everything except the live refresh survives. Record the video during the
trial, while the live connection still works.
