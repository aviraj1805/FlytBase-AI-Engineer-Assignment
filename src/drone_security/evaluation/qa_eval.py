"""Context-management experiment: how should a long video reach the model?

Conditions, each answering the same fixed question set (data/eval/qa_questions.yaml):

* offline       deterministic analyst over the five tools (no LLM)            [always]
* naive         every frame and alert pasted into the prompt, no tools         [Gemini]
* hierarchical  day/hour summaries + rolling window in the prompt, plus tools  [Gemini]  <- the system
* tools_only    property facts only in the prompt, plus tools (ablation)       [Gemini]
* summaries     hierarchical context, no tools (ablation)                      [Gemini]

Measured per condition: accuracy (automatic grading against the ground truth), correct
abstention on unanswerable questions, prompt tokens per question (Gemini's own count),
latency, and citations that point at frames the model was never shown (hallucinated evidence).
"""
from __future__ import annotations

import re
import statistics
import time
from pathlib import Path
from typing import Optional

import yaml

from ..agent import NOT_ENOUGH_EVIDENCE, AnalystToolkit, OfflineAnalyst, check_citations, cited_frame_ids
from ..config import PROJECT_ROOT, PropertyConfig
from ..memory import estimate_tokens, property_lines, render_from_index, render_naive
from .scenario_eval import run_scenario

QUESTIONS = PROJECT_ROOT / "data" / "eval" / "qa_questions.yaml"
HELDOUT = PROJECT_ROOT / "data" / "eval" / "qa_heldout.yaml"
NO_TOOL_PROMPT = """You are the security analyst for a docked patrol drone that monitors one property.
Answer the operator's question using ONLY the footage record below. Cite every fact with its frame id
written exactly as [f123]. If the record does not contain the answer, reply "{not_enough}" and say what
is missing. Bare clock times refer to the footage day. Be concise: the direct answer first.

# Footage record
{context}"""


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]


def grade(answer: str, question: dict) -> bool:
    text = answer.lower()
    abstained = NOT_ENOUGH_EVIDENCE.lower()[:20] in text
    if question.get("abstain"):
        return abstained
    if abstained and not cited_frame_ids(answer):
        return False
    if not all(re.search(p, answer, re.IGNORECASE) for p in question.get("all", [])):
        return False
    return not question.get("any") or any(re.search(p, answer, re.IGNORECASE) for p in question["any"])


def run_qa_eval(config: PropertyConfig, *, gemini=None, conditions: Optional[list[str]] = None, seed: int = 7,
                pause_s: float = 0.0, questions_path: Path = QUESTIONS) -> dict:
    run = run_scenario(config, seed=seed)
    index = run.pipeline.index
    questions = load_questions(questions_path)
    hierarchical = render_from_index(index, config)
    naive = render_naive(index, config)
    contexts = {"naive": naive, "hierarchical": hierarchical, "tools_only": "\n".join(property_lines(config)),
                "summaries": hierarchical, "offline": ""}
    sizes = {name: {"chars": len(text), "estimated_tokens": estimate_tokens(text)} for name, text in contexts.items()}
    if gemini is not None:
        for name in ("naive", "hierarchical", "tools_only"):
            try:
                sizes[name]["gemini_tokens"] = gemini.count_tokens(contexts[name])
            except Exception:  # token counting is a nice-to-have; never lose a run over it
                sizes[name]["gemini_tokens"] = None

    wanted = conditions or (["offline", "naive", "hierarchical", "tools_only", "summaries"] if gemini else ["offline"])
    results = {}
    for condition in wanted:
        answerer = _answerer(condition, index, config, contexts, gemini)
        rows = []
        for q in questions:
            started = time.perf_counter()
            answer, usage, allowed = answerer(q["q"])
            latency = time.perf_counter() - started
            citations = check_citations(answer, allowed)
            rows.append({"id": q["id"], "scope": q["scope"], "question": q["q"], "answer": answer,
                         "correct": grade(answer, q), "latency_s": round(latency, 2), **usage,
                         "cited": citations["cited"], "unsupported_citations": citations["unsupported"]})
            if pause_s:
                time.sleep(pause_s)
        results[condition] = _summarise(rows)
    return {"seed": seed, "frames": index.stats()["frames"], "questions": len(questions), "question_file": Path(questions_path).name,
            "context_sizes": sizes,
            "model": getattr(gemini, "model", None), "results": results}


