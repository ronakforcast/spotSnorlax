# AWS Spot Instance Blacklist Container

A containerized solution for monitoring AWS Spot Instance interruption rates and automatically blacklisting high-risk instances in CAST.ai.

## Overview

This solution automatically fetches data from AWS Spot Advisor, analyzes interruption rates for instances in specified regions, and blacklists high-risk instances through the CAST.ai API. Running as a Kubernetes CronJob, it provides a fully automated approach to managing spot instance reliability.

## Features

- **Automatic Monitoring**: Regularly fetches the latest AWS Spot Advisor data
- **Configurable Thresholds**: Customize interruption rate thresholds to match your risk tolerance
- **Region and OS Specific**: Target specific AWS regions and operating systems
- **Secure Credentials**: Stores API keys and sensitive data in Kubernetes Secrets
- **Batch Processing**: Processes instance blacklisting in configurable batches
- **Logging and Reporting**: Comprehensive logging of discovered instances and actions taken
- **Dry Run Mode**: Test configuration without making actual changes
- **Non-interactive Operation**: Designed for automated execution in container environments

## Architecture

The solution consists of:

1. **Python Script**: Core logic for fetching Spot Advisor data and blacklisting instances
2. **Docker Container**: Packages the script and dependencies in an isolated environment
3. **Kubernetes CronJob**: Schedules regular execution of the container
4. **Kubernetes Secret**: Securely stores CAST.ai credentials

## Prerequisites

- Docker installed on your development machine
- Access to a Kubernetes cluster
- CAST.ai account with API credentials
- Container registry to store your Docker image (optional)

## File Structure

```
.
├── spot_blacklist.py      # Main Python script
├── Dockerfile             # Container build instructions
├── requirements.txt       # Python dependencies
├── README.md              # This documentation file
└── k8s/                   # Kubernetes manifests
    ├── cronjob.yaml       # CronJob for scheduled execution
    └── secret.yaml        # Secret for secure credential storage
```

## Setup and Deployment

### 1. Build the Docker Image

```bash
# Build the Docker image
docker build -t spot-blacklist:latest .

# Optional: Tag for your registry
docker tag spot-blacklist:latest your-registry/spot-blacklist:latest

# Optional: Push to your registry
docker push your-registry/spot-blacklist:latest
```

### 2. Configure Kubernetes Resources

Edit the `k8s/secret.yaml` file to include your CAST.ai credentials:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: cast-ai-credentials
  namespace: cast-ai
type: Opaque
stringData:
  api-key: "your-cast-ai-api-key-here"
  org-id: "your-cast-ai-org-id-here"
  cluster-id: "your-cast-ai-cluster-id-here"
```

Customize the `k8s/cronjob.yaml` file as needed:

```yaml
# Update the schedule (cron syntax)
schedule: "0 */6 * * *"  # Currently set to run every 6 hours

# Update the container image if using a registry
image: your-registry/spot-blacklist:latest

# Configure environment variables
env:
- name: REGION
  value: "us-east-1"  # Change to your target region
- name: OS
  value: "Linux"      # Change to your target OS
- name: INTERRUPTION_THRESHOLD
  value: "10"         # Change to your desired threshold
```

### 3. Deploy to Kubernetes

```bash
# Create namespace
kubectl create namespace cast-ai

# Apply the secret
kubectl apply -f k8s/secret.yaml

# Apply the cronjob
kubectl apply -f k8s/cronjob.yaml
```

### 4. Verify Deployment

```bash
# Check if the cronjob was created
kubectl get cronjobs -n cast-ai

# Manually trigger a job to test
kubectl create job --from=cronjob/spot-blacklist-cronjob manual-run -n cast-ai

# Check job status
kubectl get jobs -n cast-ai

# Get the pod name
POD_NAME=$(kubectl get pods -n cast-ai -l job-name=manual-run -o jsonpath='{.items[0].metadata.name}')

