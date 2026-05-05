## ADDED Requirements

### Requirement: Shipped runtime-adjacent integrations SHALL declare installable dependency metadata

Runtime-adjacent external integrations SHALL declare the dependency needed to
run that integration in project metadata either as a mandatory dependency or as
a named optional extra.

#### Scenario: Tushare integration dependency is discoverable
- **WHEN** an operator wants to run the shipped Tushare ingest or preflight
  scripts
- **THEN** project metadata exposes a `tushare` optional extra
- **AND** runtime install hints mention that project extra
- **AND** core installs are not forced to import Tushare merely by importing
  contract or core modules
