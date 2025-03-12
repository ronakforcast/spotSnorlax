#!/usr/bin/env python3
"""
CAST.ai Node Template Spot Priority Updater

This script fetches AWS Spot Advisor data and updates CAST.ai node templates to prioritize
instance families with lower interruption rates.

Usage:
  python cast_node_template_updater.py --region REGION --os OS --api-key API_KEY --cluster-id CLUSTER_ID
                                      [--dry-run] [--cache-dir CACHE_DIR]

Example:
  python cast_node_template_updater.py --region us-east-1 --os Linux 
                                      --api-key YOUR_API_KEY --cluster-id YOUR_CLUSTER_ID
"""

import argparse
import json
import os
import tempfile
import requests
import logging
import re
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"cast_template_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("cast_template_update")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="CAST.ai Node Template Spot Priority Updater")
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
        "--api-key",
        required=True,
        help="CAST.ai API key"
    )
    parser.add_argument(
        "--cluster-id",
        required=True,
        help="CAST.ai cluster ID"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without applying them"
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
    # First check if this is already just a family name with no size
    if '.' not in instance_type:
        return instance_type
        
    # Extract the family part (everything before the dot)
    # Examples:
    # c5.large -> c5
    # m5a.2xlarge -> m5a
    # t2.micro -> t2
    # c7i-flex.2xlarge -> c7i-flex
    parts = instance_type.split('.')
    return parts[0]


def create_interruption_buckets(spot_data, region, os_type):
    """
    Create buckets based on interruption frequency ranges.
    Just two buckets: very_high (>20%) and standard (all others)
    """
    if region not in spot_data["spot_advisor"]:
        logger.error(f"Region {region} not found in Spot Advisor data")
        return {}
    
    if os_type not in spot_data["spot_advisor"][region]:
        logger.error(f"OS {os_type} not found for region {region}")
        return {}
    
    # Get interruption ranges for reference
    interruption_ranges = {int(r["index"]): r for r in spot_data["ranges"]}
    
    # Define buckets: standard (≤20%) and very_high (>20%)
    buckets = {
        "standard": [],    # ≤20%
        "very_high": []    # >20%
    }
    
    # Process each instance type
    for instance_type, values in spot_data["spot_advisor"][region][os_type].items():
        interruption_index = int(values["r"])
        interruption_info = interruption_ranges.get(interruption_index, {})
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
    Priority order: very_high > standard
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


def get_cast_node_templates(api_key, cluster_id):
    """
    Get all node templates from CAST.ai API
    """
    url = f"https://api.cast.ai/v1/kubernetes/clusters/{cluster_id}/node-templates?includeDefault=true"
    headers = {
        "X-API-Key": api_key,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get CAST.ai node templates: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return None


def update_cast_node_template(api_key, cluster_id, template_name, template_data, family_buckets, dry_run=False):
    """
    Update a CAST.ai node template with new customPriority settings based on included instance families
    """
    url = f"https://api.cast.ai/v1/kubernetes/clusters/{cluster_id}/node-templates/{template_name}"
    headers = {
        "X-API-Key": api_key,
        "accept": "application/json",
        "content-type": "application/json"
    }
    
    # Create a deep copy of the template to avoid modifying the original
    template = json.loads(json.dumps(template_data))
    constraints = template.get("constraints", {})
    
    # Check if instanceFamilies.include exists and has entries
    instance_families_include = constraints.get("instanceFamilies", {}).get("include", [])
    
    if instance_families_include:
        logger.info(f"Template '{template_name}' has {len(instance_families_include)} included instance families")
        
        # Separate included families into standard and very_high buckets
        included_standard_families = []
        included_very_high_families = []
        
        standard_families = set(family_buckets["standard"])
        very_high_families = set(family_buckets["very_high"])
        
        for family in instance_families_include:
            if family in standard_families:
                included_standard_families.append(family)
            elif family in very_high_families:
                included_very_high_families.append(family)
            else:
                # If we don't have interruption data, assume it's standard
                included_standard_families.append(family)
                logger.warning(f"Family '{family}' not found in either bucket, treating as standard")
        
        # Create customPriority array for the template
        custom_priority = []
        
        # Add standard families first (lower interruption rate)
        if included_standard_families:
            custom_priority.append({
                "families": included_standard_families,
                "spot": True
            })
        
        # Add very_high families last (if any exist)
        if included_very_high_families:
            custom_priority.append({
                "families": included_very_high_families,
                "spot": True
            })
    else:
        logger.info(f"Template '{template_name}' has no instance family restrictions, using all families from buckets")
        
        # Create customPriority array for the template
        custom_priority = []
        
        # Add standard families first (lower interruption rate)
        if family_buckets["standard"]:
            custom_priority.append({
                "families": family_buckets["standard"],
                "spot": True
            })
        
        # Add very_high families last (if any exist)
        if family_buckets["very_high"]:
            custom_priority.append({
                "families": family_buckets["very_high"],
                "spot": True
            })
    
    # Update the customPriority field in the constraints
    template["constraints"]["customPriority"] = custom_priority
    
    if dry_run:
        logger.info(f"DRY RUN: Would update template '{template_name}' with customPriority:")
        logger.info(json.dumps(custom_priority, indent=2))
        return True
    
    try:
        response = requests.put(url, headers=headers, json=template)
        response.raise_for_status()
        logger.info(f"Successfully updated template '{template_name}'")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update template '{template_name}': {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response status: {e.response.status_code}")
            logger.error(f"Response body: {e.response.text}")
        return False


def main():
    """Main function."""
    args = parse_args()
    
    logger.info(f"Starting CAST.ai Node Template Spot Priority Updater")
    logger.info(f"Region: {args.region}, OS: {args.os}, Cluster ID: {args.cluster_id}")
    
    if args.dry_run:
        logger.info("DRY RUN MODE: No templates will actually be updated")
    
    # Fetch Spot Advisor data
    logger.info("Fetching AWS Spot Advisor data...")
    try:
        spot_data = fetch_spot_advisor_data(args.cache_dir)
    except Exception as e:
        logger.error(f"Failed to fetch Spot Advisor data: {e}")
        sys.exit(1)
    
    # Create instance type buckets
    logger.info("Creating instance type buckets...")
    instance_buckets = create_interruption_buckets(spot_data, args.region, args.os)
    
    if not instance_buckets:
        logger.error("Failed to create instance buckets. Exiting.")
        sys.exit(1)
    
    # Convert to instance families
    logger.info("Converting to instance families...")
    family_buckets = convert_to_instance_families(instance_buckets)
    
    # Remove duplicates from lower buckets
    logger.info("Removing duplicates from lower priority buckets...")
    final_family_buckets = remove_duplicates_from_lower_buckets(family_buckets)
    
    # Convert sets to sorted lists for output
    sorted_family_buckets = {
        bucket: sorted(list(families))
        for bucket, families in final_family_buckets.items()
    }
    
    # Print bucket statistics
    logger.info("Instance family bucket statistics:")
    for bucket, families in sorted_family_buckets.items():
        logger.info(f"  {bucket.upper()}: {len(families)} instance families")
        logger.info(f"  {', '.join(families)}")
    
    # Get CAST.ai node templates
    logger.info("Fetching CAST.ai node templates...")
    templates_response = get_cast_node_templates(args.api_key, args.cluster_id)
    
    if not templates_response:
        logger.error("Failed to fetch node templates. Exiting.")
        sys.exit(1)
    
    templates = templates_response.get("items", [])
    logger.info(f"Found {len(templates)} node templates")
    
    # Track success/failure counts
    updated = 0
    skipped = 0
    failed = 0
    
    # Update each template that has spot: true
    for template_item in templates:
        template = template_item.get("template", {})
        template_name = template.get("name", "")
        constraints = template.get("constraints", {})
        
        # Check if this template uses spot instances
        if constraints.get("spot", False):
            logger.info(f"Processing template '{template_name}' which uses spot instances")
            
            # Update the template with the full template object
            result = update_cast_node_template(
                api_key=args.api_key,
                cluster_id=args.cluster_id,
                template_name=template_name,
                template_data=template,
                family_buckets=sorted_family_buckets,
                dry_run=args.dry_run
            )
            
            if result:
                updated += 1
            else:
                failed += 1
        else:
            logger.info(f"Skipping template '{template_name}' which doesn't use spot instances")
            skipped += 1
    
    # Print summary
    logger.info("\nUpdate summary:")
    logger.info(f"Total templates: {len(templates)}")
    logger.info(f"Updated: {updated}")
    logger.info(f"Skipped (no spot): {skipped}")
    logger.info(f"Failed: {failed}")
    
    # Save results to a file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"spot_family_buckets_{args.region}_{args.os}_{timestamp}.json"
    with open(output_file, 'w') as f:
        json.dump({
            "region": args.region,
            "os": args.os,
            "buckets": sorted_family_buckets
        }, f, indent=2)
    
    logger.info(f"Results saved to {output_file}")
    logger.info("Done.")


if __name__ == "__main__":
    main()