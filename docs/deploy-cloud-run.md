# Deploying to Cloud Run

Demo only. Not for clinical use. This app has **no authentication**: anyone who can reach
it can read every transcript and note. Keep it private, and never deploy it with real
patient data.

**Not run live.** These steps follow the shape of the Dockerfiles and settings in this
repository, but they have not been run against a real Google Cloud project. Expect to
adjust names, regions and IAM.

## Pieces

| Piece | Service |
| --- | --- |
| API (`backend/Dockerfile`) | Cloud Run service |
| Web (`frontend/Dockerfile`) | Cloud Run service |
| Postgres | Cloud SQL for PostgreSQL 17 |
| Audio | A Cloud Storage bucket mounted into the API container at `/data` |
| Keys | Secret Manager, exposed as environment variables |
| Retention | Cloud Run job running `scribe retention run`, triggered by Cloud Scheduler |

Both images listen on `$PORT`, which Cloud Run sets.

## 1. Build and push the images

```sh
PROJECT=my-project REGION=northamerica-northeast1 REPO=scribe-lite
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION
docker build -f backend/Dockerfile -t $REGION-docker.pkg.dev/$PROJECT/$REPO/api .
docker build -t $REGION-docker.pkg.dev/$PROJECT/$REPO/web frontend
docker push $REGION-docker.pkg.dev/$PROJECT/$REPO/api
docker push $REGION-docker.pkg.dev/$PROJECT/$REPO/web
```

## 2. Database

Create a Cloud SQL for PostgreSQL 17 instance and a `scribe` database and user. Cloud Run
connects through the built-in Cloud SQL connection (a Unix socket), so the URL is:

```
DATABASE_URL=postgresql+psycopg://scribe:PASSWORD@/scribe?host=/cloudsql/PROJECT:REGION:INSTANCE
```

Put the whole URL in Secret Manager. The API container runs `alembic upgrade head` on
start; with more than one instance, run migrations once as a Cloud Run job instead.

## 3. Audio storage

The API writes audio under `DATA_DIR` and stores paths relative to it, so a Cloud Storage
volume mount is enough; no code change is needed.

```sh
gcloud storage buckets create gs://$PROJECT-scribe-audio --location=$REGION \
  --uniform-bucket-level-access --public-access-prevention
```

Add a bucket lifecycle rule that deletes objects a little after `RETENTION_DAYS`, as a
backstop in case the retention job stops running.

## 4. Secrets

```sh
printf %s "$ANTHROPIC_API_KEY" | gcloud secrets create anthropic-api-key --data-file=-
printf %s "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
printf %s "$DATABASE_URL" | gcloud secrets create scribe-database-url --data-file=-
```

## 5. API service

Processing runs as a background task after the upload request returns. Cloud Run
throttles CPU between requests by default, which would stall it, so turn throttling off.
The in-process retention schedule is not reliable when instances scale to zero, so turn
it off here and use the job in step 7.

```sh
gcloud run deploy scribe-api \
  --image $REGION-docker.pkg.dev/$PROJECT/$REPO/api --region $REGION \
  --no-allow-unauthenticated --no-cpu-throttling --memory 2Gi \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE \
  --set-secrets ANTHROPIC_API_KEY=anthropic-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,DATABASE_URL=scribe-database-url:latest \
  --set-env-vars RETENTION_SCHEDULE_ENABLED=false,RETENTION_DAYS=30 \
  --add-volume name=audio,type=cloud-storage,bucket=$PROJECT-scribe-audio \
  --add-volume-mount volume=audio,mount-path=/data
```

2 GiB leaves room for the spaCy model (about 300 MB resident) and audio uploads.

## 6. Web service

```sh
gcloud run deploy scribe-web \
  --image $REGION-docker.pkg.dev/$PROJECT/$REPO/web --region $REGION \
  --no-allow-unauthenticated \
  --set-env-vars API_URL=https://scribe-api-HASH-REGION.a.run.app
```

The web server calls the API from the server side, so the web service's identity needs
`roles/run.invoker` on the API, and the requests need an identity token. The proxy in
`frontend/src/app/api/[...path]/route.ts` does not add one yet; that is the first change
a real deployment would need. Putting both services behind Identity-Aware Proxy is the
simplest way to keep the demo private.

## 7. Retention job

```sh
gcloud run jobs create scribe-retention \
  --image $REGION-docker.pkg.dev/$PROJECT/$REPO/api --region $REGION \
  --command scribe --args retention,run \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE \
  --set-secrets DATABASE_URL=scribe-database-url:latest \
  --set-env-vars RETENTION_DAYS=30 \
  --add-volume name=audio,type=cloud-storage,bucket=$PROJECT-scribe-audio \
  --add-volume-mount volume=audio,mount-path=/data

gcloud scheduler jobs create http scribe-retention-daily --location $REGION \
  --schedule "0 3 * * *" \
  --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/scribe-retention:run" \
  --http-method POST --oauth-service-account-email SCHEDULER_SA@$PROJECT.iam.gserviceaccount.com
```

Each run logs what it deleted and writes a row per file to `retention_deletions`.

## What a real deployment would also need

- Authentication and per-user authorisation on every endpoint.
- Business associate agreements (or the local equivalent) with every processor that sees
  patient data, or on-premises speech recognition. Today the audio goes to Gemini
  unredacted.
- Zero-data-retention terms with the model providers.
- A retention policy for transcripts and notes, not only audio.
- Encryption keys you control (CMEK) for the database and bucket, and audit-log export
  to a store the app cannot modify.
