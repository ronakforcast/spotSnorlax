#!/usr/bin/env python3
import argparse
import json
import requests
import sys
import time


def get_blacklist(api_key, org_id, cluster_id):
    """Fetch all blacklisted instances for a specific cluster."""
    url = f"https://api.cast.ai/v1/inventory/blacklist?organizationId={org_id}&clusterId={cluster_id}"
    headers = {
        "X-API-Key": api_key,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json().get("items", [])
    except requests.exceptions.RequestException as e:
        print(f"Error fetching blacklist: {e}")
        sys.exit(1)


def remove_from_blacklist(api_key, org_id, cluster_id, instance_family, lifecycle):
    """Remove a specific instance from the blacklist."""
    url = "https://api.cast.ai/v1/inventory/blacklist/remove"
    headers = {
        "X-API-Key": api_key,
        "accept": "application/json",
        "content-type": "application/json"
    }
    data = {
        "lifecycle": lifecycle,
        "organizationId": org_id,
        "clusterId": cluster_id,
        "instanceFamily": instance_family
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error removing {instance_family} from blacklist: {e}")
        return False


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Remove all instances from CAST.AI blacklist")
    parser.add_argument("--api-key", required=True, help="CAST.AI API Key")
    parser.add_argument("--org-id", required=True, help="Organization ID")
    parser.add_argument("--cluster-id", required=True, help="Cluster ID")
    args = parser.parse_args()
    
    # Fetch the current blacklist
    print("Fetching current blacklist...")
    blacklisted_instances = get_blacklist(args.api_key, args.org_id, args.cluster_id)
    
    if not blacklisted_instances:
        print("No instances found in the blacklist. Nothing to remove.")
        return
    
    print(f"Found {len(blacklisted_instances)} blacklisted instances:")
    for instance in blacklisted_instances:
        expiry_time = instance.get("expiresAt", "unknown")
        print(f"  - {instance.get('instanceFamily')} (lifecycle: {instance.get('lifecycle')}, expires: {expiry_time})")
    
    # Confirm before proceeding
    confirm = input("\nDo you want to remove all these instances from the blacklist? (y/n): ")
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        return
    
    # Remove each instance from the blacklist
    print("\nRemoving instances from blacklist...")
    success_count = 0
    
    for instance in blacklisted_instances:
        instance_family = instance.get("instanceFamily")
        lifecycle = instance.get("lifecycle")
        
        print(f"Removing {instance_family} ({lifecycle})...", end="")
        if remove_from_blacklist(args.api_key, args.org_id, args.cluster_id, instance_family, lifecycle):
            print(" SUCCESS")
            success_count += 1
        else:
            print(" FAILED")
        
        # Small delay to avoid overwhelming the API
        time.sleep(0.5)
    
    print(f"\nRemoval complete. Successfully removed {success_count} of {len(blacklisted_instances)} instances from the blacklist.")


if __name__ == "__main__":
    main()