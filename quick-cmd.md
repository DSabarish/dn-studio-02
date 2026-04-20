```
# -------------------------
# 1. GCP AUTH (LOCAL)
# -------------------------
gcloud config get-value project
gcloud auth application-default login
gcloud auth application-default print-access-token

# -------------------------
# 2. BUILD IMAGE
# -------------------------
docker build -t sabs1010/dn-studio:v4 .

# -------------------------
# 3. LOGIN & PUSH
# -------------------------
docker login
docker push sabs1010/dn-studio:v4

# -------------------------
# 4. RUN LOCALLY (WITH ADC)
# -------------------------
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/adc.json `
  -v "$env:APPDATA\gcloud\application_default_credentials.json:/app/adc.json:ro" `
  sabs1010/dn-studio:v4


# -------------------------
# 5. Service Account
# -------------------------

gcloud iam service-accounts add-iam-policy-binding `
  sa-dn-studio@dn-studio-01.iam.gserviceaccount.com `
  --member="user:sabsdrive02@gmail.com" `
  --role="roles/iam.serviceAccountUser"

# -------------------------
# 6. DEPLOY TO CLOUD RUN
# -------------------------

gcloud run deploy dn-studio `
  --image docker.io/sabs1010/dn-studio:v4 `
  --region us-central1 `
  --allow-unauthenticated `
  --service-account sa-dn-studio@dn-studio-01.iam.gserviceaccount.com `
  --port 8501

# -------------------------
# 7. Check Config
# -------------------------
gcloud run services describe dn-studio --region us-central1

```  