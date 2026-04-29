# v2-trading-calendar Specification

## Purpose
Define the trading-calendar abstraction used by artifact coverage and runtime
date-window helpers.

## Requirements

### Requirement: V2 SHALL provide a TradingCalendar abstraction in `src/data/`

The system SHALL expose a `TradingCalendar` Protocol in
`src/data/trading_calendar.py` declaring a single method
`count_trading_days(start: date, end: date) -> int`. The method SHALL
treat the interval as inclusive on both ends and SHALL return `0` when
`end < start`. The Protocol SHALL NOT impose any other interface
constraints on implementations.

#### Scenario: Protocol shape is stable
- **WHEN** a maintainer inspects `src/data/trading_calendar.py`
- **THEN** `TradingCalendar` is defined as a `typing.Protocol`
- **AND** it declares exactly one method, `count_trading_days(start, end)`
- **AND** the docstring states inclusive interval semantics

### Requirement: StaticTradingCalendar SHALL provide an in-memory deterministic implementation

`StaticTradingCalendar` SHALL accept any iterable of `datetime.date`
values, deduplicate and sort them at construction time, and store them
as an immutable tuple. It SHALL implement `count_trading_days` using
a binary-search bisection on the sorted dates. It SHALL raise
`TradingCalendarError` if any constructor input or query argument is
not a `datetime.date` instance.

#### Scenario: empty calendar returns zero
- **WHEN** `StaticTradingCalendar([])` is constructed
- **AND** `count_trading_days(date(2026,1,1), date(2026,12,31))` is called
- **THEN** the result is `0`

#### Scenario: inclusive endpoints are counted
- **WHEN** the calendar contains exactly `[2026-02-02, 2026-02-03, 2026-02-04]`
- **AND** `count_trading_days(date(2026,2,2), date(2026,2,4))` is called
- **THEN** the result is `3`

#### Scenario: end before start returns zero, not error
- **WHEN** `count_trading_days(date(2026,3,1), date(2026,2,1))` is called
- **THEN** the result is `0`
- **AND** no exception is raised

#### Scenario: non-date input is rejected
- **WHEN** the constructor receives a non-`date` value (e.g., a string)
- **THEN** `TradingCalendarError` is raised

### Requirement: QlibTradingCalendar SHALL adapt qlib.data.D.calendar lazily

`QlibTradingCalendar` SHALL lazily import `qlib.data.D` only on the
first call to `count_trading_days`, fetch the full calendar via
`D.calendar(freq=...)`, convert each timestamp to a `datetime.date`,
and cache the result inside an internal `StaticTradingCalendar`. All
subsequent calls SHALL reuse the cached calendar without invoking qlib
again. Failure to import qlib or to fetch the calendar SHALL raise
`TradingCalendarError` whose message instructs the operator to call
`src.core.qlib_runtime.init_qlib_canonical` first.

#### Scenario: import-time has no qlib dependency
- **WHEN** `src/data/trading_calendar.py` is imported in an environment
  where `qlib` is not installed
- **THEN** the import succeeds
- **AND** `StaticTradingCalendar` and `TradingCalendarError` are usable

#### Scenario: missing qlib at call time produces actionable error
- **WHEN** `QlibTradingCalendar().count_trading_days(...)` is called and
  `qlib.data` cannot be imported
- **THEN** `TradingCalendarError` is raised
- **AND** the error message references `init_qlib_canonical`
