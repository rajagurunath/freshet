"""CLI: benchmark cross-session retrieval strategies on the synthetic corpus.

    python -m contexthub.eval.run                 # local MiniLM, baseline
    python -m contexthub.eval.run --embedder hash # offline smoke (not semantic)
    python -m contexthub.eval.run --by-type       # break metrics down per question type

As later slices land (graph arm, reranker, MMR), register them in ``RETRIEVERS``
so a single run prints the full A/B table and the moat lift is visible at a glance.
"""

from __future__ import annotations

import argparse

from contexthub.eval.harness import build_env
from contexthub.eval.metrics import evaluate, evaluate_by_type

_COLS = ["hit@3", "recall@10", "mrr", "ndcg@10", "map"]


def _fmt_row(name: str, m: dict[str, float]) -> str:
    cells = "  ".join(f"{m.get(c, 0.0):.3f}" for c in _COLS)
    return f"{name:<22} {cells}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Cross-session retrieval benchmark")
    ap.add_argument("--embedder", default="local", choices=["local", "hash"])
    ap.add_argument("--body-chars", type=int, default=2400)
    ap.add_argument("--by-type", action="store_true",
                    help="break the baseline metrics down by question type")
    args = ap.parse_args()

    env = build_env(embedder_provider=args.embedder, body_chars=args.body_chars,
                    with_graph=True)
    try:
        corpus = env.corpus
        n_sessions = len(corpus.items)
        n_q = len(corpus.questions)
        print(f"\nCorpus: {n_sessions} sessions, {n_q} silver questions "
              f"(embedder={args.embedder})\n")

        # Registry of named retrievers. Later slices append here.
        retrievers = {
            "baseline (vec+fts)": env.baseline_retriever(),
            "graph (vec+fts+graph)": env.graph_retriever(),
            "graph+rerank": env.rerank_retriever(use_graph=True),
        }

        header = f"{'retriever':<22} " + "  ".join(f"{c:>9}" for c in _COLS)
        print(header)
        print("-" * len(header))
        for name, r in retrievers.items():
            m = evaluate(r, corpus.questions)
            print(_fmt_row(name, m))

        if args.by_type:
            for name, r in retrievers.items():
                print(f"\n{name} — by question type:")
                by = evaluate_by_type(r, corpus.questions)
                print(f"{'type':<22} " + "  ".join(f"{c:>9}" for c in _COLS))
                print("-" * len(header))
                for t, m in sorted(by.items()):
                    print(_fmt_row(t, m))
        print()
    finally:
        env.close()


if __name__ == "__main__":
    main()
