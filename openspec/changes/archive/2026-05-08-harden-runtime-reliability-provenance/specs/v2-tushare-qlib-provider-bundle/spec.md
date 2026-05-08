## ADDED Requirements

### Requirement: Tushare VWAP conversion SHALL apply adjustment factors exactly once

Generated qlib `vwap` values SHALL be expressed on the same adjustment basis as
OHLC fields. When raw traded value/volume is unavailable and close is used as a
fallback, the fallback SHALL start from raw close and apply the selected
adjustment scale exactly once.

#### Scenario: zero-volume row uses close fallback
- **WHEN** a Tushare daily row has zero volume and adjusted output is requested
- **THEN** generated `vwap` equals raw close multiplied by the selected
  adjustment scale once
- **AND** it does not equal already-adjusted close multiplied by the scale again

### Requirement: Tushare client SHALL reuse its pro_api handle

The Tushare client wrapper SHALL avoid reconstructing the underlying `pro_api`
handle for every API call on the same client instance.

#### Scenario: multiple API calls use one client
- **WHEN** two Tushare API calls are made through the same `TushareClient`
- **THEN** the wrapper constructs the underlying `pro_api` client at most once
- **AND** both calls still use the same token boundary and typed error handling