# View logs
kubectl logs -n cast-ai $POD_NAME
```

## Configuration Options

### Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| REGION | AWS region (e.g., us-east-1) | Yes | - |
| OS | Operating system (e.g., Linux, Windows) | Yes | - |
| INTERRUPTION_THRESHOLD | Interruption percentage threshold | Yes | - |
| API_KEY | CAST.ai API key | Yes | - |
| ORG_ID | CAST.ai organization ID | Yes | - |
| CLUSTER_ID | CAST.ai cluster ID | Yes | - |
| BLACKLIST_HOURS | Hours to blacklist instances for | No | 5 |
| DRY_RUN | Set to "true" for dry run mode | No | false |
| BATCH_SIZE | Number of instances in a batch | No | 10 |
| BATCH_PAUSE | Seconds between batches | No | 5 |
| AUTO_APPROVE | Set to "true" to skip confirmation | No | false |

### Command-line Arguments

All environment variables can also be passed as command-line arguments when running the container directly:

```bash
docker run spot-blacklist:latest \
  --region us-east-1 \
  --os Linux \
  --interruption-threshold 10 \
  --api-key your-api-key \
  --org-id your-org-id \
  --cluster-id your-cluster-id \
  --blacklist-hours 5 \
  --auto-approve
```

## Testing

### Dry Run Mode

To test the script without making actual changes:

```bash
docker run spot-blacklist:latest \
  --region us-east-1 \
  --os Linux \
  --interruption-threshold 10 \
  --api-key your-api-key \
  --org-id your-org-id \
  --cluster-id your-cluster-id \
  --dry-run
```

### Local Testing

You can test locally using Docker:

```bash
docker run -it --rm \
  -e REGION=us-east-1 \
  -e OS=Linux \
  -e INTERRUPTION_THRESHOLD=10 \
  -e API_KEY=your-api-key \
  -e ORG_ID=your-org-id \
  -e CLUSTER_ID=your-cluster-id \
  spot-blacklist:latest
```

## Maintenance

### Updating the Script

1. Modify the `spot_blacklist.py` file
2. Rebuild the Docker image
3. Push to your registry (if using one)
4. Either:
   - Update the CronJob with the new image tag: `kubectl set image cronjob/spot-blacklist-cronjob spot-blacklist=your-registry/spot-blacklist:new-tag -n cast-ai`
   - Or reapply the updated manifest: `kubectl apply -f k8s/cronjob.yaml`

### Viewing Logs

For historical runs:

```bash
# Get a list of completed jobs
kubectl get pods -n cast-ai --selector=job-name

# View logs for a specific pod
kubectl logs -n cast-ai pod-name
```

## Troubleshooting

### Common Issues

1. **API Authentication Failures**:
   - Verify your API credentials in the Secret
   - Ensure the Secret is in the same namespace as the CronJob

2. **Job Not Running on Schedule**:
   - Check the CronJob is active: `kubectl get cronjobs -n cast-ai`
   - Review the schedule format

3. **No Instances Found**:
   - Verify the region and OS settings
   - Consider lowering the interruption threshold

### Debugging

```bash
# Enable dry run mode to test without making changes
kubectl set env cronjob/spot-blacklist-cronjob DRY_RUN=true -n cast-ai

# Run with lower threshold to verify data fetching
kubectl set env cronjob/spot-blacklist-cronjob INTERRUPTION_THRESHOLD=5 -n cast-ai

# Check pod status for termination reasons
kubectl describe pod pod-name -n cast-ai
```

## How It Works

1. **Data Fetching**: The script fetches the latest AWS Spot Advisor data, which includes interruption rates for all instance types across regions.

2. **Filtering**: It filters instances based on:
   - The specified AWS region
   - The specified OS (e.g., Linux, Windows)
   - Interruption rates above the configured threshold

3. **Blacklisting**: For each high-risk instance type, the script calls the CAST.ai API to add it to the blacklist with the specified expiration time.

4. **Reporting**: The script logs detailed information about the instances it processed and the actions taken.

## Security Considerations

- The container runs as a non-root user
- API credentials are stored in Kubernetes Secrets
- The script follows the principle of least privilege
- No data is persisted between runs (stateless operation)

## License

[Insert your license information here]

## Support

[Insert support contact information here]