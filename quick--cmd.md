```
# -------------------------
# 1. GCP AUTH (LOCAL)
# -------------------------
gcloud config get-value project
gcloud auth application-default login
gcloud auth application-default print-access-token

# -------------------------
# 2. LOCAL APP RUN
# -------------------------
uv venv
.\.venv\Scripts\Activate.ps1
uv pip install -r requirements.txt
streamlit run app.py

# Note: In UI, "GCS URIs (optional...)" accepts gs:// media and transcript .json paths.

# -------------------------
# 3. DOCKER BUILD, LOGIN, PUSH, PULL
# -------------------------
docker login
docker build -t sabs1010/dn-studio:v5 .
docker push sabs1010/dn-studio:v5
docker pull sabs1010/dn-studio:v5


# -------------------------
# 4. RUN CONTAINER LOCALLY (WITH ADC)
# -------------------------
docker run --rm -p 8501:8501 `
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/adc.json `
  -v "$env:APPDATA\gcloud\application_default_credentials.json:/app/adc.json:ro" `
  sabs1010/dn-studio:v5

# -------------------------
# 5. SERVICE ACCOUNT BINDING
# -------------------------

gcloud iam service-accounts add-iam-policy-binding `
  sa-dn-studio@dn-studio-01.iam.gserviceaccount.com `
  --member="user:sabsdrive02@gmail.com" `
  --role="roles/iam.serviceAccountUser"

# -------------------------
# 6. DEPLOY TO CLOUD RUN
# -------------------------

gcloud run deploy dn-studio `
  --image docker.io/sabs1010/dn-studio:v5 `
  --region us-central1 `
  --project dn-studio-01 `
  --allow-unauthenticated `
  --service-account sa-dn-studio@dn-studio-01.iam.gserviceaccount.com `
  --port 8501 `
  --cpu=2 `
  --memory=4Gi `
  --timeout=3600 `
  --concurrency=2 `
  --set-env-vars "GOOGLE_CLOUD_PROJECT=dn-studio-01" `
  --set-secrets "HF_TOKEN=HFT:latest"

# -------------------------
# 7. Check Config
# -------------------------
gcloud run services describe dn-studio --region us-central1

```  