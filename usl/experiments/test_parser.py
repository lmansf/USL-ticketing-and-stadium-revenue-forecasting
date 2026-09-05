
import requests
from bs4 import BeautifulSoup

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
URL = "https://fbref.com/en/comps/73/2025/2025-USL-Championship-Stats"
response = requests.get(URL, headers=headers)
print(response.status_code)



soup = BeautifulSoup(response.text, "html.parser")
with open("test_scrape.html", "w", encoding="utf-8") as f:
    f.write(soup.prettify())