# Delta for v2-today-workbench

## ADDED Requirements

### Requirement: 今日工作台 SHALL 显示最近若干次更新运行的形态

The today workbench SHALL render a compact strip of the most recent update runs
read from the run ledger, showing each run's date and outcome in order, so a
run of consecutive failures is visible without reading logs. It SHALL read the
ledger only — deriving nothing of its own — and SHALL state plainly when the
ledger is missing or unreadable rather than rendering an empty strip that looks
like "no problems".

Three consecutive nightly failures went unnoticed until the third. The queue
escalates severity from the serving side's staleness verdict, which infers
pressure from the bundle's date — and the bundle's date only advances on
success, so it is indirect evidence of a failure run. The pattern itself was
never shown anywhere.

An empty strip and a missing ledger look identical to an operator unless the
page says which one it is; that is the same "blank reads as nothing to say"
failure this page already fixed once for the failure reason.

#### Scenario: consecutive failures are visible at a glance
- **WHEN** the ledger's recent entries contain several failures in a row
- **THEN** the strip shows them in order so the run of failures is apparent
  without opening any log

#### Scenario: a missing ledger is stated, not implied
- **WHEN** the ledger file does not exist or cannot be parsed
- **THEN** the page says so, rather than rendering an empty strip

#### Scenario: a malformed line does not hide the rest
- **WHEN** one ledger line is malformed
- **THEN** the remaining entries still render and the malformed count is
  disclosed

#### Scenario: the strip reproduces, never re-derives
- **WHEN** the strip renders a run's outcome
- **THEN** it presents what the ledger recorded, computing no verdict of its own
