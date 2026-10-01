# Render demo deployment

This configuration is for a **demo with fake data only**. Do not enter patient,
safety-case, or complaint information into this demo. The free Render database
is temporary, and the web service's local file storage is not durable; uploaded
files may be lost when the service restarts.

## Publish the demo

1. Push the branch containing `render.yaml` to GitHub.
2. In Render, create a new **Blueprint** and connect
   `yonaodoi/APDL-PV-AI-Platform`. Select the branch containing the Blueprint.
3. Review the services before applying the Blueprint. It creates a free web
   service and a free PostgreSQL database. Render generates the Flask secret
   key; do not replace it with a value from `.env`.
4. Wait for the deployment to finish. The service runs the numbered SQL
   migrations at startup, then serves the app. The migration runner records
   completed migrations and takes a PostgreSQL advisory lock to prevent
   simultaneous deploys from applying them at once.
5. Create the first administrator from a trusted computer. In Render, copy the
   database's **external** connection string, then run these commands from the
   project folder in PowerShell, substituting that string locally:

   ```powershell
   $env:DATABASE_URL = "<Render external database connection string>"
   python -m flask --app run create-admin
   Remove-Item Env:DATABASE_URL
   ```

   The command prompts for the account details and password. Do not paste the
   connection string or password into chat, source files, or GitHub.
6. Open the web service URL shown in Render and sign in with the administrator
   account you created.

## Demo limitations

- This is not a production deployment or a validated environment for regulated
  data. Real operational use requires an approved hosting arrangement, durable
  storage and backups, access/security controls, and review of applicable data
  residency and compliance requirements.
- AI assistance currently expects Ollama at `127.0.0.1:11434`. That address
  refers to the Render service itself, where Ollama is not installed, so those
  features are unavailable in this deployment.
- The speech transcription model may download and initialize on its first use;
  performance and memory use depend on the free service limits.
- Free Render services may sleep when idle, and free database availability,
  storage, and retention are limited by Render's current plan terms.
