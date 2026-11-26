.PHONY: eval-smoke eval-full eval-behavior eval-citation eval-gate

eval-smoke:
	uv run python -m tools.evalkit.run --suite smoke

eval-full:
	uv run python -m tools.evalkit.run --suite full

eval-behavior:
	uv run python -m tools.eval_behavior

eval-citation:
	uv run python -m tools.eval_citation --sample 50

eval-gate:
	uv run python -m tools.eval_gate
