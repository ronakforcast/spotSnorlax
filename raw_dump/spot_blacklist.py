#!/usr/bin/env python3
"""
AWS Spot Instance Blacklist Script

This script fetches AWS Spot Advisor data, filters instances based on region, OS, 
and interruption percentage, then blacklists instances with interruption frequency 
higher than a specified threshold.

Usage:
  python spot_blacklist.py --region REGION --os OS --interruption-threshold THRESHOLD 
                          --api-key API_KEY --org-id ORG_ID --cluster-id CLUSTER_ID
                          [--blacklist-hours HOURS] [--dry-run]

Example:
  python spot_blacklist.py --region us-east-1 --os Linux --interruption-threshold 10 \
                          --api-key your_api_key --org-id your_org_id --cluster-id your_cluster_id
"""

import argparse
import json
import os
import tempfile
import requests
import time
import logging
from datetime import datetime, timedelta


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("spot_blacklist.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("spot_blacklist")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="AWS Spot Instance Blacklist Script")
    parser.add_argument(
        "--region", 
        required=True,
        help="Filter results by AWS region (e.g., us-east-1)"
    )
    parser.add_argument(
        "--os", 
        required=True,
        help="Filter results by operating system (e.g., Linux, Windows)"
    )
    parser.add_argument(
        "--interruption-threshold", 
        type=float,
        required=True,
        help="Blacklist instances with interruption percentage higher than this threshold"
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="CAST.ai API key"
    )
    parser.add_argument(
        "--org-id",
        required=True,
        help="CAST.ai organization ID"
    )
    parser.add_argument(
        "--cluster-id",
        required=True,
        help="CAST.ai cluster ID"
    )
    parser.add_argument(
        "--blacklist-hours",
        type=int,
        default=5,
        help="Number of hours to blacklist instances for (default: 5)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show instances that would be blacklisted without actually blacklisting them"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of instances to blacklist in a batch before pausing"
    )
    parser.add_argument(
        "--batch-pause",
        type=int,
        default=5,
        help="Seconds to pause between batches"
    )
    return parser.parse_args()


def fetch_spot_advisor_data():
    """Fetch the AWS Spot Advisor data from the official source."""
    url = "https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
    
    # Create a cache directory if it doesn't exist
    cache_dir = os.path.join(tempfile.gettempdir(), "aws-spot-advisor-cache")
    os.makedirs(cache_dir, exist_ok=True)
    
    cache_file = os.path.join(cache_dir, "spot-advisor-data.json")
    cache_headers_file = os.path.join(cache_dir, "headers.json")
    
    headers = {}
    if os.path.exists(cache_headers_file):
        try:
            with open(cache_headers_file, 'r') as f:
                headers = json.load(f)
                if 'ETag' in headers:
                    headers = {'If-None-Match': headers['ETag']}
        except (json.JSONDecodeError, IOError):
            headers = {}
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 304:  # Not modified
        logger.info(f"Using cached data from {cache_file}")
        with open(cache_file, 'r') as f:
            return json.load(f)
    
    if response.status_code == 200:  # OK
        # Save the response headers for caching
        with open(cache_headers_file, 'w') as f:
            json.dump(dict(response.headers), f)
        
        # Save the data
        data = response.json()
        with open(cache_file, 'w') as f:
            json.dump(data, f)
        
        return data
    
    response.raise_for_status()


def blacklist_instance(instance_type, api_key, org_id, cluster_id, blacklist_hours):
    """
    Blacklist a specific instance type using the CAST.ai API.
    """
    # Calculate expiry time (current time + specified hours)
    expires_at = (datetime.utcnow() + timedelta(hours=blacklist_hours)).isoformat() + "Z"
    
    url = "https://api.cast.ai/v1/inventory/blacklist/add"
    headers = {
        "X-API-Key": api_key,
        "accept": "application/json",
        "content-type": "application/json"
    }
    payload = {
        "lifecycle": "spot",
        "organizationId": org_id,
        "clusterId": cluster_id,
        "instanceFamily": instance_type,
        "expiresAt": expires_at
    }
    
    logger.info(f"Blacklisting instance type {instance_type} until {expires_at}")
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()  # Raise exception for 4XX/5XX responses
        
        logger.info(f"Successfully blacklisted {instance_type}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"API call failed for {instance_type}: {str(e)}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return False


def main():
    """Main function to fetch data and blacklist high-interruption instances."""
    args = parse_args()
    
    logger.info(f"Starting spot instance blacklist script")
    logger.info(f"Region: {args.region}, OS: {args.os}, Threshold: {args.interruption_threshold}%")
    
    if args.dry_run:
        logger.info("DRY RUN MODE: No instances will actually be blacklisted")
    
    # Fetch Spot Advisor data
    logger.info("Fetching AWS Spot Advisor data...")
    try:
        data = fetch_spot_advisor_data()
    except Exception as e:
        logger.error(f"Failed to fetch Spot Advisor data: {e}")
        return
    
    # Get interruption ranges for reference
    interruption_ranges = {
        int(r["index"]): r for r in data["ranges"]
    }
    
    # Check if region exists
    if args.region not in data["spot_advisor"]:
        logger.error(f"Region {args.region} not found in Spot Advisor data")
        return
    
    # Check if OS exists for the region
    if args.os not in data["spot_advisor"][args.region]:
        logger.error(f"OS {args.os} not found for region {args.region}")
        return
    
    # Find instances to blacklist
    instances_to_blacklist = []
    
    for instance_type, values in data["spot_advisor"][args.region][args.os].items():
        # Get instance details
        instance_info = data["instance_types"].get(instance_type, {})
        
        # Get interruption rate and format it properly
        interruption_index = int(values["r"])
        interruption_info = interruption_ranges.get(interruption_index, {})
        
        # Get the actual interruption rate from the ranges data
        interruption_label = interruption_info.get("label", "Unknown")
        interruption_max = float(interruption_info.get("max", 0))
        
        # Get savings percentage
        savings_percent = values["s"]
        
        # Check if interruption rate exceeds threshold
        if interruption_max > args.interruption_threshold:
            instances_to_blacklist.append({
                "instance_type": instance_type,
                "interruption_rate": interruption_max,
                "interruption_label": interruption_label,
                "savings_percent": savings_percent,
                "vcpus": instance_info.get("cores", "N/A"),
                "memory_gb": instance_info.get("ram_gb", "N/A"),
                "emr_compatible": "Yes" if instance_info.get("emr", False) else "No"
            })
    
    # Sort by interruption rate (highest first)
    instances_to_blacklist.sort(key=lambda x: x["interruption_rate"], reverse=True)
    
    # Summary
    logger.info(f"Found {len(instances_to_blacklist)} instances with interruption rate > {args.interruption_threshold}%")
    
    if not instances_to_blacklist:
        logger.info("No instances to blacklist. Exiting.")
        return
    
    # Print table header
    logger.info("\nInstances to blacklist:")
    logger.info("{:<20} {:<25} {:<15} {:<8} {:<10} {:<15}".format(
        "Instance Type", "Interruption Rate", "Savings", "vCPUs", "Memory GB", "EMR Compatible"
    ))
    logger.info("-" * 100)
    
    # Print instances to blacklist
    for instance in instances_to_blacklist:
        logger.info("{:<20} {:<25} {:<15} {:<8} {:<10} {:<15}".format(
            instance["instance_type"],
            f"{instance['interruption_label']} (max {instance['interruption_rate']}%)",
            f"{instance['savings_percent']}%",
            instance["vcpus"],
            instance["memory_gb"],
            instance["emr_compatible"]
        ))
    
    # Confirm before proceeding if not in dry-run mode
    if not args.dry_run:
        confirm = input(f"\nAre you sure you want to blacklist these {len(instances_to_blacklist)} instances? (y/n): ")
        if confirm.lower() != 'y':
            logger.info("Operation cancelled by user.")
            return
    
    # Blacklist instances
    successful = 0
    failed = 0
    
    for i, instance in enumerate(instances_to_blacklist):
        # Process in batches
        if i > 0 and i % args.batch_size == 0:
            logger.info(f"Pausing for {args.batch_pause} seconds after processing {args.batch_size} instances...")
            time.sleep(args.batch_pause)
        
        instance_type = instance["instance_type"]
        interruption_rate = instance["interruption_rate"]
        
        try:
            if args.dry_run:
                logger.info(f"DRY RUN: Would blacklist {instance_type} (Interruption: {interruption_rate}%)")
                successful += 1
            else:
                result = blacklist_instance(
                    instance_type=instance_type,
                    api_key=args.api_key,
                    org_id=args.org_id,
                    cluster_id=args.cluster_id,
                    blacklist_hours=args.blacklist_hours
                )
                if result:
                    successful += 1
                else:
                    logger.error(f"Failed to blacklist {instance_type}")
                    failed += 1
        except Exception as e:
            logger.error(f"Error blacklisting {instance_type}: {e}")
            failed += 1
    
    # Print summary
    logger.info("\nBlacklisting complete:")
    logger.info(f"Total instances processed: {len(instances_to_blacklist)}")
    logger.info(f"Successfully blacklisted: {successful}")
    
    if failed > 0:
        logger.info(f"Failed to blacklist: {failed}")
    
    logger.info("Done.")


# def main():
    """Main function to fetch data and blacklist high-interruption instances."""
    args = parse_args()
    
    logger.info(f"Starting spot instance blacklist script")
    logger.info(f"Region: {args.region}, OS: {args.os}, Threshold: {args.interruption_threshold}%")
    
    if args.dry_run:
        logger.info("DRY RUN MODE: No instances will actually be blacklisted")
    
    # Fetch Spot Advisor data
    logger.info("Fetching AWS Spot Advisor data...")
    try:
        data = fetch_spot_advisor_data()
    except Exception as e:
        logger.error(f"Failed to fetch Spot Advisor data: {e}")
        return
    
    # Get interruption ranges for reference
    interruption_ranges = {int(r["index"]): r for r in data["ranges"]}
    
    # Check if region exists
    if args.region not in data["spot_advisor"]:
        logger.error(f"Region {args.region} not found in Spot Advisor data")
        return
    
    # Check if OS exists for the region
    if args.os not in data["spot_advisor"][args.region]:
        logger.error(f"OS {args.os} not found for region {args.region}")
        return
    
    # Find instances to blacklist
    instances_to_blacklist = []
    
    for instance_type, values in data["spot_advisor"][args.region][args.os].items():
        interruption_index = int(values["r"])
        interruption_info = interruption_ranges.get(interruption_index, {})
        interruption_max = float(interruption_info.get("max", 0))
        
        if interruption_max > args.interruption_threshold:
            instances_to_blacklist.append(instance_type)
    
    logger.info(f"Found {len(instances_to_blacklist)} instances with interruption rate > {args.interruption_threshold}%")
    
    if not instances_to_blacklist:
        logger.info("No instances to blacklist. Exiting.")
        return
    
    # Confirm before proceeding if not in dry-run mode
    if not args.dry_run:
        confirm = input(f"\nAre you sure you want to blacklist these {len(instances_to_blacklist)} instances? (y/n): ")
        if confirm.lower() != 'y':
            logger.info("Operation cancelled by user.")
            return
    
    # Blacklist instances
    successful = 0
    failed = 0
    blacklisted_instances = []  # Store successfully blacklisted instances
    
    for i, instance_type in enumerate(instances_to_blacklist):
        if i > 0 and i % args.batch_size == 0:
            logger.info(f"Pausing for {args.batch_pause} seconds after processing {args.batch_size} instances...")
            time.sleep(args.batch_pause)
        
        try:
            if args.dry_run:
                logger.info(f"DRY RUN: Would blacklist {instance_type}")
                successful += 1
            else:
                result = blacklist_instance(
                    instance_type=instance_type,
                    api_key=args.api_key,
                    org_id=args.org_id,
                    cluster_id=args.cluster_id,
                    blacklist_hours=args.blacklist_hours
                )
                if result:
                    blacklisted_instances.append(instance_type)  # Store successful instance
                    successful += 1
                else:
                    failed += 1
        except Exception as e:
            logger.error(f"Error blacklisting {instance_type}: {e}")
            failed += 1
    
    # Print summary
    logger.info("\nBlacklisting complete:")
    logger.info(f"Total instances processed: {len(instances_to_blacklist)}")
    logger.info(f"Successfully blacklisted: {successful}")
    
    if failed > 0:
        logger.info(f"Failed to blacklist: {failed}")
    
    # Print blacklisted instances
    if blacklisted_instances:
        logger.info("\nBlacklisted instance families:")
        logger.info(", ".join(blacklisted_instances))
    
    logger.info("Done.")
if __name__ == "__main__":
    main()


#--region ap-northeast-1 --os Linux --interruption-threshold 15 --api-key API_KEY --org-id org_id --cluster-id cluster_id  --dry-run