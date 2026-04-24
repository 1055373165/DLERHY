"""Golden PDF regression fixtures (PDF v2 M1 exit criterion).

This package contains programmatic PDF generators — not checked-in
binaries — because:

  1. A source-controlled synthesizer is self-documenting: the failure
     mode each fixture targets is the code, not an opaque byte blob.
  2. Binary PDFs drift (version, tool) and would need re-reviewing on
     every toolchain bump.
  3. Some failure modes (broken ToUnicode maps, injected PUA) are
     awkward to reproduce with real-world tools.

Each generator is a pure function that returns `bytes` and accepts only
deterministic parameters, so regression runs are reproducible without
filesystem state.
"""
