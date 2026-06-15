# Rust Runtime Benchmarks

Run from the repo root:

```powershell
npm run bench:rust
```

The runner scans the Tauri crates for Rust benchmark targets and writes `benchmarks/rust/bench-rust-summary.json`. It skips cleanly while no `benches/*.rs` targets exist.

When Rust hot-path targets are added, prefer Criterion for wall-clock benchmarks and IAI-Callgrind for instruction-level regressions. CI already uploads `benchmarks/rust/**` artifacts.
