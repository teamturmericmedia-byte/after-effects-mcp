"""Call one MCP tool from the command line: python cli.py <tool> '<json args>'  (images saved to test_out/<tool>.jpg)."""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

name, args = sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
try:
    out = server.TOOLS[name]["fn"](args)
except server.AEError as e:
    print("ERROR:", e)
    sys.exit(1)
for c in out:
    if c["type"] == "text":
        print(c["text"])
    else:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_out", name + ".jpg")
        open(p, "wb").write(base64.b64decode(c["data"]))
        print("image ->", p)
