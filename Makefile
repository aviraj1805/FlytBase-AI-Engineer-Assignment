# Thin wrapper: every target is implemented once, in make.py, so Windows users can run
# `python make.py <target>` and get exactly the same behaviour as `make <target>`.
PY ?= python

.PHONY: setup run test demo eval eval-gemini benchmark
setup run test demo eval eval-gemini benchmark:
	$(PY) make.py $@
