# Fixtures

`cargo-build-workspace.jsonl` and `cargo-build-sample.jsonl` are the output of
`cargo build --locked -p <package> --message-format=json` for the two projects
below. The temporary directory is rewritten to a fixed `/tmp/roadie-cargo-*`
path, because the tests create the reported executable there.

Regenerate both with:

```sh
set -euo pipefail
work=$(cd "$(mktemp -d)" && pwd -P)
fixtures=$PWD/tests/fixtures

# A workspace whose package `api_server` lives in `server/`, has a build
# script, a library and the binary `api-server`, and depends on `decoy`.
mkdir -p "$work/ws/server/src" "$work/ws/decoy/src"
printf '[workspace]\nresolver = "2"\nmembers = ["server", "decoy"]\n' > "$work/ws/Cargo.toml"
printf '[package]\nname = "decoy"\nversion = "0.1.0"\nedition = "2021"\n' > "$work/ws/decoy/Cargo.toml"
printf '[package]\nname = "api_server"\nversion = "0.1.0"\nedition = "2021"\n\n[[bin]]\nname = "api-server"\npath = "src/main.rs"\n\n[dependencies]\ndecoy = { path = "../decoy" }\n' > "$work/ws/server/Cargo.toml"
echo '' > "$work/ws/decoy/src/lib.rs"
echo '' > "$work/ws/server/src/lib.rs"
echo 'fn main() {}' > "$work/ws/server/src/main.rs"
echo 'fn main() {}' > "$work/ws/server/build.rs"
(cd "$work/ws" && cargo generate-lockfile && cargo build --locked -p api_server --message-format=json) \
  | sed "s#$work/ws#/tmp/roadie-cargo-ws#g" > "$fixtures/cargo-build-workspace.jsonl"

# A standalone package `sample` with the same parts, depending on `decoy`.
mkdir -p "$work/sample/sample/src" "$work/sample/decoy/src"
printf '[package]\nname = "decoy"\nversion = "0.1.0"\nedition = "2021"\n' > "$work/sample/decoy/Cargo.toml"
printf '[package]\nname = "sample"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\ndecoy = { path = "../decoy" }\n' > "$work/sample/sample/Cargo.toml"
echo '' > "$work/sample/decoy/src/lib.rs"
echo '' > "$work/sample/sample/src/lib.rs"
echo 'fn main() {}' > "$work/sample/sample/src/main.rs"
echo 'fn main() {}' > "$work/sample/sample/build.rs"
(cd "$work/sample/sample" && cargo generate-lockfile && cargo build --locked -p sample --message-format=json) \
  | sed "s#$work/sample#/tmp/roadie-cargo-sample#g" > "$fixtures/cargo-build-sample.jsonl"
```

Run it from the repository root.
