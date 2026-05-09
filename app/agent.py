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
        self.model = "llama-3.3-70b-versatile"
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
                "temperature": 0.3,
                "top_p": 1,
                "max_completion_tokens": 1500 # Keeping under the 8000 TPM Groq Free Tier limit
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
        is_end = state.get("is_goal_achieved", False)
        if "?" in reply:
            return False
        if state.get("missing_slots"):
            return False
        if state.get("has_recommendations") is False and state.get("is_ready_to_recommend"):
            return False
        return is_end

    async def get_reply(self, messages: List[Dict]) -> Dict:
        start_time = time.time()

        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        query = " ".join(user_msgs[-2:]) if user_msgs else ""

        # --- PASS 1: RAG RETRIEVAL WITH NATURAL QUERY EXPANSION ---
        # Let the LLM bridge the semantic gap by inferring assessment terminology naturally
        expansion_prompt = f"""Map the user's hiring need to standard assessment terminology.
Generate 5-8 keywords that would appear in the descriptions of the ideal assessments for this role.
Include assessment types (e.g., personality, cognitive, situational judgment, leadership) and core skills.
Return ONLY a comma-separated list of keywords. Do not explain.
User Need: {query}"""
        
        expansion_call = await self._call_llm([{"role": "user", "content": expansion_prompt}])
        expanded_query = query
        if expansion_call:
            try:
                expanded_query = f"{query}, {expansion_call.choices[0].message.content.strip()}"
            except: pass
            
        raw_retrieved = self.vector_store.search(expanded_query, k=10)
        retrieved_shortlist = raw_retrieved[:10]
        context_str = ""
        for i, product in enumerate(retrieved_shortlist, 1):
            context_str += f"ID: {i}\n"
            for k, v in product.items():
                if k not in ['scraped_at', 'entity_id', 'url', 'link']:
                    context_str += f"{k.capitalize()}: {v}\n"
            context_str += "\n"

        # --- PASS 2: STATE AUDIT ---
        audit_prompt = f"""You are a session auditor for an SHL Recommender.
Look at the user's request and the retrieved SHL CATALOG below. 
Decide whether the next best step is to clarify a missing detail or to provide a grounded recommendation.

CATALOG:
{context_str if context_str else "No catalog items found."}

Rules:
1. You can ask AT MOST ONE clarification question if critical information is needed to narrow down the catalog.
2. If the user's request spans multiple distinct areas or skills, analyze the catalog. If in general there is no perfect match that covers all of them, you MUST return is_ready_to_recommend: false so you can ask the user which specific area to prioritize.
3. If this is the initial request and critical information or focus is missing, return is_ready_to_recommend: false.
4. Look at the conversation history. If the assistant ALREADY asked a clarification question in the previous turn, and the user just replied, you MUST return is_ready_to_recommend: true. (Whether the user provided the requested info or just asked to see recommendations anyway, we proceed to recommend).
5. If no critical info is missing from the start, return is_ready_to_recommend: true.

Return JSON only:
{{
  "analysis": "Brief step-by-step evaluation of whether a single catalog item covers the entire request, or if critical focus is missing.",
  "is_ready_to_recommend": true/false,
  "missing_slots": ["role", "level", "language", "priority_area"],
  "is_goal_achieved": false
}}
"""
        state = {
            "is_ready_to_recommend": False,
            "missing_slots": ["role", "level", "language"],
            "is_goal_achieved": False,
        }
        audit_call = await self._call_llm(
            [{"role": "system", "content": audit_prompt}, *messages],
            {"type": "json_object"},
        )
        if audit_call:
            try:
                state = json.loads(audit_call.choices[0].message.content)
            except Exception:
                pass

        # Deterministic Override: If user explicitly asks for a comparison, never clarify.
        query_lower = query.lower()
        if any(word in query_lower for word in ["compare", "comparison", "difference", "differentiate", "vs", "versus"]):
            state["is_ready_to_recommend"] = True

        action = "recommend" if state.get("is_ready_to_recommend") else "clarify"
        # Load mermaid guide
        mermaid_guide = ""
        try:
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            guide_path = os.path.join(base_dir, "data", "mermaid_syntax_guide.md")
            with open(guide_path, "r", encoding="utf-8") as f:
                mermaid_guide = f.read()
        except Exception as e:
            print("Failed to load mermaid guide:", e)

        # --- PASS 3: EXPERT GENERATION ---
        gen_prompt = f"""You are an expert SHL Talent Solutions Consultant.
Provide high-value, professional advice based only on the SHL catalog provided below.

CATALOG:
{context_str if context_str else "No shortlist is available for recommendation yet."}

MERMAID SYNTAX REFERENCE:
{mermaid_guide}

Action: {action.upper()}
User Query: {query}
Missing Slots: {state.get("missing_slots", [])}

RULES:
- If ACTION is RECOMMEND: recommend the best shortlist items with concrete rationale tied to the user’s role and business need.
- If exact technology-specific tests are missing, explicitly say there is no exact match and recommend the closest adjacent shortlist items.
- If ACTION is CLARIFY: Ask EXACTLY ONE single, highly focused question. DO NOT ask multiple questions or compound questions in one sentence (e.g., do not ask for role AND level AND language). Choose the single most important missing detail and ask ONLY about that. Keep recommendations empty.
- Never repeat the same clarification question that the assistant already asked in the previous turn.
- If the user asks you to proceed, asks for the best assessment, or otherwise pushes for an answer after a clarification attempt, give a provisional grounded recommendation instead of asking the same question again.
- If the user asks to compare or differentiate items, you MUST structure your response as a Markdown table. Do NOT use bullet points or paragraphs for comparisons.
- If the user explicitly asks for a flowchart or process diagram, you MUST use a valid ```mermaid``` code block (e.g., flowchart TD).
- If you generate a mermaid diagram, ENSURE the syntax is 100% valid. Start the code block exactly with ```mermaid followed by a newline, and end it with ``` on a new line. Quote any node labels that contain special characters or parentheses.
- Do not ask permission like "Would you like to see them?" if you already have enough to recommend useful shortlist items.
- Do not mention generic leadership capabilities unless tied to actual shortlist items.
- Maintain a decisive, professional tone.

CRITICAL OUTPUT FORMATTING:
1. If the user asks for differences or comparisons, you MUST output a fully populated Markdown table STRICTLY inside your `reply` string. Do NOT put the table inside the `recommendations` array. DO NOT skip the table.
2. You MUST leave a blank empty line before the table starts so the Markdown parser can read it.
Example of required table format:

| Feature | Item A | Item B |
|---|---|---|
| Duration | 10 mins | 15 mins |
| Focus | General | Specific |

3. At the absolute end of your `reply` string, ALWAYS ask exactly ONE natural, conversational follow-up question to guide the user (e.g., "Would you like me to find a technical test to pair with this?" or "Shall I compare these two for you?").

Return JSON only:
{{
  "reply": "Your response",
  "recommendations": [
    {{ 
      "name": "Exact assessment name", 
      "rationale": "Expert rationale",
      "matched_keywords": ["MUST extract exactly 1-2 keywords from the user's prompt (like 'Spanish' or 'Java') that triggered this recommendation."]
    }}
  ]
}}
"""
        print(f"\nDEBUG: Action is {action.upper()}")
        # print(f"DEBUG: Gen Prompt:\n{gen_prompt}")
        
        gen_call = await self._call_llm(
            [{"role": "system", "content": gen_prompt}, *messages],
            {"type": "json_object"},
        )

        if not gen_call:
            return {
                "reply": "I'm having trouble accessing the catalog.",
                "recommendations": [],
                "end_of_conversation": False,
            }

        try:
            raw_output = gen_call.choices[0].message.content
            # Write to debug log to avoid terminal Unicode errors
            with open("debug_log.txt", "a", encoding="utf-8") as f:
                f.write(f"\nDEBUG: Action is {action.upper()}\n")
                f.write(f"DEBUG: RAW LLM OUTPUT:\n{raw_output}\n")
                
            result = json.loads(raw_output)
            reply = result.get("reply", "")

            valid_recs = []
            seen = set()
            shortlist_map = {p["name"].lower(): p for p in retrieved_shortlist}

            for rec in result.get("recommendations", []):
                name_lower = rec.get("name", "").lower()
                rationale = rec.get("rationale", "")
                keywords = rec.get("matched_keywords", [])
                if name_lower in shortlist_map and name_lower not in seen:
                    if not rationale or len(rationale.strip()) < 30:
                        continue
                    official = shortlist_map[name_lower]
                    valid_recs.append({
                        "name": official["name"],
                        "url": official.get("url", "#"),
                        "duration": official.get("duration_raw", official.get("duration", "N/A")).replace(
                            "Approximate Completion Time in minutes = ", ""
                        ),
                        "test_type": self._compute_display_types(official.get("categories", [])),
                        "rationale": rationale,
                        "matched_keywords": keywords,
                    })
                    seen.add(name_lower)

            if valid_recs:
                table = "\n| Name | Type | Duration | Rationale |\n| :--- | :--- | :--- | :--- |\n"
                for rec in valid_recs:
                    table += f"| {rec['name']} | {rec['test_type']} | {rec['duration']} | {rec['rationale']} |\n"
                reply = self._strip_tables(reply) + "\n\n" + table.strip()
            else:
                reply = self._strip_tables(reply)

            state["has_recommendations"] = len(valid_recs) > 0
            is_end = self._validate_session_end(state, reply)

            latency = time.time() - start_time
            print(f"[RELIABILITY] Action: {action} | Latency: {latency:.2f}s | Valid Recs: {len(valid_recs)}")

            return {
                "reply": reply,
                "recommendations": valid_recs[:10],
                "end_of_conversation": is_end,
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
