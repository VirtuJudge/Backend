# Render and Cloudflare R2 deployment

This deployment is intended for a free-tier prototype:

- Render Free Web Service for the FastAPI API
- Render Free Key Value for Redis-compatible rate-limit state
- an external PostgreSQL provider such as Neon
- a private Cloudflare R2 Standard bucket
- Resend's HTTPS email API for real transactional mail

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

## 3. Configure real email delivery

Create a free Resend account, add a domain or sending subdomain that you own, and add the SPF and
DKIM records Resend shows to your DNS provider. After the domain is verified, create an API key
with **Sending access**, restricted to that domain, and save the key when it is shown.

Use a sender address on the verified domain, for example:

```dotenv
RESEND_FROM_ADDRESS=VirtuJudge <noreply@mail.example.com>
```

A Gmail address cannot be used as this sender because you do not control the `gmail.com` DNS
records. The application sends through Resend's HTTPS API; it does not use fake mail or blocked
SMTP ports.

## 4. Deploy the Render Blueprint

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
| `RESEND_API_KEY` | Resend sending-access key beginning with `re_` |
| `RESEND_FROM_ADDRESS` | Name and address on the verified domain, such as `VirtuJudge <noreply@mail.example.com>` |

Example CORS value:

```dotenv
CORS_ALLOWED_ORIGINS=https://frontend.example.com,http://localhost:3000
```

Never paste secrets into `render.yaml` or commit a populated env file.

## 5. Verify the deployed service

After the first deployment succeeds:

```bash
curl --fail https://<SERVICE>.onrender.com/health
```

Then send an authenticated request to `/api/v1/me` using a real OIDC access token, exercise one
upload intent from the deployed frontend, and trigger one invitation email to an address you can
inspect. Confirm the delivery in Resend's Emails and Logs pages. The API health endpoint
intentionally checks only process liveness, so it does not prove PostgreSQL, Redis,
authentication, R2 connectivity, or mail delivery.

## Current free-tier boundaries

- The Blueprint enables the real `resend` mail backend, which uses HTTPS and works on Render Free.
  Its delivery volume is bounded by the selected Resend plan.
- The current AI-ML repository does not yet contain a long-running queue consumer. A Render paid
  background worker or another worker host is required after that consumer is implemented.
- Free Render Key Value can lose all data on restart. Use persistent Redis before AI jobs depend
  on it.
