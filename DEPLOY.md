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

## Part C — Point it at your Lovable site

11. In Render, open your service → **Environment** (left sidebar).
12. Find **`ALLOWED_ORIGINS`**. Click **Edit** and set it to your published
    Lovable URL plus your local dev URLs, comma-separated, for example:

    ```
    https://your-app.lovable.app,http://localhost:5173,http://localhost:3000
    ```

    Replace `your-app.lovable.app` with your real Lovable domain (open your live
    Lovable site and copy the domain from the browser address bar).
    Click **Save Changes** — Render redeploys automatically.

    > You can skip this if you're in a hurry: the backend already accepts any
    > `*.lovable.app` origin automatically. Setting it just makes it explicit.

---

## Part D — Find your public URL and wire up the frontend

13. At the **top of your Render service page** you'll see the public URL, like:

    ```
    https://gatekeeper-backend.onrender.com
    ```

    (Your exact subdomain may differ if the name was taken — use whatever Render
    shows. This is your **API base URL**.)

14. Confirm it works: open `https://gatekeeper-backend.onrender.com/health` in your
    browser. You should see `{"status":"ok"}` (allow ~50s on the first hit while
    it wakes up).

15. In **Lovable**, set the frontend environment variables (Project Settings →
    Environment Variables, or your `.env`) to your new URLs:

    ```
    VITE_API_BASE_URL=https://gatekeeper-backend.onrender.com
    VITE_WS_URL=wss://gatekeeper-backend.onrender.com/ws
    ```

    Save and let Lovable rebuild. Your dashboard is now talking to the live
    backend — no more localhost or Codespace IDs.

---

## Updating later

Any time you `git push` to the `main` branch, Render automatically rebuilds and
redeploys. Nothing else to do.
