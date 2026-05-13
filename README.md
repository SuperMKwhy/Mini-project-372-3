# mp-etl — Bay KPI ETL (Azure Function)

Reads flow sensor data from Azure Blob Storage and failure history from Azure SQL, then upserts a `BAY_KPI` table every minute (or every 5 seconds in test mode).

## How it runs — no manual trigger needed

This is a **Timer Trigger** function. Once deployed (or started locally), it fires automatically on schedule. You do not call or trigger it yourself.

- **Test mode:** every 5 seconds (`*/5 * * * * *`)
- **Production:** every 1 minute (`0 * * * * *`) — change the `schedule` in `function_app.py` line 135 before deploying

It also runs once immediately on startup (`run_on_startup=True`).

---

## What it writes to SQL

Table: `BAY_KPI` (created automatically on first run)

| kpi_metric | diesel_1 | diesel_2 | diesel_3 | diesel_4 | gasohol95_5 | gasohol95_6 | updated_at |
|---|---|---|---|---|---|---|---|
| Performance_Flow_Pct | % | % | % | % | % | % | datetime |
| Failure_Rate_Min_Per_Hour | rate | rate | rate | rate | rate | rate | datetime |

- **Performance_Flow_Pct** — average actual flow rate as % of ideal (220 L/min). `0.0` means no truck filling at that bay.
- **Failure_Rate_Min_Per_Hour** — historical failures per hour (`count / total_hours_since_first_record`), e.g. `0.000107`.

The table always has exactly 2 rows — each run overwrites the values in place.

---

## Configuration

Fill in `local.settings.json` (local) or import `app_settings.json` (Azure):

| Key | Description |
|---|---|
| `SqlServer` | Azure SQL server hostname |
| `SqlDatabase` | Database name |
| `SqlUser` / `SqlPassword` | SQL credentials |
| `BlobConnectionString` | Storage account connection string (see below) |
| `BlobContainerName` | Container where JSON blobs are stored |
| `BlobPrefix` | Optional folder prefix (leave empty to scan whole container) |

**Where to get `BlobConnectionString`:**
Azure Portal → Storage Account → Security + networking → **Access keys** → copy the full **Connection string** (starts with `DefaultEndpointsProtocol=https;...`).

---

## Run locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start the function host
func start
```

Requires [Azure Functions Core Tools](https://learn.microsoft.com/azure/azure-functions/functions-run-local) and a filled-in `local.settings.json`.

---

## Deploy to Azure

Push to `main` branch — the GitHub Actions workflow (`.github/workflows/main_mp-etl.yml`) builds and deploys automatically.

To set environment variables on the deployed app:
- **Portal:** Function App → Configuration → Advanced edit → paste contents of `app_settings.json`
- **CLI:** `az functionapp config appsettings set --name mp-etl --resource-group <rg> --settings @app_settings.json`

---

## Source data

Blob JSON format (newline-delimited, one record per line):
```json
{"tag_name": "Channel3...PLC_PRG.FT_100_1", "tag_value": 185.4, "received_at": "2026-05-14T00:42:08Z"}
```

Tag → bay mapping:
| Tag | Bay |
|---|---|
| FT_100_1 | Diesel-1 |
| FT_100_2 | Diesel-2 |
| FT_100_3 | Diesel-3 |
| FT_100_4 | Diesel-4 |
| FT_200_1 | Gasohol95-5 |
| FT_200_2 | Gasohol95-6 |
