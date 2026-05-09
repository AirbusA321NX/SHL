import requests
import json

url = "http://127.0.0.1:8000/chat"
payload = {
    "messages": [
        {"role": "user", "content": "What's the difference between the DSI and the Safety & Dependability 8.0?"}
    ]
}

print("Sending request to backend...")
response = requests.post(url, json=payload)

if response.status_code == 200:
    data = response.json()
    print("\n--- RAW BACKEND REPLY ---")
    print(repr(data.get("reply", "")))
    print("\n--- RECOMMENDATIONS ---")
    print(json.dumps(data.get("recommendations", []), indent=2))
else:
    print(f"Error: {response.status_code}")
    print(response.text)
