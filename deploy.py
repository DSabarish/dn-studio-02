import subprocess
import datetime
import sys

IMAGE = "sabs1010/dn-studio:v6"
PROJECT = "dn-studio-01"
SERVICE = "dn-studio"
REGION = "us-central1"


def log(message):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def run_command(cmd, shell=True):
    log(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=shell)
    if result.returncode != 0:
        log(f"FAILED: {cmd}")
        sys.exit(result.returncode)
    log("Done\n")

#####################################################################################################
#####################################################################################################
#####################################################################################################


def build_and_push():
    log("Building Docker image...")
    run_command(f"docker build -t {IMAGE} .")

    log("Pushing Docker image...")
    run_command(f"docker push {IMAGE}")


def pull_image():
    log("Pulling Docker image (verification)...")
    run_command(f"docker pull {IMAGE}")


def run_local():
    log("Running container locally...")

    cmd = (
        "docker run --rm -p 8501:8501 "
        "-e GOOGLE_APPLICATION_CREDENTIALS=/app/adc.json "
        "-e GOOGLE_CLOUD_PROJECT=dn-studio-01 "
        "-e GCLOUD_PROJECT=dn-studio-01 "
        "-v \"%APPDATA%\\gcloud\\application_default_credentials.json:/app/adc.json:ro\" "
        f"{IMAGE}"
    )

    run_command(cmd)


def deploy_cloud_run():
    log("Deploying to Cloud Run...")

    cmd = (
        f"gcloud run deploy {SERVICE} "
        f"--image docker.io/{IMAGE} "
        f"--region {REGION} "
        f"--project {PROJECT} "
        "--allow-unauthenticated "
        "--service-account sa-dn-studio@dn-studio-01.iam.gserviceaccount.com "
        "--port 8501 "
        "--cpu=2 "
        "--memory=8Gi "
        "--max-instances=2 "
        "--min-instances=1 "
        "--timeout=3600 "
        "--concurrency=20 "
        "--set-env-vars GOOGLE_CLOUD_PROJECT=dn-studio-01,GCLOUD_PROJECT=dn-studio-01 "
        "--set-secrets HF_TOKEN=HFT:latest"
    )

    run_command(cmd)


if __name__ == "__main__":
    log("Starting deployment pipeline...\n")

    build_and_push()
    pull_image()
    # run_local()
    deploy_cloud_run()

    log("Deployment complete!")