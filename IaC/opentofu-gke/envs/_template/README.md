# GKE stack template

Provisions a Google Kubernetes Engine (GKE) cluster with:

- Custom-mode VPC + regional subnet (with secondary ranges for pods & services — VPC-native)
- Regional or zonal GKE cluster (no default node pool)
- Primary node pool with optional autoscaling
- Extra node pools (map) for specialised workloads
- Optional private nodes with Cloud NAT
- Workload Identity enabled (`<project>.svc.id.goog`)

## GKE service account access

The stack sets GKE nodes to use `node_service_account_email`. If that field is
empty, OpenSible uses the `client_email` from the GCP service account JSON key.

By default, OpenSible manages this binding for you with
`google_service_account_iam_member.node_act_as`. OpenTofu runs as the service
account in the JSON key, not as your Cloud Shell or browser user. Grant the
required roles to the JSON key's `client_email` service account.

For auto-managed binding, the JSON key service account needs permission to read
and update IAM policy on the selected node service account, commonly via
`roles/iam.serviceAccountAdmin` or an organization-managed equivalent:

```bash
gcloud projects add-iam-policy-binding GCP_PROJECT_ID \
  --member="serviceAccount:PROVISIONING_SERVICE_ACCOUNT_EMAIL" \
  --role="roles/iam.serviceAccountAdmin"
```

If your organization manages service account IAM separately, set
`manage_node_service_account_act_as_binding = false` and grant the actAs binding
manually.

The provisioning service account from the JSON key must be allowed to act as the
node service account. If managing the binding manually, grant
`roles/iam.serviceAccountUser` to the service account member, not only to your
human Google user:

```bash
gcloud iam service-accounts add-iam-policy-binding NODE_SERVICE_ACCOUNT_EMAIL \
  --project=GCP_PROJECT_ID \
  --member="serviceAccount:PROVISIONING_SERVICE_ACCOUNT_EMAIL" \
  --role="roles/iam.serviceAccountUser"
```

If you leave `node_service_account_email` empty, replace both service account
email values with the JSON key's `client_email`.

Managed by the OpenSible **Google Kubernetes Engine (GKE)** wizard —
edit through the UI, not by hand.
