#!/usr/bin/env bash
# Package and deploy the stack. Needs `aws login --profile hackprinceton` first.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-hackprinceton} AWS_REGION=${AWS_REGION:-us-east-2}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ARTIFACTS="buoy-monitor-artifacts-$ACCOUNT-$AWS_REGION"
aws s3api head-bucket --bucket "$ARTIFACTS" 2>/dev/null || aws s3 mb "s3://$ARTIFACTS" >/dev/null
aws cloudformation package --template-file template.yaml --s3-bucket "$ARTIFACTS" --output-template-file .packaged.yaml >/dev/null
aws cloudformation deploy --template-file .packaged.yaml --stack-name buoy-monitor \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset "$@"
aws cloudformation describe-stacks --stack-name buoy-monitor --query "Stacks[0].Outputs" --output table
