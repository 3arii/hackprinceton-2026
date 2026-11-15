#!/usr/bin/env bash
# Package and deploy the stack. Needs `aws login --profile hackprinceton` first.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-hackprinceton} AWS_REGION=${AWS_REGION:-us-east-2}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ARTIFACTS="buoy-monitor-artifacts-$ACCOUNT-$AWS_REGION"
aws s3api head-bucket --bucket "$ARTIFACTS" 2>/dev/null || aws s3 mb "s3://$ARTIFACTS" >/dev/null
# The photo checker's layer: numpy and OpenCV for Lambda (Linux arm64) at the versions vision/ locks,
# plus the microplastics package. Reinstalled only when the lock changes.
mkdir -p .build
uv export --project vision --frozen --no-dev --no-emit-project --no-hashes --quiet -o .build/vision.new
if cmp -s .build/vision.new .build/vision.txt; then rm .build/vision.new; else
  rm -rf .build/vision
  uv pip install --quiet --target .build/vision/python --python-version 3.13 \
    --python-platform aarch64-manylinux_2_28 --only-binary :all: -r .build/vision.new
  mv .build/vision.new .build/vision.txt
fi
rsync -a --delete --exclude __pycache__ vision/microplastics/ .build/vision/python/microplastics/
aws cloudformation package --template-file template.yaml --s3-bucket "$ARTIFACTS" --output-template-file .packaged.yaml >/dev/null
aws cloudformation deploy --template-file .packaged.yaml --stack-name buoy-monitor \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --no-fail-on-empty-changeset "$@"
# The cameras' clean-mesh models and rig configs, which the photo checker loads from the bucket
BUCKET=$(aws cloudformation describe-stacks --stack-name buoy-monitor --query "Stacks[0].Outputs[?OutputKey=='Bucket'].OutputValue" --output text)
aws s3 sync vision/models/ "s3://$BUCKET/models/" --exclude "*" --include "*.npz" --include "*.json" --only-show-errors
aws cloudformation describe-stacks --stack-name buoy-monitor --query "Stacks[0].Outputs" --output table
