import requests, os
from dotenv import load_dotenv
load_dotenv()

key = os.getenv("JINA_API_KEY")
url = "https://r.jina.ai/https://ph.jobstreet.com/jobs"
r = requests.get(url, headers={
    "Authorization": f"Bearer {key}",
    "Accept": "text/plain",
    "X-Return-Format": "text",
}, timeout=30)
print("Status:", r.status_code)
print(r.text[:1000])