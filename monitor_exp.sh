#!/bin/bash
cd /E/Kaggle/Solar-Filament-Segmentation-Challenge-2026
for i in {1..360}; do
  if [ -f ".kaggle_work/output/exp_0/metrics.json" ]; then
    echo "Metrics found at iteration $i"
    exit 0
  fi
  echo "Waiting... ($i/360 minutes)"
  sleep 60
done
echo "Timeout reached"
