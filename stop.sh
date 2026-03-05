#!/bin/bash
set -e

echo "Stopping all ShopFlow services..."
docker compose down --remove-orphans
echo "All services stopped."
