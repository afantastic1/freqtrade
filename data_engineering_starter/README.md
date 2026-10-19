# Alpha Lab Data Engineering — Retired transfer archive

This directory is a preserved transfer archive from the initial v0.1 handoff. It is not a runtime package and must not be maintained as a second implementation.

The only maintained runtime source is the version-controlled `data_engineering/` package at the root of this repository. Install that package directly:

```bash
python -m pip install -e 'data_engineering[test]'
python -m data_engineering --help
```

`install.sh` is retained only as a guard against accidentally reinstalling copied source. See [`docs/data_engineering.md`](../docs/data_engineering.md) for the current package and PIT contracts.