def _answerer(condition: str, index, config: PropertyConfig, contexts: dict, gemini):
    """Returns ask(question) -> (answer, usage dict, frame ids the model was allowed to see)."""
    if condition == "offline":
        analyst = OfflineAnalyst(AnalystToolkit(index, config), config)

        def ask_offline(question: str):
            analyst.tools.retrieved_ids = set()
            analyst.focus = None
            answer = analyst.ask(question).text
            return answer, {"prompt_tokens": 0, "model_calls": 0, "tool_calls": len(analyst._calls)}, analyst.tools.retrieved_ids
        return ask_offline

    if condition in ("naive", "summaries"):
        context = contexts[condition]
        system = NO_TOOL_PROMPT.replace("{not_enough}", NOT_ENOUGH_EVIDENCE).replace("{context}", context)
        visible = cited_frame_ids(context)  # frame ids the model was shown

        def ask_prompt_only(question: str):
            before = gemini.usage.prompt_tokens
            answer = gemini.text(question, system=system, purpose=f"qa_{condition}")
            return answer, {"prompt_tokens": gemini.usage.prompt_tokens - before, "model_calls": 1, "tool_calls": 0}, visible
        return ask_prompt_only

    from ..agent.langchain_agent import LangChainAnalyst
    from ..gemini import load_api_key

    toolkit = AnalystToolkit(index, config)
    agent = LangChainAnalyst(toolkit, contexts[condition], model=gemini.model, api_key=load_api_key())

    def ask_agent(question: str):
        agent.reset()
        answer = agent.ask(question)
        usage = {**agent.last_usage, "tool_calls": len(answer.tool_calls)}
        return answer.text, usage, toolkit.retrieved_ids | agent.context_ids
    return ask_agent


def _summarise(rows: list[dict]) -> dict:
    answerable = [r for r in rows if r["scope"] != "unanswerable"]
    unanswerable = [r for r in rows if r["scope"] == "unanswerable"]
    return {
        "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 3),
        "answerable_accuracy": round(sum(r["correct"] for r in answerable) / len(answerable), 3),
        "abstention_accuracy": round(sum(r["correct"] for r in unanswerable) / len(unanswerable), 3) if unanswerable else None,
        "by_scope": {s: round(statistics.mean(r["correct"] for r in rows if r["scope"] == s), 3)
                     for s in sorted({r["scope"] for r in rows})},
        "mean_prompt_tokens": round(statistics.mean(r.get("prompt_tokens", 0) for r in rows)),
        "mean_latency_s": round(statistics.mean(r["latency_s"] for r in rows), 2),
        "answers_with_unsupported_citations": sum(bool(r["unsupported_citations"]) for r in rows),
        "rows": rows,
    }


def write_qa_report(result: dict, out_dir: Path, name: str = "qa_context_eval") -> Path:
    from .report import write_json

    write_json(result, out_dir, name, f"python -m drone_security eval-qa ({result['question_file']})")
    sizes = result["context_sizes"]
    lines = [
        "# Context-management experiment",
        "",
        f"{result['questions']} fixed questions (data/eval/{result['question_file']}) about the generated day "
        f"({result['frames']} frames, seed {result['seed']}). Model: `{result['model'] or 'none (offline only)'}`. "
        f"Raw data: `{name}.json`.",
        "",
        "## Context size",
        "",
        "| context | characters | tokens (estimate, 4 chars/token) | tokens (Gemini count) |",
        "|---|---|---|---|",
        *[f"| {k} | {v['chars']:,} | {v['estimated_tokens']:,} | {v.get('gemini_tokens', '-')} |"
          for k, v in sizes.items() if k != "offline"],
        "",
        "## Answers",
        "",
        "| condition | accuracy | answerable | abstention | whole-day questions | mean prompt tokens | mean latency s | answers with made-up citations |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cond, r in result["results"].items():
        lines.append(f"| {cond} | {r['accuracy']} | {r['answerable_accuracy']} | {r['abstention_accuracy']} | "
                     f"{r['by_scope'].get('whole day', '-')} | {r['mean_prompt_tokens']:,} | {r['mean_latency_s']} | "
                     f"{r['answers_with_unsupported_citations']} |")
    lines += ["", "## Per question", "", "| id | question | " + " | ".join(result["results"]) + " |",
              "|---|---|" + "---|" * len(result["results"])]
    conds = list(result["results"])
    for i, row in enumerate(result["results"][conds[0]]["rows"]):
        lines.append(f"| {row['id']} | {row['question']} | " + " | ".join(
            "ok" if result["results"][c]["rows"][i]["correct"] else "wrong" for c in conds) + " |")
    path = out_dir / f"{name}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


__all__ = ["run_qa_eval", "write_qa_report", "grade", "load_questions"]
