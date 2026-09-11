# Render and Cloudflare R2 deployment

This deployment is intended for a free-tier prototype:

- Render Free Web Service for the FastAPI API
- Render Free Key Value for Redis-compatible rate-limit state
- an external PostgreSQL provider such as Neon
- a private Cloudflare R2 Standard bucket

Free Render services sleep and can restart. Free Key Value has no persistence, so it must not
be treated as a durable AI job queue.

## 1. Create the R2 bucket and credentials

In Cloudflare, open **Storage & databases > R2**, activate R2, and create a private Standard
bucket such as `virtujudge-prod`. Under **Manage R2 API Tokens**, create an account token with
**Object Read & Write** permission scoped only to that bucket.

Record these values in a password manager when the token is created:

- S3 endpoint: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
- bucket name
- Access Key ID
- Secret Access Key (shown only once)

Add this CORS policy to the R2 bucket, replacing the production frontend origin:

```json
[
  {
    "AllowedOrigins": [
      "https://frontend.example.com",
      "http://localhost:3000"
    ],
    "AllowedMethods": ["GET", "PUT", "HEAD"],
    "AllowedHeaders": ["content-type", "if-none-match"],
    "ExposeHeaders": ["etag", "content-length"],
    "MaxAgeSeconds": 3600
  }
]
```

Do not enable public bucket access. VirtuJudge uses short-lived presigned URLs.

Before deployment, put the R2 values in an ignored file based on `.env.example` and run:

```bash
uv run python scripts/smoke-r2.py --env-file .env.r2
```

The check writes one unique object, verifies upload/download and overwrite protection, and
deletes the object. It never creates or deletes a bucket.

## 2. Create PostgreSQL

Create a PostgreSQL database with a provider that does not expire after 30 days. If the database
is Supabase, do not use its direct `db.<project>.supabase.co` URL on Render: that endpoint is
IPv6-only. In Supabase, select **Connect > Session pooler** and copy the port 5432 URL. Preserve
the pooler's generated hostname and its `postgres.<project-ref>` username.

Express the selected connection string in this form:

```dotenv
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST/DATABASE?ssl=require
```

The container applies `alembic upgrade head` before starting the API. If the database is
unreachable or a migration fails, the deployment fails instead of serving against an old schema.

## 3. Deploy the Render Blueprint

In Render, create a new Blueprint from this repository's `render.yaml`. The Blueprint creates
the API and a private, same-region Key Value instance. Render prompts for every variable marked
`sync: false`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | PostgreSQL URL from step 2 |
| `OBJECT_STORAGE_ENDPOINT` | R2 S3 endpoint |
| `OBJECT_STORAGE_BUCKET` | R2 bucket name |
| `OBJECT_STORAGE_ACCESS_KEY` | R2 Access Key ID |
| `OBJECT_STORAGE_SECRET_KEY` | R2 Secret Access Key |
| `OIDC_ISSUER` | Exact issuer from the authentication provider |
| `OIDC_AUDIENCE` | API audience expected in access tokens |
| `OIDC_JWKS_URL` | Provider's JWKS URL |
| `FRONTEND_URL` | Frontend origin, without a trailing slash |
| `CORS_ALLOWED_ORIGINS` | Comma-separated browser origins, without paths |
| `GMAIL_SMTP_USERNAME` | Dedicated Gmail or Google Workspace sender address |
| `GMAIL_SMTP_PASSWORD` | Google app password, not the account password |
| `GMAIL_FROM_ADDRESS` | Verified sender address, normally the SMTP username |

Example CORS value:

```dotenv
CORS_ALLOWED_ORIGINS=https://frontend.example.com,http://localhost:3000
```

Never paste secrets into `render.yaml` or commit a populated env file.

## 4. Verify the deployed service

After the first deployment succeeds:

```bash
curl --fail https://<SERVICE>.onrender.com/health
```

Then send an authenticated request to `/api/v1/me` using a real OIDC access token, and exercise
one upload intent from the deployed frontend. The API health endpoint intentionally checks only
process liveness, so it does not prove PostgreSQL, Redis, authentication, or R2 connectivity.

## Current free-tier boundaries

- The Blueprint enables `MAIL_BACKEND=gmail`, but Render Free blocks SMTP ports 25, 465, and 587.
  Gmail delivery therefore requires upgrading the web service to a paid Render instance. To stay
  on Render Free, implement an HTTPS email provider adapter instead of SMTP.
- The current AI-ML repository does not yet contain a long-running queue consumer. A Render paid
  background worker or another worker host is required after that consumer is implemented.
- Free Render Key Value can lose all data on restart. Use persistent Redis before AI jobs depend
  on it.
