#!/usr/bin/env python3
"""Run one GAIA validation text-only task against the API server."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "../../data/gaia-2023-validation-text-103/standardized_data.jsonl"
).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use one GAIA validation text-only task (by task_id) to test /v1/api/execute."
    )
    parser.add_argument(
        "--task-id",
        required=True,
        help="GAIA task_id from gaia-2023-validation-text-103/standardized_data.jsonl",
    )
    parser.add_argument(
        "--dataset-path",
        default=str(DEFAULT_DATASET_PATH),
        help=f"Path to standardized_data.jsonl (default: {DEFAULT_DATASET_PATH})",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://localhost:7210",
        help="API server base url (default: http://localhost:7210)",
    )
    parser.add_argument(
        "--auth-token",
        default="test_token_xyz",
        help="Authorization token; script will send it as Bearer token",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help="Read timeout for streaming response in seconds; <=0 disables read timeout (default: 0)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=10,
        help="Connect timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional X-Session-Id; auto-generated when omitted",
    )
    parser.add_argument(
        "--match-mode",
        choices=["contains", "exact", "none"],
        default="contains",
        help="How to compare model output with ground_truth (default: contains)",
    )
    return parser.parse_args()


def load_task_by_id(dataset_path: Path, task_id: str) -> dict[str, Any]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    with dataset_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("task_id") == task_id:
                return row

    raise ValueError(f"task_id not found in dataset: {task_id}")


def ensure_health(base_url: str, timeout: float) -> None:
    with build_session(base_url) as session:
        response = session.get(f"{base_url.rstrip('/')}/health", timeout=timeout)
    response.raise_for_status()


def stream_execute(
    base_url: str,
    task_question: str,
    session_id: str,
    auth_token: str,
    timeout: float,
    connect_timeout: float,
) -> tuple[str, bool]:
    url = f"{base_url.rstrip('/')}/v1/api/execute"
    token_value = auth_token.strip()
    if token_value.lower().startswith("bearer "):
        auth_header = token_value
    else:
        auth_header = f"Bearer {token_value}"
    headers = {
        "Content-Type": "application/json",
        "X-Session-Id": session_id,
        "Authorization": auth_header,
    }
    payload = {
        "message": [
            {
                "type": "query",
                "step": 1,
                "content": task_question,
            }
        ]
    }

    answer_chunks: list[str] = []
    has_end_event = False
    read_timeout = None if timeout <= 0 else timeout

    with build_session(base_url) as session:
        with session.post(
            url,
            headers=headers,
            json=payload,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    print(f"[WARN] Skip non-JSON line: {line[:120]}", file=sys.stderr)
                    continue

                event_type = event.get("type")
                if event_type == "answer":
                    delta = event.get("delta", "")
                    if delta:
                        answer_chunks.append(delta)
                        print(delta, end="", flush=True)
                elif event_type == "end":
                    has_end_event = True

    print()
    return "".join(answer_chunks).strip(), has_end_event


def evaluate(predicted: str, ground_truth: str) -> tuple[bool, bool]:
    predicted_norm = predicted.strip().lower()
    ground_truth_norm = ground_truth.strip().lower()
    exact = predicted_norm == ground_truth_norm
    contains = ground_truth_norm in predicted_norm
    return exact, contains


def build_session(base_url: str) -> requests.Session:
    session = requests.Session()
    host = (urlparse(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        # Local API calls should not be routed via system proxy.
        session.trust_env = False
    return session


def main() -> int:
    try:
        args = parse_args()

        dataset_path = Path(args.dataset_path).expanduser().resolve()
        task = load_task_by_id(dataset_path, args.task_id)
        session_id = args.session_id or f"gaia-task-{args.task_id[:8]}-{int(time.time())}"

        print(f"[INFO] dataset: {dataset_path}")
        print(f"[INFO] task_id: {task['task_id']}")
        print(f"[INFO] session_id: {session_id}")
        print(f"[INFO] checking health: {args.api_base_url}/health")
        ensure_health(args.api_base_url, timeout=max(args.connect_timeout, 1))
        print("[INFO] health check passed")

        print("\n===== QUESTION =====")
        print(task["task_question"])
        print("====================")
        print("\n===== STREAMED ANSWER =====")

        answer, has_end_event = stream_execute(
            base_url=args.api_base_url,
            task_question=task["task_question"],
            session_id=session_id,
            auth_token=args.auth_token,
            timeout=args.timeout,
            connect_timeout=args.connect_timeout,
        )

        ground_truth = str(task.get("ground_truth", "")).strip()
        exact, contains = evaluate(answer, ground_truth)

        print("\n===== RESULT =====")
        print(f"ground_truth : {ground_truth}")
        print(f"predicted    : {answer}")
        print(f"exact_match  : {exact}")
        print(f"contains_gt  : {contains}")
        print(f"has_end_event: {has_end_event}")

        if args.match_mode == "exact":
            passed = exact and has_end_event
        elif args.match_mode == "contains":
            passed = contains and has_end_event
        else:
            passed = has_end_event

        if not passed:
            print(f"[FAIL] match_mode={args.match_mode}", file=sys.stderr)
            return 1

        print(f"[PASS] match_mode={args.match_mode}")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
