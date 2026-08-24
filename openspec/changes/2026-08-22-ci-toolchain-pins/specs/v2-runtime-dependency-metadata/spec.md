# Delta for v2-runtime-dependency-metadata

## ADDED Requirements

### Requirement: Every dependency CI installs SHALL declare an upper version bound

Project metadata SHALL declare an upper version bound for every dependency the
CI workflows install — the build-system requirements, the base dependencies, AND
every extra they name — and a test SHALL enforce this by deriving the covered
groups from the workflows' own install commands rather than from a hand-written
list. Project metadata SHALL be read by PARSING TOML, and workflow `run` blocks by
LEXING shell, so that quoting style, comments, line continuations, here-documents
and command separators are handled by one model of the syntax rather than by
accumulated text rules; content the lexer cannot read SHALL fail loudly rather
than be skipped. A local-project
install SHALL be recognised by its TARGET (`.` or `.[extras]`), not by which
editable spelling precedes it. Dependencies that JUDGE the
code (the test runner and its plugins, the type checker, the linter) SHALL be
bounded at the NEXT minor version; libraries the code merely uses MAY be bounded
at the major version.

An unbounded judging tool turns CI red on the day it publishes, independently
of the change under review. Measured: PR #462 went red on all six legs because
`pytest>=7.4` resolved to a same-day 9.1.1 whose logging plugin behaves
differently from the 9.0.3 a developer had locally — local green proved
nothing.

A major-version bound would not have prevented it: the break came from a MINOR
bump. Nor does merely requiring a dotted ceiling — `<10.0` is still a
major-only bound wearing a minor's clothes — so the ceiling is required to be
exactly the floor's next minor.

`pip install -e ".[dev,ui]"` resolves the base dependency list as well as the
named extras, and pip's default isolated build resolves `[build-system].requires`
on top of that, so a guard that walks only the extras leaves both free to drift
while reporting success.

The covered set is not enumerated category by category — that is how the gap
kept reappearing (extras, then the base list, then the build requirements).
pyproject can carry requirements in exactly three standard-defined places: PEP
518's `[build-system].requires`, PEP 621's `[project].dependencies`, and the
groups under `[project.optional-dependencies]`. That closed set is what the
guard walks.

Scanning raw workflow text is likewise not refined rule by rule. A comment, a
step `name:` carrying an example command, and a single-quoted argument each
broke a text-level pattern in turn; the guard therefore parses `run` blocks out
of the YAML and shell-tokenises them, so quoting style and non-executable
metadata stop being special cases.

Tokenising is not the same as lexing, and that difference cost two further
rounds. `shlex` splits WORDS; it does not know where a command ends. It returns
`…qlib.git@sha&&pip` as a single word for the whitespace-free form, and it
raises outright on a trailing-backslash continuation — and in both cases the
guard's answer was to skip silently, so the coverage was empty exactly where it
mattered. The guard therefore scans the shell's own lexical constructs — a
finite set: single quotes, double quotes, backslash escapes, comments, line
continuations, here-documents, and the command separators — and refuses loudly
what it cannot read.

The same distinction applies to the metadata side: matching `"…"` in
`pyproject.toml` reads only ONE of TOML's string syntaxes, so a single-quoted
requirement disappears from the scan while the other entries keep the "we read
something" floor satisfied. The file is parsed as TOML instead.

Runtime dependencies already carried upper bounds for numpy / scipy / pandas for
exactly this reason; the gap was that the reasoning had never been extended to
the rest of the base list, nor to the tools that judge.

#### Scenario: an unbounded dependency in anything CI installs fails the build
- **WHEN** any requirement in the base list, or in an extra a workflow installs,
  carries no upper bound
- **THEN** the governance test fails and names the requirement

#### Scenario: both TOML string syntaxes are read
- **WHEN** a requirement is declared with TOML's single-quoted literal syntax
  rather than double quotes
- **THEN** it is checked like any other, because the file is parsed as TOML

#### Scenario: the covered groups follow the workflows
- **WHEN** a workflow changes which extras it installs
- **THEN** the enforced set follows it, because the test reads the install lines
  instead of restating the group names

#### Scenario: a major-only bound on a judging tool is rejected
- **WHEN** the test runner, one of its plugins, the type checker, or the linter
  is bounded above its floor's next minor — including a dotted form such as
  `<10.0`
- **THEN** the governance test fails, because the failure this rule exists for
  was a minor-version bump

### Requirement: A dependency window restated outside project metadata SHALL be pinned to it

Any dependency window restated outside `pyproject.toml` SHALL be kept
byte-identical to the declaration, enforced by a test that scans EVERY workflow
rather than a named one, and the install command that resolves qlib before the
project SHALL itself carry both windows. Only executable content SHALL be
scanned — a commented-out command is not an install.

The numpy and scipy windows are inlined in the CI workflows because qlib is
installed BEFORE the project and therefore cannot pick the constraint up from
project metadata. Restating is necessary; drifting apart silently is not — the
REGEN-2 determinism anchor reproduces only inside that window, so a workflow
that installs a different one would move the anchor without any spec changing.

Naming one workflow is not enough: the window is restated in the test workflow
AND in the baseline-regeneration workflow, and the regeneration path is the one
that actually produces the anchor.

#### Scenario: every workflow that restates a window names the same one
- **WHEN** any workflow's numpy or scipy constraint differs from
  `pyproject.toml`
- **THEN** the governance test fails and names the workflow

#### Scenario: the scan covers workflows nobody listed
- **WHEN** a new workflow restates one of these windows
- **THEN** it is checked automatically, because the test discovers workflows
  instead of naming them — regardless of what that workflow installs

#### Scenario: the constraint sits on the command that needs it
- **WHEN** the pre-project qlib install drops a window while the same string
  still appears elsewhere in that workflow — including later on the SAME
  physical line, joined by a shell operator
- **THEN** the governance test fails, because the unconstrained first resolve is
  what produces the incompatible environment

#### Scenario: every local-project install spelling is covered
- **WHEN** a workflow installs the project with `-e`, with `--editable`, or with
  no editable flag at all
- **THEN** the extras it names enter the checked coverage the same way, because
  the target is what identifies the install

#### Scenario: only executable content counts as an install
- **WHEN** a workflow carries an installation line inside a shell comment, or a
  step `name:` that merely quotes such a command
- **THEN** neither contributes to the derived coverage and neither can by itself
  turn the governance test red

#### Scenario: quoting style is not a special case
- **WHEN** a workflow restates a pinned window with single quotes, double
  quotes, or none
- **THEN** it is compared the same way, because the command is shell-tokenised
  before comparison

#### Scenario: a separator needs no surrounding whitespace
- **WHEN** two commands are joined without spaces, as in
  `pip install <qlib>&&pip install "numpy…"`
- **THEN** they are still two commands, because the boundary is found by
  scanning the text rather than by looking for a separator among split words

#### Scenario: a command continued across lines stays one command
- **WHEN** an install is written with a trailing backslash and continues on the
  next line
- **THEN** the extras it names still enter the derived coverage

#### Scenario: a here-document body is data, not commands
- **WHEN** a step feeds a script to an interpreter via `<<`
- **THEN** the body is not read as shell, and the real commands in that step
  remain covered

#### Scenario: unreadable shell is refused, not skipped
- **WHEN** a `run` block cannot be lexed — an unclosed quote, an unterminated
  here-document
- **THEN** the governance test fails saying so, because a guard that silently
  reads nothing is empty in a way nobody can see
