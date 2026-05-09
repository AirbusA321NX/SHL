import json
import os
import httpx
import asyncio
import time
import re
from typing import List, Dict, Set

API_URL = "http://127.0.0.1:8000/chat"
CONVO_DIR = "sample_conversations/GenAI_SampleConversations"
CATALOG_PATH = "data/catalog.json"
REPORT_PATH = "evaluation_report.json"

class AgentEvaluator:
    def __init__(self):
        with open(CATALOG_PATH, "r") as f:
            self.catalog = {p['name'].lower(): p for p in json.load(f)}
        self.results = []

    def validate_schema(self, data: dict) -> List[str]:
        errors = []
        if not isinstance(data.get("reply"), str): errors.append("schema_reply_not_string")
        if not isinstance(data.get("recommendations"), list): errors.append("schema_recs_not_list")
        if not isinstance(data.get("end_of_conversation"), bool): errors.append("schema_end_convo_not_bool")
        
        recs = data.get("recommendations", [])
        if len(recs) > 10: errors.append("schema_too_many_recommendations")
        return errors

    def validate_grounding(self, recs: List[dict]) -> List[str]:
        errors = []
        seen = set()
        for r in recs:
            name = r.get("name", "").lower()
            if not name: errors.append("grounding_missing_name")
            if name in seen: errors.append(f"grounding_duplicate_{name}")
            seen.add(name)
            
            if name not in self.catalog:
                errors.append(f"grounding_hallucination_{name}")
            else:
                official = self.catalog[name]
                if r.get("url") != official.get("url", official.get("link")):
                    errors.append(f"grounding_url_mismatch_{name}")
        return errors

    def validate_behavior(self, turn_idx: int, data: dict, should_recommend: bool, is_last_turn: bool) -> List[str]:
        errors = []
        recs = data.get("recommendations", [])
        reply = data.get("reply", "").lower()
        is_end = data.get("end_of_conversation", False)
        
        # 1. Recommendation Discipline
        if not should_recommend:
            if recs: errors.append("behavior_leaked_recs_on_clarify")
            if "|" in reply: errors.append("behavior_leaked_table_on_clarify")
            if "?" not in reply and not is_last_turn: errors.append("behavior_clarify_missing_question")
        
        # 2. End-State Integrity
        if is_end:
            if not should_recommend and turn_idx < 1: errors.append("behavior_premature_end")
            if "?" in reply: errors.append("behavior_end_with_question")
        elif is_last_turn and should_recommend:
             # This is a soft warning, not a hard error as traces vary
             pass
            
        return errors

    async def run_trace(self, client: httpx.AsyncClient, name: str, turns: List[dict]) -> dict:
        messages = []; latencies = []; all_errors = []
        found_recs = set()
        
        for i, turn in enumerate(turns):
            messages.append({"role": "user", "content": turn["q"]})
            start = time.time()
            try:
                resp = await client.post(API_URL, json={"messages": messages}, timeout=15.0)
                latencies.append(time.time() - start)
                data = resp.json()
                
                # Run Validations
                all_errors.extend(self.validate_schema(data))
                all_errors.extend(self.validate_grounding(data.get("recommendations", [])))
                all_errors.extend(self.validate_behavior(i, data, turn.get("should_recommend", False), i == len(turns)-1))
                
                for r in data.get("recommendations", []):
                    found_recs.add(r["name"].lower())
                messages.append({"role": "assistant", "content": data.get("reply", "")})
            except Exception as e:
                all_errors.append(f"runtime_error_{str(e)}")

        # 1. Aggregate Recall Calculation (Whole Convo)
        all_targets = set().union(*[t.get("targets", set()) for t in turns])
        recalled = sum(1 for t in all_targets if any(t in fr or fr in t for fr in found_recs))
        recall = (recalled / len(all_targets)) if all_targets else 1.0

        # 2. Hardened Probe Verification (Special cases)
        if name == "PROBE_COMPARE":
            # Assert that the reply contains comparative terminology
            reply_text = " ".join([m["content"].lower() for m in messages if m["role"] == "assistant"])
            if not any(w in reply_text for w in ["comparison", "difference", "overlap", "while", "whereas"]):
                all_errors.append("behavior_weak_comparison_content")

        return {
            "file": name,
            "schema_pass": all(not e.startswith("schema") for e in all_errors),
            "grounding_pass": all(not e.startswith("grounding") for e in all_errors),
            "behavior_pass": all(not e.startswith("behavior") for e in all_errors),
            "recall": recall,
            "latency_avg": sum(latencies)/len(latencies) if latencies else 0,
            "errors": list(set(all_errors))
        }

    async def run_synthetic_probes(self, client: httpx.AsyncClient):
        print("Running Synthetic Probes...")
        
        # 1. Turn-Limit Probe
        limit_turns = [{"q": f"Test message {i}", "should_recommend": False} for i in range(8)]
        limit_res = await self.run_trace(client, "PROBE_TURN_LIMIT", limit_turns)
        self.results.append(limit_res)
        
        # 2. Refusal Probe
        refusal_turns = [{"q": "Can you give me legal advice on hiring?", "should_recommend": False}]
        refusal_res = await self.run_trace(client, "PROBE_REFUSAL", refusal_turns)
        self.results.append(refusal_res)

        # 3. Compare Probe
        compare_turns = [{"q": "Compare OPQ and OPQ MQ.", "should_recommend": True}]
        compare_res = await self.run_trace(client, "PROBE_COMPARE", compare_turns)
        self.results.append(compare_res)

    async def run_evaluation(self):
        async with httpx.AsyncClient() as client:
            await self.run_synthetic_probes(client)
            
            convo_files = [f for f in os.listdir(CONVO_DIR) if f.endswith(".md")]
            for filename in convo_files:
                print(f"Testing {filename}...")
                filepath = os.path.join(CONVO_DIR, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Parse turns with smarter 'should_recommend' logic
                turns = []
                lines = content.split('\n')
                current_q = ""; current_targets = set()
                
                # Pre-scan for tables to mark turn types
                blocks = re.split(r'\*\*User\*\*', content)
                for block in blocks[1:]:
                    q_match = re.search(r'> (.*)', block)
                    q = q_match.group(1) if q_match else ""
                    # Check if there's a table in this specific block's assistant response
                    has_table = '|' in block and 'https://' in block
                    
                    targets = set()
                    if has_table:
                        table_matches = re.findall(r'\|\s*\d+\s*\|\s*([^|]+)\|', block)
                        for name in table_matches:
                            name_clean = name.strip().lower()
                            if name_clean not in ["name", ""]: targets.add(name_clean)
                    
                    turns.append({"q": q, "should_recommend": has_table, "targets": targets})
                
                res = await self.run_trace(client, filename, turns)
                self.results.append(res)
        
        # Aggregate
        total = len(self.results)
        report = {
            "summary": {
                "avg_recall": sum(r['recall'] for r in self.results)/total,
                "schema_pass_rate": sum(1 for r in self.results if r['schema_pass'])/total,
                "grounding_pass_rate": sum(1 for r in self.results if r['grounding_pass'])/total,
                "behavior_pass_rate": sum(1 for r in self.results if r['behavior_pass'])/total,
                "avg_latency_ms": sum(r['latency_avg'] for r in self.results)/total * 1000
            },
            "details": self.results
        }
        with open(REPORT_PATH, "w") as f:
            json.dump(report, f, indent=2)
            
        print(f"\n{'='*50}\nFINAL AGGREGATE RESULTS\n{'='*50}")
        for k, v in report["summary"].items():
            print(f"{k:20}: {v:.2f}")
        print(f"{'='*50}\nReport saved to {REPORT_PATH}")

if __name__ == "__main__":
    evaluator = AgentEvaluator()
    asyncio.run(evaluator.run_evaluation())
