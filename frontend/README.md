# TrueMark web

Next.js (App Router, plain JavaScript and CSS) front end for the TrueMark API.

```bash
npm install
cp .env.example .env.local     # set NEXT_PUBLIC_API_URL to your API
npm run dev                    # http://localhost:3000
```

Deploy on Vercel: import the `frontend` folder, add `NEXT_PUBLIC_API_URL`, deploy. Then set `CORS_ORIGINS` on the
API to your Vercel URL.

Judge flow: open the link, the first sample is already loaded, press "Check this copy", click any marked passage
or finding, use a suggested fix, then download the report.

Free hosts put servers to sleep. The page keeps retrying `/health` and says the server is waking up. To avoid the
wait during judging, ping `<API>/health` every 5 minutes with a free uptime monitor.
