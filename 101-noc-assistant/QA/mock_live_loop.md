# Deterministic mock-loop test

Validates `run_live`'s tool-call plumbing against a scripted local
OpenAI-compatible endpoint: turn 1 returns a `get_active_alerts` tool call,
turn 2 a `search_runbooks` call, turn 3 the final answer; the mock asserts
that the loop fed real tool results (containing `REGISTRATION_STORM`) back
into the conversation. No network, no model variance.

```bash
python3 - <<'EOF'
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, '.')

SCRIPT = [
    {"tool": "get_active_alerts", "args": {"component": "all"}},
    {"tool": "search_runbooks", "args": {"query": "registration storm"}},
]

class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        n = sum(1 for m in body["messages"] if m.get("role") == "tool")
        if n < len(SCRIPT):
            step = SCRIPT[n]
            msg = {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"call_{n}", "type": "function",
                "function": {"name": step["tool"], "arguments": json.dumps(step["args"])}}]}
        else:
            assert any("REGISTRATION_STORM" in m.get("content", "")
                       for m in body["messages"] if m.get("role") == "tool")
            msg = {"role": "assistant", "content": "MOCK-FINAL: grounded answer."}
        resp = {"id": "mock", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "message": msg,
                             "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]}
        out = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

srv = HTTPServer(("127.0.0.1", 8311), Mock)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ.update(LLM_BASE_URL="http://127.0.0.1:8311/v1",
                  LLM_API_KEY="test", LLM_MODEL="mock-model")
from agent.noc_agent import run_live
answer = run_live("What is wrong in the core right now?")
assert "MOCK-FINAL" in answer
print("mock live-loop test: PASS")
EOF
```
