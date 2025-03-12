#!/usr/bin/env python3
"""
AWS Spot Instance Family Bucket Script

This script fetches AWS Spot Advisor data and organizes instance families into buckets
based on their interruption frequency. If an instance family appears in multiple buckets,
it is removed from the lower interruption buckets.

Usage:
  python spot_bucket.py --region REGION --os OS [--cache-dir CACHE_DIR]

Example:
  python spot_bucket.py --region us-east-1 --os Linux
"""

import argparse
import json
import os
import tempfile
import requests
import logging
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("spot_bucket.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("spot_bucket")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="AWS Spot Instance Family Bucket Script")
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
        "--cache-dir",
        default=None,
        help="Directory to cache the spot advisor data (default: system temp dir)"
    )
    return parser.parse_args()


def fetch_spot_advisor_data(cache_dir=None):
    """Fetch the AWS Spot Advisor data from the official source."""
    url = "https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json"
    
    # Create a cache directory if it doesn't exist
    if cache_dir is None:
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


def get_instance_family(instance_type):
    """Extract the instance family from the instance type."""
    # Use regex to extract the family
    # Examples:
    # c5.large -> c5
    # m5a.2xlarge -> m5a
    # t2.micro -> t2
    match = re.match(r'^([a-z]+\d+[a-z]*)\.', instance_type)
    return match.group(1) if match else instance_type


def create_interruption_buckets(spot_data, region, os_type):
    """
    Create buckets based on interruption frequency ranges.
    """
    if region not in spot_data["spot_advisor"]:
        logger.error(f"Region {region} not found in Spot Advisor data")
        return {}
    
    if os_type not in spot_data["spot_advisor"][region]:
        logger.error(f"OS {os_type} not found for region {region}")
        return {}
    
    # Get interruption ranges for reference
    interruption_ranges = {int(r["index"]): r for r in spot_data["ranges"]}
    
    # Define just two buckets: very_high (>20%) and standard (all others)
    buckets = {
        "standard": [],    # ≤20%
        "very_high": []    # >20%
    }
    
    # Process each instance type
    for instance_type, values in spot_data["spot_advisor"][region][os_type].items():
        interruption_index = int(values["r"])
        interruption_info = interruption_ranges.get(interruption_index, {})
        interruption_min = float(interruption_info.get("min", 0))
        interruption_max = float(interruption_info.get("max", 0))
        
        # Assign to appropriate bucket based on max interruption rate
        if interruption_max > 20:
            buckets["very_high"].append(instance_type)
        else:
            buckets["standard"].append(instance_type)
    
    return buckets


def convert_to_instance_families(buckets):
    """
    Convert instance type buckets to instance family buckets.
    """
    family_buckets = {
        "standard": set(),
        "very_high": set()
    }
    
    # Convert each instance type to its family
    for bucket_name, instances in buckets.items():
        for instance_type in instances:
            family = get_instance_family(instance_type)
            family_buckets[bucket_name].add(family)
    
    return family_buckets


def remove_duplicates_from_lower_buckets(family_buckets):
    """
    Remove instance families from lower interruption buckets if they appear in higher ones.
    Priority order: high > medium > low > very_low
    """
    # The buckets in order from highest to lowest priority
    bucket_priority = ["very_high", "standard"]
    
    # For each higher priority bucket
    for i, higher_bucket in enumerate(bucket_priority[:-1]):
        # Get families in this bucket
        families_in_higher = family_buckets[higher_bucket]
        
        # Remove these families from all lower priority buckets
        for lower_bucket in bucket_priority[i+1:]:
            family_buckets[lower_bucket] -= families_in_higher
    
    return family_buckets


def main():
    """Main function to fetch data and create instance family buckets."""
    args = parse_args()
    
    logger.info(f"Starting spot instance family bucket script")
    logger.info(f"Region: {args.region}, OS: {args.os}")
    
    # Fetch Spot Advisor data
    logger.info("Fetching AWS Spot Advisor data...")
    try:
        data = fetch_spot_advisor_data(args.cache_dir)
    except Exception as e:
        logger.error(f"Failed to fetch Spot Advisor data: {e}")
        return
    
    # Create buckets based on interruption frequency
    logger.info("Creating instance type buckets based on interruption frequency...")
    instance_buckets = create_interruption_buckets(data, args.region, args.os)
    
    if not instance_buckets:
        logger.error("Failed to create instance buckets. Exiting.")
        return
    
    # Print instance bucket statistics
    logger.info("Instance type bucket statistics:")
    for bucket, instances in instance_buckets.items():
        logger.info(f"  {bucket.upper()}: {len(instances)} instance types")
    
    # Convert to instance families
    logger.info("Converting to instance families...")
    family_buckets = convert_to_instance_families(instance_buckets)
    
    # Remove duplicates from lower buckets
    logger.info("Removing duplicates from lower priority buckets...")
    final_family_buckets = remove_duplicates_from_lower_buckets(family_buckets)
    
    # Print final results
    logger.info("\nFinal instance family buckets:")
    logger.info("-" * 60)
    
    for bucket, families in final_family_buckets.items():
        logger.info(f"{bucket.upper()} interruption families ({len(families)}):")
        if families:
            families_list = sorted(list(families))
            logger.info(f"  {', '.join(families_list)}")
        else:
            logger.info("  No families in this bucket")
        logger.info("-" * 60)
    
    # Save results to a JSON file
    output_file = f"spot_family_buckets_{args.region}_{args.os}.json"
    with open(output_file, 'w') as f:
        json.dump({k: list(v) for k, v in final_family_buckets.items()}, f, indent=2)
    
    logger.info(f"Results saved to {output_file}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
    # c5,c5a,c5d,c5n,c6a,c6i,c6id,c6in,c7a,c7i,c7i-flex,m5,m5a,m5ad,m5d,m5dn,m5n,m5zn,m6a,m6i,m6id,m6idn,m6in,m7a,m7i,m7i-flex,r5,r5a,r5ad,r5b,r5dn,r5d,r5n,r6a,r6i,r6id,r6idn,r6in,r7a,r7i,r7iz

