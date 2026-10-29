import os
from dotenv import load_dotenv
load_dotenv()
import requests
import time

def ask_search_net(query, retries=3):
    url = "https://ydc-index.io/v1/search"
    api_key = os.getenv("YOU_API_KEY", "")


    payload = {
        "query": query,
        "extraction": {
            "extraction_mode": "highlights",
            "highlights": {
                "total_max_tokens": 1024
            }
        },
        "safesearch": "off",
        "count": 2
    }


    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    for i in range(retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            # YDC structure usually returns 'hits' or similar
            return response.json()
        except Exception as e:
            if i < retries - 1:
                time.sleep(2)
                continue
            raise e

if __name__ == '__main__':
    print(ask_search_net("How to make a boat in valheim"))
