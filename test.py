import requests
# FreeSerp Main (default): newest AI startups with LLM summaries
r = requests.get('https://freeserp.ai/api.php', params={
    'q': 'ai chatbot', 'ai_startups': 1, 'sort': 'went_live', 'order': 'desc', 'size': 5,
})
for hit in r.json()['results']:
    print(hit['domain'], '—', hit['ai_summary'][:120])

# FreeSerp Global: any page on the web
r = requests.get('https://freeserp.ai/api.php', params={'index': 'web', 'q': 'how to make a boat in valheim', 'lang': 'en', 'size': 5})
for hit in r.json()['results']:
    print(hit['url'], '—', hit['snippet'][:120])