#!/usr/bin/env python3
"""Quick validation of GAIA data and test setup"""

import json
from pathlib import Path

data_path = Path("data/gaia-2023-validation/standardized_data.jsonl")

if not data_path.exists():
    print(f"❌ Data file not found: {data_path}")
    exit(1)

print("✓ Data file found")

# Load data
data = []
with open(data_path, 'r', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            data.append(json.loads(line))

print(f"✓ Total test cases: {len(data)}")

# Count by level
level_counts = {}
for item in data:
    level = item.get("metadata", {}).get("Level", "Unknown")
    level_counts[level] = level_counts.get(level, 0) + 1

print(f"  Level distribution: {dict(sorted(level_counts.items()))}")

# Count text-only
text_only = sum(1 for item in data if not item.get("file_name"))
print(f"  Text-only cases: {text_only}")

# Show a sample question
sample = data[0]
print(f"\n✓ Sample question (Level {sample.get('metadata', {}).get('Level')}):")
print(f"  {sample['task_question'][:150]}...")
print(f"  Answer: {sample['ground_truth']}")

print("\n✓ All checks passed! Ready to run tests.")
