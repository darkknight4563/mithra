# Deploying the Gatekeeper backend (no devops experience needed)

This puts your backend on a **stable, free HTTPS URL** using **Render**. Total
time ~10 minutes. You only need a web browser and a GitHub account.

Why Render: it has a genuinely free tier, deploys straight from GitHub, gives you
HTTPS automatically, and **supports WebSockets** (your dashboard's live updates).

> Heads-up about the free tier: after ~15 minutes with no traffic the service
> "sleeps". The next request wakes it and takes ~50 seconds. That's normal for
> free hosting. Upgrade to a paid instance later if you want it always-on.

---

## Part A — Put the code on GitHub

1. Go to <https://github.com/new>.
2. **Repository name:** `gatekeeper-backend`. Leave everything else default. Click
   **Create repository**.
3. On the next page, find the box titled **"…or push an existing repository from
   the command line"** and note the URL (looks like
   `https://github.com/YOUR-NAME/gatekeeper-backend.git`).
4. Open a terminal **in the `backend` folder** and run these three commands,
   replacing the URL with yours:

   ```bash
   git remote add origin https://github.com/YOUR-NAME/gatekeeper-backend.git
   git branch -M main
   git push -u origin main
   ```

5. Refresh the GitHub page — you should now see your files (`Dockerfile`,
   `render.yaml`, `app/`, etc.).

---

## Part B — Deploy on Render

6. Go to <https://render.com> and click **Get Started** / **Sign in**. Choose
   **"Sign in with GitHub"** and authorize Render.
7. In the Render dashboard, click the **New +** button (top right) → **Blueprint**.
8. Click **Connect** next to your `gatekeeper-backend` repository. (If you don't
   see it, click **"Configure account"**, give Render access to the repo, come
   back, and refresh.)
9. Render reads `render.yaml` automatically and shows a service named
   **gatekeeper-backend**. Click **Apply** (or **Create Resources**).
10. Render now builds the Docker image and deploys it. This first build takes a
    few minutes — watch the **Logs** tab until you see
    `Application startup complete` and the status turns **Live**.

---

## Part C — Confirm the boot logs

There is no CORS to configure. The dashboard is bundled into this service and
served same-origin from `/`, so there is no second frontend to point anywhere.

11. In Render, open your service → **Logs**. On the first deploy, confirm all
    three of these — don't infer them from the app merely responding:

    ```
    INFO:     Started server process [<pid>]
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://0.0.0.0:10000 (Press CTRL+C to quit)
    ```

    * `0.0.0.0` — bound on all interfaces, as Render requires.
    * a port that is **not 8000** (Render's injected `$PORT`, 10000 by default).
      Seeing `8000` means `${PORT}` did not expand and Render is only reaching
      you via port detection.
    * exactly **one** `Started server process` and **no** `Started parent
      process` line. Uvicorn never prints its worker count; a parent-process
      line plus two server-process lines is what `--workers 2` looks like. Two
      workers would mean two control loops fighting over one fleet.

---

## Part D — Find your public URL and verify it

12. At the **top of your Render service page** you'll see the public URL, like:

    ```
    https://gatekeeper-backend.onrender.com
    ```

    (Your exact subdomain may differ if the name was taken — use whatever Render
    shows.)

13. Confirm it works: open `https://gatekeeper-backend.onrender.com/health` in your
    browser. You should see `{"status":"ok"}` (on a Free instance allow ~1 minute
    on the first hit while it wakes up).

14. Run the full verification against the deployed URL — not localhost:

    ```bash
    python scripts/verify_live.py https://gatekeeper-backend.onrender.com
    ```

    It takes ~2.5 minutes (the scripted demo alone is ~82s) and checks the
    dashboard, the WebSocket, the whole demo sequence, the failsafe, the ROI API,
    and that only one controller owns the fleet.

---

## Updating later

Any time you `git push` to the `main` branch, Render automatically rebuilds and
redeploys. Nothing else to do.
