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
editable spelling precedes it, and a pip invocation by its EXECUTABLE — `pip`
with pip's own version-suffix naming scheme, path-prefixed or via `python -m
pip`, in the command's EXECUTABLE POSITION (after POSIX assignment prefixes) —
not by one literal spelling, and not by scanning arguments: `pip install …`
quoted inside an `echo` is an example being logged, not an installation. Dependencies that JUDGE the
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

#### Scenario: every pip executable spelling is covered
- **WHEN** a workflow invokes pip as `pip`, `pip3`, `pip3.X`, with a path
  prefix, or through `python -m pip`
- **THEN** the install enters the derived coverage the same way, while
  lookalikes such as `pipx` do not

#### Scenario: pip named in an argument is not an installation
- **WHEN** a command merely mentions an install — `echo pip install -e
  ".[research]"` logging an example
- **THEN** it contributes nothing to the derived coverage, because the
  executable is a position, not a substring — a false positive here turns
  governance red for dependencies CI never installs

#### Scenario: subshell parentheses are command syntax
- **WHEN** an install is wrapped in unquoted grouping parentheses
- **THEN** it is recognised like any other command, while parentheses inside
  quotes remain word characters

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
- **WHEN** a step feeds a script to an interpreter via `<<` with a QUOTED
  delimiter
- **THEN** the body is not read as shell, and the real commands in that step
  remain covered

#### Scenario: a here-document ends exactly where the shell says it ends
- **WHEN** a body line merely resembles the delimiter — indented `  EOF` — or
  the form is `<<-` where only leading TABS are stripped
- **THEN** the body is not terminated early: what follows a lookalike line is
  still data, never commands, so no coverage is invented from heredoc content

#### Scenario: a heredoc delimiter undergoes real quote removal
- **WHEN** the delimiter is partially quoted (`E'O'F`) or backslash-quoted
- **THEN** the effective delimiter is the shell's (`EOF`), the body counts as
  literal, and the workflow is not misjudged unterminated

#### Scenario: an unquoted here-document body stays honest
- **WHEN** the delimiter is unquoted — the shell performs command substitution
  inside the body
- **THEN** a body containing `$(` or a backtick is refused loudly, while a
  plain-text body (variable expansion cannot run a command) is still skipped

#### Scenario: a direct workflow install target is bounded like any other
- **WHEN** a workflow installs a package directly, outside `pyproject.toml` —
  the `pip` bootstrap upgrade itself being the standing example
- **THEN** that requirement must carry an upper bound, an unpinned `git+`
  source install is named, and an unclassifiable target (usually the value of
  a bare option) is refused loudly with the `--opt=value` remedy

#### Scenario: file-sourced install content is refused, not skipped
- **WHEN** a pip install carries `-r/--requirement` or `-c/--constraint`, bare
  or `=`-joined — pip installs or constrains from a file the command text
  cannot see
- **THEN** the guard refuses loudly instead of skipping the option, so no
  dependency can enter CI through a file outside the derived coverage

#### Scenario: the qlib reference must be an immutable commit
- **WHEN** the qlib source suffix after `@` is anything but a full 40-hex
  commit SHA — a branch like `@main`
- **THEN** it is flagged: a moving reference lets CI's qlib code drift between
  runs while every guard stays green

#### Scenario: an equals-joined editable target is still a target
- **WHEN** the project is installed as `--editable=.[extras]`
- **THEN** the extras enter the derived coverage and the parseability check
  sees the command, because target candidates include `=`-joined option values

#### Scenario: pip behavior injected through the environment is refused
- **WHEN** a pip command carries a `PIP_*` assignment prefix — pip maps every
  option to a `PIP_<OPTION>` environment variable, so `PIP_DRY_RUN=1` installs
  nothing while the arguments look like a real install
- **THEN** the guard refuses loudly and demands explicit flags, which the
  existing dry-run and file-sourcing judgements then govern

#### Scenario: workflow env mappings cannot configure pip either
- **WHEN** a `PIP_*` key is declared in a workflow-, job-, or step-level `env:`
  mapping — GitHub Actions applies all three to the run command, invisibly to
  its text
- **THEN** the governance test fails naming the key, with a floor proving the
  env collection actually read the existing declarations

#### Scenario: attached short-option values are the same option
- **WHEN** a file-sourcing or editable option carries its value attached —
  `-rrequirements.txt`, `-e.[extras]` — as optparse permits
- **THEN** it is treated exactly like the spaced or `=`-joined spelling:
  file-sourcing is refused loudly, an editable target enters the coverage

#### Scenario: a dry run is not an install
- **WHEN** a pip install carries `--dry-run` — defined by pip as not actually
  installing anything
- **THEN** it contributes nothing: neither to the derived coverage nor to the
  qlib-pin presence check, which a dry-run carrying the pin and both windows
  would otherwise satisfy while qlib stays absent

#### Scenario: the qlib pin must sit on an actual install
- **WHEN** the qlib URL appears only in a command that is not a pip install —
  an `echo` documenting it, for instance
- **THEN** that command satisfies neither the presence floor nor the
  carries-both-windows assertion, because `pytest.importorskip("qlib")` would
  let a missing install turn qlib-dependent CI silently green

#### Scenario: install must be pip's subcommand, not any argument
- **WHEN** `install` appears among a pip command's arguments without being its
  subcommand — `pip --help install ".[research]"` prints help and installs
  nothing
- **THEN** it contributes nothing to the derived coverage; help flags preempt,
  and a bare option before the subcommand position is refused loudly because a
  value-taking global option would make that position undecidable

#### Scenario: an option value shaped like a target is ambiguous, loudly
- **WHEN** a pip install carries both an option value and an install target
  that match the local-target shape, as in `--find-links . ".[research]"`
- **THEN** the guard refuses loudly and demands the `--opt=value` spelling,
  because telling them apart needs pip's own option table — a set this guard
  does not own

#### Scenario: unreadable shell is refused, not skipped
- **WHEN** a `run` block cannot be lexed — an unclosed quote, an unterminated
  here-document
- **THEN** the governance test fails saying so, because a guard that silently
  reads nothing is empty in a way nobody can see

#### Scenario: command substitution is refused, not modelled
- **WHEN** `$(` or a backtick appears anywhere — inside double quotes, where
  treating the range as opaque would silently HIDE an install, or unquoted,
  where treating the parentheses as command boundaries would INVENT a command
  the shell never runs (the substitution's output is the outer command's
  argument)
- **THEN** the lexer refuses loudly; escaped `\$` remains a literal character,
  and the workflows-all-lex guard keeps the construct out of the repository

#### Scenario: a continuation inside quotes vanishes before tokenising
- **WHEN** a double-quoted argument is continued with a backslash-newline
- **THEN** the token compares as the shell would execute it, with the
  continuation removed — while an escaped backslash followed by a real newline
  keeps its newline

#### Scenario: a control keyword does not hide an install
- **WHEN** an install is guarded by a POSIX reserved word — `if pip install
  ".[research]"; then …`, `while`, `until`
- **THEN** the executable is still found, because reserved words lead compound
  commands and the reserved-word list is the closed set POSIX defines

#### Scenario: an extras segment does not hide a restatement
- **WHEN** a workflow restates a pinned window with an extras segment —
  `numpy[feature]>=1.24,<2.1`
- **THEN** it is detected as that package's restatement and held to
  byte-identity with `pyproject.toml`

#### Scenario: a restated window is found under any spelling of the name
- **WHEN** a workflow restates a pinned window with a differently-cased or
  differently-separated package name, as PEP 503 permits
- **THEN** the restatement is detected by canonical name, and the byte-identical
  assertion then requires it to be rewritten to match `pyproject.toml` exactly
