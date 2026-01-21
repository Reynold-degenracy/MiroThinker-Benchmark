#!/usr/bin/env python3
"""
GAIA Benchmark Testing Script for MiroFlow Agent API

This script extracts test cases from the GAIA benchmark dataset and tests
the MiroFlow Agent's capabilities through its API endpoint.

Usage:
    python test_gaia_benchmark.py --num-tests 5 --level 1 --api-url http://localhost:8000
"""

import argparse
import asyncio
import json
import logging
import random
import sys
import time
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("gaia_test_results.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class GAIABenchmarkTester:
    """Test MiroFlow Agent using GAIA benchmark questions"""
    
    def __init__(
        self,
        api_url: str = "http://localhost:8000",
        data_path: str = "./data/gaia-2023-validation/standardized_data.jsonl",
        timeout: int = 300
    ):
        """
        Initialize the GAIA benchmark tester
        
        Args:
            api_url: Base URL of the MiroFlow Agent API
            data_path: Path to the GAIA standardized data file
            timeout: Timeout in seconds for each test
        """
        self.api_url = api_url.rstrip('/')
        self.data_path = Path(data_path)
        self.timeout = timeout
        self.results = []
        
    def load_gaia_data(self) -> List[Dict]:
        """Load GAIA benchmark data from JSONL file"""
        if not self.data_path.exists():
            raise FileNotFoundError(f"GAIA data file not found: {self.data_path}")
        
        data = []
        with open(self.data_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
        
        logger.info(f"Loaded {len(data)} test cases from {self.data_path}")
        return data
    
    def filter_by_level(self, data: List[Dict], level: Optional[int] = None) -> List[Dict]:
        """Filter test cases by difficulty level"""
        if level is None:
            return data
        
        filtered = [item for item in data if item.get("metadata", {}).get("Level") == level]
        logger.info(f"Filtered to {len(filtered)} test cases at level {level}")
        return filtered
    
    def filter_text_only(self, data: List[Dict]) -> List[Dict]:
        """Filter test cases that don't require file attachments"""
        text_only = [item for item in data if not item.get("file_name")]
        logger.info(f"Filtered to {len(text_only)} text-only test cases")
        return text_only
    
    def sample_tests(self, data: List[Dict], num_tests: int) -> List[Dict]:
        """Randomly sample test cases"""
        if len(data) <= num_tests:
            return data
        return random.sample(data, num_tests)
    
    async def check_health(self) -> bool:
        """Check if the API server is healthy"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/health",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(f"API health check passed: {data}")
                        return True
                    else:
                        logger.error(f"API health check failed with status {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Failed to connect to API: {e}")
            return False
    
    async def query_agent(
        self,
        question: str,
        session_id: str,
        is_confirmed: bool = False
    ) -> Dict:
        """
        Query the MiroFlow Agent API
        
        Args:
            question: The question to ask
            session_id: Session identifier
            is_confirmed: Whether to confirm the plan
            
        Returns:
            Dict containing the agent's response and metadata
        """
        url = f"{self.api_url}/get_response"
        headers = {
            "Content-Type": "application/json",
            "X-Session-Id": session_id
        }
        payload = {
            "query": question,
            "history": None,
            "is_confirmed": is_confirmed
        }
        
        response_text = []
        plan_steps = []
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return {
                            "success": False,
                            "error": f"API returned status {response.status}: {error_text}",
                            "response_time": time.time() - start_time
                        }
                    
                    # Read streaming response
                    async for line in response.content:
                        if not line:
                            continue
                        
                        try:
                            line_str = line.decode('utf-8').strip()
                            if not line_str:
                                continue
                            
                            event = json.loads(line_str)
                            status = event.get("status")
                            data = event.get("data", "")
                            
                            if status == "plan":
                                step = event.get("step", 0)
                                plan_steps.append({"step": step, "data": data})
                                logger.debug(f"[Plan Step {step}] {data}")
                            elif status == "answer":
                                response_text.append(data)
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to decode JSON line: {line_str[:100]}... Error: {e}")
                            continue
            
            elapsed_time = time.time() - start_time
            full_response = "".join(response_text)
            
            return {
                "success": True,
                "answer": full_response.strip(),
                "plan_steps": plan_steps,
                "response_time": elapsed_time
            }
            
        except asyncio.TimeoutError:
            elapsed_time = time.time() - start_time
            return {
                "success": False,
                "error": f"Timeout after {elapsed_time:.2f} seconds",
                "response_time": elapsed_time
            }
        except Exception as e:
            elapsed_time = time.time() - start_time
            return {
                "success": False,
                "error": str(e),
                "response_time": elapsed_time
            }
    
    def evaluate_answer(self, predicted: str, ground_truth: str) -> Dict:
        """
        Evaluate the predicted answer against ground truth
        
        Args:
            predicted: Agent's answer
            ground_truth: Correct answer
            
        Returns:
            Dict with evaluation metrics
        """
        # Try using Gemini LLM via Python SDK (if available) to judge answers.
        # We attempt to use `google.generativeai` if installed and `GEMINI_API_KEY` is set.
        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set")

            # Try to import common Gemini SDK wrapper
            try:
                import google.generativeai as genai  # type: ignore
                genai.configure(api_key=api_key)

                prompt = (
                    "You are an automated judge. Compare the predicted answer and the ground truth. "
                    "Return a JSON object with the following fields: exact_match (true/false), "
                    "contains_match (true/false), word_overlap (0.0-1.0), verdict (Correct/Partial/Incorrect).\n\n"
                    f"Ground truth: {ground_truth}\n"
                    f"Predicted: {predicted}\n\n"
                    "Only output valid JSON with the keys: exact_match, contains_match, word_overlap, verdict"
                )

                # Use a deterministic / low-temperature call
                try:
                    response = genai.generate_text(model="gemini-1.0", prompt=prompt, temperature=0.0)
                    # Response shape varies; try common attributes
                    text = getattr(response, "text", None) or getattr(response, "content", None) or str(response)
                except Exception:
                    # Fallback to a different API surface if present
                    response = genai.generate(prompt=prompt, max_output_tokens=512, temperature=0.0)
                    text = getattr(response, "candidates", [None])[0] or str(response)

                # Extract first JSON object from the LLM output
                m = re.search(r"\{.*\}", text, flags=re.S)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                        # Ensure expected keys exist
                        return {
                            "exact_match": bool(parsed.get("exact_match", False)),
                            "contains_match": bool(parsed.get("contains_match", False)),
                            "word_overlap": float(parsed.get("word_overlap", 0.0)),
                            "llm_verdict": parsed.get("verdict")
                        }
                    except Exception:
                        logger.warning("Failed to parse JSON from Gemini response: %s", text[:200])

            except Exception as e:
                # If sdk import or call fails, fall back to local evaluation
                logger.warning("Gemini SDK evaluation unavailable: %s", e)

        except Exception as e:
            logger.debug("Skipping Gemini evaluation: %s", e)

        # --- Fallback: simple local evaluation (original behavior) ---
        # Normalize for comparison
        pred_normalized = predicted.lower().strip()
        gt_normalized = ground_truth.lower().strip()

        # Exact match
        exact_match = pred_normalized == gt_normalized

        # Contains match (ground truth is in prediction)
        contains_match = gt_normalized in pred_normalized

        # Word overlap
        pred_words = set(pred_normalized.split())
        gt_words = set(gt_normalized.split())
        if len(gt_words) > 0:
            overlap = len(pred_words & gt_words) / len(gt_words)
        else:
            overlap = 0.0

        return {
            "exact_match": exact_match,
            "contains_match": contains_match,
            "word_overlap": overlap,
            "llm_verdict": None
        }
    
    async def run_test(self, test_case: Dict, test_idx: int) -> Dict:
        """Run a single test case with optional file attachment handling"""
        task_id = test_case.get("task_id", f"test_{test_idx}")
        question = test_case.get("task_question", "")
        ground_truth = test_case.get("ground_truth", "")
        metadata = test_case.get("metadata", {})
        level = metadata.get("Level", "Unknown")
        file_name = test_case.get("file_name", "")
        
        logger.info(f"\n{'='*80}")
        logger.info(f"Test {test_idx + 1}: {task_id}")
        logger.info(f"Level: {level}")
        logger.info(f"Question: {question[:200]}...")
        logger.info(f"Ground Truth: {ground_truth}")
        logger.info(f"{'='*80}")
        
        # Generate unique session ID for this test
        session_id = f"gaia_test_{task_id}_{int(time.time())}"
        
        # Handle file attachments: upload to sandbox and reference local + sandbox paths in query
        sandbox_path = None
        local_path = None
        if file_name:
            candidate_path = self.data_path.parent / file_name
            if candidate_path.exists():
                local_path = str(candidate_path.resolve())
                logger.info(f"Attachment detected. Local path: {local_path}")
                # Try uploading to sandbox for tools that operate there
                try:
                    async with aiohttp.ClientSession() as session:
                        upload_url = f"{self.api_url}/upload_file"
                        headers = {"X-Session-Id": session_id}
                        form = aiohttp.FormData()
                        # Open file in binary mode; aiohttp sets content-type automatically
                        with open(local_path, "rb") as f:
                            form.add_field("file", f, filename=Path(local_path).name, content_type="application/octet-stream")
                            async with session.post(upload_url, headers=headers, data=form, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                                if resp.status == 200:
                                    resp_json = await resp.json()
                                    sandbox_path = resp_json.get("data", {}).get("path")
                                    logger.info(f"Uploaded to sandbox: {sandbox_path}")
                                else:
                                    err_text = await resp.text()
                                    logger.warning(f"Upload failed with status {resp.status}: {err_text}")
                except Exception as e:
                    logger.warning(f"Upload error: {e}")
            else:
                logger.warning(f"Attachment file not found: {candidate_path}")
        
        # Augment the question with attachment references so the agent can use the file
        augmented_question = question
        if local_path or sandbox_path:
            attach_lines = ["[Attachment Info]"]
            if local_path:
                attach_lines.append(f"Local path: {local_path}")
            if sandbox_path:
                attach_lines.append(f"Sandbox path: {sandbox_path}")
            attach_lines.append("Please use the attached file if relevant.")
            augmented_question = question + "\n\n" + "\n".join(attach_lines)
        
        # Query the agent
        result = await self.query_agent(augmented_question, session_id, is_confirmed=False)
        
        # Evaluate if we got an answer
        evaluation = None
        if result.get("success") and result.get("answer"):
            evaluation = self.evaluate_answer(result["answer"], ground_truth)
            logger.info(f"Answer: {result['answer'][:500]}...")
            logger.info(f"Evaluation: {evaluation}")
        else:
            logger.error(f"Failed: {result.get('error', 'Unknown error')}")
        
        # Compile test result
        test_result = {
            "test_idx": test_idx,
            "task_id": task_id,
            "level": level,
            "question": question,
            "ground_truth": ground_truth,
            "predicted_answer": result.get("answer", ""),
            "success": result.get("success", False),
            "error": result.get("error"),
            "response_time": result.get("response_time", 0),
            "plan_steps_count": len(result.get("plan_steps", [])),
            "evaluation": evaluation,
            "attached_file_local_path": local_path,
            "attached_file_sandbox_path": sandbox_path,
        }
        
        self.results.append(test_result)
        return test_result
    
    async def run_tests(
        self,
        num_tests: int = 5,
        level: Optional[int] = None,
        text_only: bool = True,
        seed: Optional[int] = None
    ):
        """
        Run multiple test cases from GAIA benchmark
        
        Args:
            num_tests: Number of tests to run
            level: Filter by difficulty level (1, 2, or 3)
            text_only: Only test cases without file attachments
            seed: Random seed for reproducibility
        """
        # Set random seed
        if seed is not None:
            random.seed(seed)
        
        # Check API health
        logger.info("Checking API health...")
        if not await self.check_health():
            logger.error("API is not healthy. Exiting.")
            return
        
        # Load and filter data
        logger.info("Loading GAIA benchmark data...")
        data = self.load_gaia_data()
        
        if level is not None:
            data = self.filter_by_level(data, level)
        
        if text_only:
            data = self.filter_text_only(data)
        
        # Sample tests
        test_cases = self.sample_tests(data, num_tests)
        logger.info(f"\nRunning {len(test_cases)} tests...")
        
        # Run tests
        for idx, test_case in enumerate(test_cases):
            await self.run_test(test_case, idx)
            
            # Add a small delay between tests
            if idx < len(test_cases) - 1:
                await asyncio.sleep(2)
        
        # Print summary
        self.print_summary()
        
        # Save results
        self.save_results()
    
    def print_summary(self):
        """Print test results summary"""
        logger.info(f"\n{'='*80}")
        logger.info("TEST SUMMARY")
        logger.info(f"{'='*80}")
        
        total = len(self.results)
        successful = sum(1 for r in self.results if r["success"])
        exact_matches = sum(1 for r in self.results 
                          if r.get("evaluation", {}).get("exact_match", False))
        contains_matches = sum(1 for r in self.results 
                             if r.get("evaluation", {}).get("contains_match", False))
        
        avg_time = sum(r["response_time"] for r in self.results) / total if total > 0 else 0
        avg_steps = sum(r["plan_steps_count"] for r in self.results) / total if total > 0 else 0
        
        logger.info(f"Total Tests: {total}")
        logger.info(f"Successful Responses: {successful} ({successful/total*100:.1f}%)")
        logger.info(f"Exact Matches: {exact_matches} ({exact_matches/total*100:.1f}%)")
        logger.info(f"Contains Matches: {contains_matches} ({contains_matches/total*100:.1f}%)")
        logger.info(f"Average Response Time: {avg_time:.2f}s")
        logger.info(f"Average Plan Steps: {avg_steps:.1f}")
        logger.info(f"{'='*80}\n")
    
    def save_results(self, output_file: str = "gaia_test_results.json"):
        """Save detailed results to JSON file"""
        output_path = Path(output_file)
        
        summary = {
            "total_tests": len(self.results),
            "successful": sum(1 for r in self.results if r["success"]),
            "exact_matches": sum(1 for r in self.results 
                               if r.get("evaluation", {}).get("exact_match", False)),
            "contains_matches": sum(1 for r in self.results 
                                  if r.get("evaluation", {}).get("contains_match", False)),
            "avg_response_time": sum(r["response_time"] for r in self.results) / len(self.results) if self.results else 0,
            "results": self.results
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Test MiroFlow Agent using GAIA benchmark questions"
    )
    parser.add_argument(
        "--num-tests",
        type=int,
        default=5,
        help="Number of test cases to run (default: 5)"
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="Filter by difficulty level (1=easy, 2=medium, 3=hard)"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8000",
        help="MiroFlow Agent API URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--data-path",
        type=str,
        default="./data/gaia-2023-validation/standardized_data.jsonl",
        help="Path to GAIA benchmark data file"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for each test (default: 300)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility"
    )
    parser.add_argument(
        "--include-file-tests",
        action="store_true",
        help="Include test cases that require file attachments"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="gaia_test_results.json",
        help="Output file for results (default: gaia_test_results.json)"
    )
    
    args = parser.parse_args()
    
    # Create tester
    tester = GAIABenchmarkTester(
        api_url=args.api_url,
        data_path=args.data_path,
        timeout=args.timeout
    )
    
    # Run tests
    asyncio.run(tester.run_tests(
        num_tests=args.num_tests,
        level=args.level,
        text_only=not args.include_file_tests,
        seed=args.seed
    ))


if __name__ == "__main__":
    main()
