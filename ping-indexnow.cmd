@echo off
rem Ping IndexNow after a website deploy (fallback when GitHub Actions is unavailable).
rem Usage: run after pushing to gh-pages. Expect HTTP 202 = accepted.
rem key file: 9346fbd5a68844a9af42e393ee1c4ad8.txt (site root)
curl.exe -sS -X POST "https://api.indexnow.org/indexnow" -H "Content-Type: application/json; charset=utf-8" -d "{\"host\":\"fewtype.pages.dev\",\"key\":\"9346fbd5a68844a9af42e393ee1c4ad8\",\"keyLocation\":\"https://fewtype.pages.dev/9346fbd5a68844a9af42e393ee1c4ad8.txt\",\"urlList\":[\"https://fewtype.pages.dev/\",\"https://fewtype.pages.dev/zh\"]}" -w "\nIndexNow HTTP %%{http_code}\n"
