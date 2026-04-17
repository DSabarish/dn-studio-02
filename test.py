"""
prompt = Path(r"run\run_026\debug-prompt-populate.md").read_text(encoding="utf-8")
# client = genai.Client(api_key="AIzaSyD4e5JA57bj7K8i5-30XClnfISvl0NfQDg")
client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
# model_name = "gemini-3.1-pro-preview"
model_name = "gemini-2.5-flash"
response = client.models.generate_content(model=model_name, contents=prompt)
print(response.text)
"""

import json
from pathlib import Path

from google import genai

PROJECT_ID = "dn-studio-01"
LOCATION = "asia-south1"

prompt = Path(r"run\run_026\debug-prompt-populate.md").read_text(encoding="utf-8")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
model_name = "gemini-2.5-flash"
response = client.models.generate_content(model=model_name, contents=prompt)

raw_text = response.text or ""
try:
    parsed = json.loads(raw_text)
except json.JSONDecodeError:
    parsed = {"content": raw_text}

Path("c.json").write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
print("Saved JSON to c.json")