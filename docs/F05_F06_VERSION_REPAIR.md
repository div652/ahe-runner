# F05, F06, and Release-Version Repair

This repair closes the three localized issues retained by the release 0.3.0
closure check. It does not change the AHE evolution algorithm, benchmark
ownership, NexAU executor, checkpoint lifecycle, or model transport.

## F05: exact citation coverage

### Failure

Line-mode `read_file` bounded output by bytes. When the allowance ended exactly
after one complete line, the loop inspected the next line with zero bytes
remaining and incorrectly credited that entirely unread line in the Debugger
access ledger. A partially returned final line was also credited as though the
whole line had been returned.

### Contract

- A line citation is covered only when every byte of every cited line was
  returned by a line-mode read or by an exact search hit.
- A partially returned line does not cover a line citation.
- Returned partial bytes may cover only their exact half-open byte range.
- A wholly unread next line contributes no line or byte coverage.

### Implementation

Line reads now operate on binary lines so source byte offsets remain exact.
Complete lines and a possible partial final line are recorded separately:

- complete lines enter the inclusive line-range ledger;
- partial content enters the half-open byte-range ledger;
- response metadata identifies the partial line and exact byte bounds.

The regression test reproduces the exact 18-byte boundary, verifies rejection
of the unread second-line citation, verifies rejection of a partially returned
line citation, accepts the exact partial-byte citation, and accepts line 2 only
after a complete read.

## F06: lazy nested-package imports

### Failure

The ABI V2 static import graph found the final imported module but omitted the
ancestor `__init__.py` files Python executes while importing a regular package.
Consequently, a function-local import could execute correctly in NexAU while
conformance rejected a declared package initializer as unattached.

### Contract

For a statically resolvable local module import, conformance attaches:

1. every existing ancestor package initializer on that module path;
2. the resolved module or package initializer;
3. statically resolvable dependencies of each attached initializer and module.

Unrelated initializers and unreferenced Python files remain invalid. Dynamic
imports remain outside this static contract.

The parameterized regression exercises eager and function-local imports using
both absolute and relative syntax. It verifies the complete registration set
and executes the resulting tool through the same import scope used by the
runner.

## Release-version authority

`pyproject.toml` remains the release metadata authority. Runtime
`ahe_runner.__version__` is now derived from installed distribution metadata
instead of duplicating a stale literal. If source is imported without an
installed distribution, it reports `0+unknown` rather than asserting an
unverified release.

The regression verifies that:

- project metadata reports `0.3.0`;
- installed distribution metadata reports the same version;
- `ahe_runner.__version__` reports the same version;
- a fresh campaign records that version in `campaign_manifest.json`.

The runtime-backed example documentation is also corrected from evidence
schema V2 to V3.

## Validation plan

After this implementation and its tests are committed:

1. run the focused F05, F06, and version regressions;
2. run the complete test suite and static checks;
3. build wheel and source distributions;
4. install the wheel into a clean environment;
5. verify wheel metadata, packaged runtime version, and a fresh campaign
   manifest all report `0.3.0`.

## Validation result

Validated from implementation commit `fe9c03e`:

```text
Focused F05/F06/version regressions: 6 passed in 5.68s
Complete suite:                       63 passed in 83.56s
Ruff:                                 all checks passed
Format:                               100 files already formatted
Compileall:                           exit 0
```

Release artifacts:

```text
ahe_runner-0.3.0-py3-none-any.whl
sha256: 3fa5ea8d924f5f7aa1e39bc127c3aa9f6b87039c11d47a6df1559d6bf2149b1b

ahe_runner-0.3.0.tar.gz
sha256: 6ab493bba29f33993aa96583df974accff7a22f98bc16dc81c35524cb06c7d9b
```

The wheel was installed into a new Python 3.14.7 environment. A zero-round
scripted campaign created from the installed CLI recorded:

```json
{
  "campaign_runner_version": "0.3.0",
  "runtime_version": "0.3.0",
  "wheel_metadata": "0.3.0"
}
```

No live model endpoint was contacted.
