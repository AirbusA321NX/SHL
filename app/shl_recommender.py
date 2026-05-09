# CACHE BUSTER - FORCE REBUILD - VERSION 1.2
from groq import AsyncGroq
import os
import time
from typing import List, Dict
from app.vector_store import get_vector_store
import json
import re
from dotenv import load_dotenv

load_dotenv()

class SHLAgent:
    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")
        self.model = "openai/gpt-oss-20b"
        print(f"DEBUG: Initializing SHLAgent with API Key: {'Set' if self.api_key else 'Missing'}")
        self.client = AsyncGroq(api_key=self.api_key)
        self.vector_store = get_vector_store()

    async def _call_llm(self, messages, response_format=None):
        if not self.client:
            raise Exception("AsyncGroq client not initialized. Is the groq package installed?")
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.6, # Slightly higher for better flow
                "top_p": 1,
                "max_completion_tokens": 4096
            }
            if response_format:
                kwargs["response_format"] = response_format
                
            response = await self.client.chat.completions.create(**kwargs)
            return response
        except Exception as e:
            print(f"[API ERROR] {e}")
            return None

    def _compute_display_types(self, categories: List[str]) -> str:
        shorthand = []
        cats = [c.lower() for c in categories]
        mapping = {
            "personality": "P",
            "behavior": "B",
            "ability": "A",
            "aptitude": "A",
            "skill": "S",
            "knowledge": "S",
            "cognitive": "C",
            "video": "V",
            "interview": "V",
        }
        for key, val in mapping.items():
            if any(key in c for c in cats) and val not in shorthand:
                shorthand.append(val)
        return ", ".join(shorthand) if shorthand else "K"

    def _strip_tables(self, text: str) -> str:
        return re.sub(r"\|.*\|(\n\|.*\|)*", "", text or "", flags=re.MULTILINE).strip()

    def _validate_session_end(self, state: dict, reply: str) -> bool:
        # Simple heuristic for now
        if "?" in reply:
            return False
        return state.get("is_goal_achieved", False)

    async def get_reply(self, messages: List[Dict]) -> Dict:
        start_time = time.time()

        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        query = user_msgs[-1] if user_msgs else ""

        # Retrieve catalog context
        raw_retrieved = self.vector_store.search(query, k=10)
        context_str = ""
        for i, product in enumerate(raw_retrieved, 1):
            context_str += f"ID: {i} | Name: {product.get('name')} | Type: {product.get('categories')} | Desc: {product.get('description')}\n"

        # Load mermaid guide
        mermaid_guide = ""
        try:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            guide_path = os.path.join(base_dir, "data", "mermaid_syntax_guide.md")
            with open(guide_path, "r", encoding="utf-8") as f:
                mermaid_guide = f.read()
        except: pass

        # SINGLE CONSOLIDATED PROMPT
        system_prompt = f"""You are an expert SHL Talent Solutions Consultant.
Provide professional, grounded advice based ONLY on the SHL CATALOG below.

CATALOG:
{context_str if context_str else "No catalog items found."}

MERMAID SYNTAX REFERENCE:
{mermaid_guide}

RULES:
1. ANALYZE: First, check if the user request is specific enough to recommend a product.
2. RECOMMEND: If ready, recommend the best matches with clear rationale.
3. CLARIFY: If too vague, ask EXACTLY ONE focused question. Do not compound questions.
4. COMPARE: If the user asks for differences or "vs", use a Markdown table.
5. FLOWCHART: If requested, use a valid ```mermaid``` block. Quote node labels with parentheses.
6. KEYWORDS: Extract 1-2 specific keywords from the user's prompt that triggered each recommendation.
7. Tone: Decisive and professional.

Return JSON only:
{{
  "reply": "Your main response text (including tables if needed)",
  "is_goal_achieved": true/false,
  "recommendations": [
    {{ 
      "name": "Assessment Name from Catalog", 
      "rationale": "Why this fits",
      "matched_keywords": ["keyword1"]
    }}
  ]
}}
"""
        
        gen_call = await self._call_llm(
            [{"role": "system", "content": system_prompt}, *messages],
            {"type": "json_object"},
        )

        if not gen_call:
            return {
                "reply": "I'm having trouble accessing the catalog due to service limits. Please try again in a moment.",
                "recommendations": [],
                "end_of_conversation": False,
            }

        try:
            raw_output = gen_call.choices[0].message.content
            result = json.loads(raw_output)
            reply = result.get("reply", "")

            valid_recs = []
            seen = set()
            shortlist_map = {p["name"].lower(): p for p in raw_retrieved}

            for rec in result.get("recommendations", []):
                name_lower = rec.get("name", "").lower()
                if name_lower in shortlist_map and name_lower not in seen:
                    official = shortlist_map[name_lower]
                    valid_recs.append({
                        "name": official["name"],
                        "url": official.get("url", "#"),
                        "duration": official.get("duration_raw", "N/A"),
                        "test_type": self._compute_display_types(official.get("categories", [])),
                        "rationale": rec.get("rationale", ""),
                        "matched_keywords": rec.get("matched_keywords", []),
                    })
                    seen.add(name_lower)

            if valid_recs:
                table = "\n| Name | Type | Duration | Rationale |\n| :--- | :--- | :--- | :--- |\n"
                for rec in valid_recs:
                    table += f"| {rec['name']} | {rec['test_type']} | {rec['duration']} | {rec['rationale']} |\n"
                reply = self._strip_tables(reply) + "\n\n" + table.strip()
            
            latency = time.time() - start_time
            print(f"[RELIABILITY] Model: {self.model} | Latency: {latency:.2f}s | Recs: {len(valid_recs)}")

            return {
                "reply": reply,
                "recommendations": valid_recs[:10],
                "end_of_conversation": result.get("is_goal_achieved", False),
            }
        except Exception as e:
            print(f"[INTERNAL ERROR] {e}")
            return {
                "reply": "I encountered an error processing your request.",
                "recommendations": [],
                "end_of_conversation": False,
            }

    async def generate_title(self, prompt: str) -> str:
        sys_prompt = "Summarize this request into a concise, professional 3-4 word title for a chat sidebar (e.g., 'Senior Java Engineer', 'Sales Audit'). Return ONLY the title text, nothing else."
        try:
            res = await self._call_llm(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": prompt}
                ]
            )
            if res:
                title = res.choices[0].message.content.strip().replace('"', '')
                return title if len(title) > 0 else "New Chat"
        except Exception:
            pass
        return "New Chat"
