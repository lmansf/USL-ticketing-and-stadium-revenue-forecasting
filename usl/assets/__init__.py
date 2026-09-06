"""Dagster assets: a thin orchestration wrapper over the phase-one package.

Every asset here calls a function that python -m usl.run also calls. The
assets own no business logic - if one ever does, the migration went wrong
(docs/phases/11-phase-two-dagster.md). What they add is what the scheduled
task could not give: a browsable run history, an asset lineage graph, and
every numeric metadata field plotted over time.

The run log is written too. Each asset records itself as a stage of the
Dagster run, under the Dagster run id, so the Tableau tracker strip keeps
working whichever scheduler is in charge; each asset check writes its row to
check_log the same way.
"""
